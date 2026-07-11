"""
SQLite database module for tg-vault.

Stores metadata for every uploaded file:
  - file name, size, SHA256
  - total parts, chunk size
  - message IDs (parts + manifest)
  - description, hashtags
  - channel IDs (main + temp)
  - timestamps (uploaded, last accessed)
  - share link
  - session ID

The database path is stored in the JSON config file. If database is
enabled, every upload/download automatically updates the DB.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    size            INTEGER NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,
    total_parts     INTEGER NOT NULL,
    chunk_size      INTEGER NOT NULL,
    message_ids     TEXT NOT NULL,           -- JSON array of ints
    manifest_msg_id INTEGER,
    description_msg_id INTEGER,
    description     TEXT,
    hashtags        TEXT,                    -- JSON array of strings
    main_channel    TEXT,
    temp_channel    TEXT,
    share_link      TEXT,
    session_id      TEXT,
    uploaded_at     INTEGER NOT NULL,
    last_accessed_at INTEGER,
    status          TEXT DEFAULT 'uploaded',  -- uploaded | deleted | corrupted
    -- v8 fields
    encrypted       INTEGER DEFAULT 0,
    compressed      INTEGER DEFAULT 0,
    has_chunk_header INTEGER DEFAULT 0,
    encryption_algorithm TEXT,
    encryption_kdf  TEXT,
    encryption_salt TEXT,
    original_size   INTEGER,                -- size before compression (may differ from `size`)
    tags            TEXT                    -- comma-separated for quick LIKE search
);

CREATE TABLE IF NOT EXISTS downloads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL,
    output_path     TEXT,
    sha256_verified INTEGER NOT NULL,        -- 0=false, 1=true
    downloaded_at   INTEGER NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id)
);

-- v8: per-chunk metadata (mirror of message_ids but queryable)
CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL,
    chunk_index     INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    size            INTEGER,                -- chunk size in bytes (after processing)
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(file_id, chunk_index)
);

-- v8: tag-based organization (many-to-many)
CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL,
    tag             TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(file_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_files_sha256    ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_name      ON files(name);
CREATE INDEX IF NOT EXISTS idx_files_uploaded  ON files(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_files_status    ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_encrypted ON files(encrypted);
CREATE INDEX IF NOT EXISTS idx_downloads_file  ON downloads(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file     ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_msg      ON chunks(message_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag        ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_tags_file       ON tags(file_id);
"""


@contextmanager
def get_conn(db_path):
    """Context manager for SQLite connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class Database:
    """SQLite-backed metadata store."""

    def __init__(self, db_path):
        self.path = db_path
        self._init_schema()

    def _init_schema(self):
        with get_conn(self.path) as conn:
            conn.executescript(SCHEMA)

    # ─────────────── Files ───────────────

    def insert_file(self, manifest, share_link, temp_channel=None):
        """Insert a new file record from manifest dict. Returns the row id.

        Also inserts per-chunk records and tags (v8).
        """
        hashtags = manifest.get("hashtags", []) or []
        message_ids = manifest.get("message_ids", []) or []
        now = int(time.time())

        with get_conn(self.path) as conn:
            # Insert file record
            cur = conn.execute(
                """INSERT INTO files
                   (name, size, sha256, total_parts, chunk_size,
                    message_ids, manifest_msg_id, description_msg_id,
                    description, hashtags, main_channel, temp_channel,
                    share_link, session_id, uploaded_at,
                    encrypted, compressed, has_chunk_header,
                    encryption_algorithm, encryption_kdf, encryption_salt,
                    tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    manifest["name"],
                    manifest["size"],
                    manifest["sha256"],
                    manifest["total_parts"],
                    manifest.get("chunk_size", 0),
                    json.dumps(message_ids),
                    manifest.get("manifest_message_id"),
                    manifest.get("description_msg_id"),
                    manifest.get("description", ""),
                    json.dumps(hashtags),
                    str(manifest.get("channel_id", "")),
                    str(temp_channel or ""),
                    share_link,
                    manifest.get("session_id", ""),
                    now,
                    1 if manifest.get("encrypted") else 0,
                    1 if manifest.get("compressed") else 0,
                    1 if manifest.get("has_chunk_header") else 0,
                    manifest.get("encryption_algorithm"),
                    manifest.get("encryption_kdf"),
                    manifest.get("encryption_salt"),
                    ",".join(hashtags),  # quick LIKE search
                ),
            )
            file_id = cur.lastrowid

            # Insert per-chunk records (v8)
            for idx, msg_id in enumerate(message_ids):
                conn.execute(
                    """INSERT OR IGNORE INTO chunks
                       (file_id, chunk_index, message_id, created_at)
                       VALUES (?,?,?,?)""",
                    (file_id, idx, msg_id, now),
                )

            # Insert tags (v8)
            for tag in hashtags:
                conn.execute(
                    """INSERT OR IGNORE INTO tags
                       (file_id, tag, created_at)
                       VALUES (?,?,?)""",
                    (file_id, tag, now),
                )

            return file_id

    def update_share_link(self, file_id, share_link):
        with get_conn(self.path) as conn:
            conn.execute(
                "UPDATE files SET share_link=? WHERE id=?",
                (share_link, file_id),
            )

    def get_file_by_sha(self, sha256):
        with get_conn(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE sha256=?", (sha256,)
            ).fetchone()
            return dict(row) if row else None

    def get_file_by_id(self, file_id):
        with get_conn(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE id=?", (file_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_file_by_link(self, share_link):
        with get_conn(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE share_link=?", (share_link,)
            ).fetchone()
            return dict(row) if row else None

    def list_files(self, limit=50, offset=0, status=None):
        with get_conn(self.path) as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM files WHERE status=? ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM files ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def search_files(self, query):
        """Search by name, description, hashtags, or tags (LIKE)."""
        pattern = f"%{query}%"
        with get_conn(self.path) as conn:
            rows = conn.execute(
                """SELECT DISTINCT f.* FROM files f
                   LEFT JOIN tags t ON t.file_id = f.id
                   WHERE f.name LIKE ? OR f.description LIKE ? OR f.hashtags LIKE ?
                      OR f.tags LIKE ? OR t.tag LIKE ?
                   ORDER BY f.uploaded_at DESC""",
                (pattern, pattern, pattern, pattern, pattern),
            ).fetchall()
            return [dict(r) for r in rows]

    def query_files(self, filters=None):
        """Query files with flexible filters.

        filters dict (all optional):
            name:         str  — LIKE pattern for filename
            description:  str  — LIKE pattern for description
            tag:          str  — exact tag match (via tags table)
            min_size:     int  — minimum file size in bytes
            max_size:     int  — maximum file size in bytes
            min_parts:    int  — minimum number of parts
            max_parts:    int  — maximum number of parts
            encrypted:    bool — filter by encryption status (None = any)
            compressed:   bool — filter by compression status (None = any)
            since:        int  — uploaded_at >= this unix timestamp
            until:        int  — uploaded_at <= this unix timestamp
            status:       str  — file status (default 'uploaded')
            sort:         str  — sort field: name, size, parts, date, downloads
            sort_dir:     str  — 'asc' or 'desc' (default 'desc')
            limit:        int  — max results (default 50)
            offset:       int  — pagination offset (default 0)
        """
        filters = filters or {}
        where_clauses = []
        params = []
        join_tags = False

        status = filters.get("status", "uploaded")
        if status:
            where_clauses.append("f.status = ?")
            params.append(status)

        if filters.get("name"):
            where_clauses.append("f.name LIKE ?")
            params.append(f"%{filters['name']}%")

        if filters.get("description"):
            where_clauses.append("f.description LIKE ?")
            params.append(f"%{filters['description']}%")

        if filters.get("tag"):
            where_clauses.append("t.tag = ?")
            params.append(filters["tag"])
            join_tags = True

        if filters.get("min_size") is not None:
            where_clauses.append("f.size >= ?")
            params.append(int(filters["min_size"]))

        if filters.get("max_size") is not None:
            where_clauses.append("f.size <= ?")
            params.append(int(filters["max_size"]))

        if filters.get("min_parts") is not None:
            where_clauses.append("f.total_parts >= ?")
            params.append(int(filters["min_parts"]))

        if filters.get("max_parts") is not None:
            where_clauses.append("f.total_parts <= ?")
            params.append(int(filters["max_parts"]))

        if filters.get("encrypted") is not None:
            where_clauses.append("f.encrypted = ?")
            params.append(1 if filters["encrypted"] else 0)

        if filters.get("compressed") is not None:
            where_clauses.append("f.compressed = ?")
            params.append(1 if filters["compressed"] else 0)

        if filters.get("since") is not None:
            where_clauses.append("f.uploaded_at >= ?")
            params.append(int(filters["since"]))

        if filters.get("until") is not None:
            where_clauses.append("f.uploaded_at <= ?")
            params.append(int(filters["until"]))

        # Sort
        sort_field = filters.get("sort", "date")
        sort_map = {
            "name": "f.name",
            "size": "f.size",
            "parts": "f.total_parts",
            "date": "f.uploaded_at",
            "downloads": "dl_count",
        }
        sort_col = sort_map.get(sort_field, "f.uploaded_at")
        sort_dir = "ASC" if filters.get("sort_dir", "desc").lower() == "asc" else "DESC"

        limit = int(filters.get("limit", 50))
        offset = int(filters.get("offset", 0))

        # Build query
        select = "SELECT DISTINCT f.*"
        if sort_field == "downloads":
            select += ", (SELECT COUNT(*) FROM downloads d WHERE d.file_id = f.id) AS dl_count"
        from_clause = "FROM files f"
        if join_tags or sort_field == "downloads":
            if join_tags:
                from_clause += " LEFT JOIN tags t ON t.file_id = f.id"

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        order_sql = f" ORDER BY {sort_col} {sort_dir}"
        limit_sql = f" LIMIT {limit} OFFSET {offset}"

        sql = f"{select} {from_clause}{where_sql}{order_sql}{limit_sql}"

        with get_conn(self.path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def count_files(self, filters=None):
        """Count files matching filters (same filters as query_files, minus sort/limit/offset)."""
        filters = filters or {}
        where_clauses = []
        params = []
        join_tags = False

        status = filters.get("status", "uploaded")
        if status:
            where_clauses.append("f.status = ?")
            params.append(status)

        for field, op in [("name", "LIKE"), ("description", "LIKE")]:
            val = filters.get(field)
            if val:
                where_clauses.append(f"f.{field} LIKE ?")
                params.append(f"%{val}%")

        if filters.get("tag"):
            where_clauses.append("t.tag = ?")
            params.append(filters["tag"])
            join_tags = True

        for field in ["min_size", "max_size", "min_parts", "max_parts", "since", "until"]:
            val = filters.get(field)
            if val is not None:
                col_map = {
                    "min_size": ("f.size", ">="),
                    "max_size": ("f.size", "<="),
                    "min_parts": ("f.total_parts", ">="),
                    "max_parts": ("f.total_parts", "<="),
                    "since": ("f.uploaded_at", ">="),
                    "until": ("f.uploaded_at", "<="),
                }
                col, op = col_map[field]
                where_clauses.append(f"{col} {op} ?")
                params.append(int(val))

        for field in ["encrypted", "compressed"]:
            val = filters.get(field)
            if val is not None:
                where_clauses.append(f"f.{field} = ?")
                params.append(1 if val else 0)

        from_clause = "FROM files f"
        if join_tags:
            from_clause += " LEFT JOIN tags t ON t.file_id = f.id"
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = f"SELECT COUNT(DISTINCT f.id) {from_clause}{where_sql}"

        with get_conn(self.path) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def get_files_by_ids(self, ids):
        """Get multiple file records by their IDs."""
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with get_conn(self.path) as conn:
            rows = conn.execute(
                f"SELECT * FROM files WHERE id IN ({placeholders}) ORDER BY id",
                ids
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_deleted(self, file_id):
        with get_conn(self.path) as conn:
            conn.execute(
                "UPDATE files SET status='deleted' WHERE id=?", (file_id,)
            )

    def touch(self, file_id):
        """Update last_accessed_at."""
        with get_conn(self.path) as conn:
            conn.execute(
                "UPDATE files SET last_accessed_at=? WHERE id=?",
                (int(time.time()), file_id),
            )

    # ─────────────── Downloads ───────────────

    def log_download(self, file_id, output_path, sha256_verified):
        with get_conn(self.path) as conn:
            conn.execute(
                """INSERT INTO downloads (file_id, output_path, sha256_verified, downloaded_at)
                   VALUES (?,?,?,?)""",
                (file_id, output_path, 1 if sha256_verified else 0, int(time.time())),
            )
            conn.execute(
                "UPDATE files SET last_accessed_at=? WHERE id=?",
                (int(time.time()), file_id),
            )

    # ─────────────── Stats ───────────────

    def stats(self):
        with get_conn(self.path) as conn:
            total_files = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='uploaded'"
            ).fetchone()[0]
            total_size = conn.execute(
                "SELECT COALESCE(SUM(size), 0) FROM files WHERE status='uploaded'"
            ).fetchone()[0]
            total_downloads = conn.execute(
                "SELECT COUNT(*) FROM downloads"
            ).fetchone()[0]
            # top 5 by downloads
            top = conn.execute(
                """SELECT f.name, f.size, COUNT(d.id) AS dl_count
                   FROM files f
                   JOIN downloads d ON d.file_id = f.id
                   GROUP BY f.id
                   ORDER BY dl_count DESC
                   LIMIT 5"""
            ).fetchall()
            return {
                "total_files": total_files,
                "total_size": total_size,
                "total_downloads": total_downloads,
                "top_files": [dict(r) for r in top],
            }

    # ─────────────── Export ───────────────

    def export_json(self, output_path):
        """Export all file records to JSON."""
        with get_conn(self.path) as conn:
            rows = conn.execute("SELECT * FROM files").fetchall()
            data = []
            for r in rows:
                d = dict(r)
                d["message_ids"] = json.loads(d["message_ids"]) if d["message_ids"] else []
                d["hashtags"] = json.loads(d["hashtags"]) if d["hashtags"] else []
                data.append(d)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return len(data)
