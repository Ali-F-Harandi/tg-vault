# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Local Bot API Server support (`api_url` per bot)
- HTTP Range streaming for video files
- Docker image + REST API wrapper
- TUI (Textual / Rich) for the interactive menu
- Topic-group support (`message_thread_id`)
- Sync engine (Dropbox-like folder sync, inspired by TAS)
- FUSE mount (mount Telegram storage as a local folder, inspired by TAS)

## [v8.3.0] — 2026-07-12

### Added
- **Download Manager** (`download_manager.py`) — IDM-style download management:
  - Multiple concurrent downloads with pause/resume/cancel
  - Downloads persist across GUI restarts (`downloads.json`)
  - Concurrency control via semaphore (limits API calls to bot count)
  - Temp folder (`.temp/`) for partial downloads
  - Real-time progress, speed, and ETA display
  - Multiselect (Ctrl+click, Shift+click) for batch operations
  - Right-click context menu (pause/resume/cancel/remove/open folder)
- **Multi-channel support** — upload to multiple storage channels:
  - `channels.storage` config field for additional channels
  - `channels add/remove/show` CLI commands
  - `--channel` and `--all-channels` upload flags
  - Orphan scan across ALL storage channels
- **`db verify`** — fix share_link / manifest_msg_id mismatches
  - Tries both manifest_msg_id and share_link to find the correct manifest
  - Updates message_ids, manifest_msg_id, description_msg_id, share_link
- **`db find-missing`** — check if files in DB still exist in channel
- **`db clear-temp`** — delete all temp channel messages except DB backup
- **`db edit`** — edit description/tags of uploaded files:
  - Single file: `db edit <ID> --desc "..." --tag ...`
  - Bulk edit: `db edit --ids 1,2,3 --desc "..." --tag ...`
  - Add tags: `db edit --ids 1,2,3 --add-tag backup`
  - Remove tags: `db edit --ids 1,2,3 --remove-tag old`
- **Manifest type selection**:
  - `default_manifest_type` config field (text/file/auto, default: text)
  - `--manifest-type text|file|auto` CLI flag
  - Compact JSON (separators=(',',':')) — ~60% smaller manifests
- **Orphan scanner improvements**:
  - Detects ALL message types (text, photo, video, sticker, audio, voice, etc.)
  - `message_type` and `file_size` columns in orphans table
  - `--max-scan`, `--batch-size`, `--delay` flags for controlled scanning
  - Safety: share_link message_ids added to known set
- **GUI improvements**:
  - Right-click copy/paste context menu for all Entry and Text widgets
  - Browse tab: Tags + Description columns, inline edit panel with bulk support
  - Configuration tab: full config editor (bots, channels, advanced, DB, proxy)
  - Storage channels management (add/remove/list)
  - Status bar fixed at bottom of window
  - Scrollable Configuration tab

### Fixed
- **CRITICAL**: `update_share_link` now updates ALL message fields (message_ids,
  manifest_msg_id, description_msg_id) — not just share_link. Previously,
  re-uploaded files only had share_link updated, causing the orphan scanner
  to delete the new upload's messages.
- **CRITICAL**: `db sync` now properly deletes old DB backup messages
  (forwardMessage doesn't return caption for channel messages — fixed by
  checking filename AND caption, plus direct deletion of known db_sync_msg_id)
- `db restore` on Windows: `os.replace()` instead of `os.rename()` (FileExistsError)
- `cmd_test` crash when no bots available (NoneType has no attribute 'request')
- Download tree selection lost on refresh (now preserves ALL selected items)
- Download tree crash when removing cancelled downloads
- `ls` command printing entire JSON body instead of just hash prefix
- GUI path resolution for `tg.py` and `config.json` after reorganization
- Pack/grid geometry manager conflict in Configuration tab

## [v8.1.0] — 2026-07-11

### Changed — Project reorganization
- **Restructured into a proper Python package** (`tg_vault/`):
  - `tg.py` (3580 lines) split into 14 focused modules
  - All logic now lives in `tg_vault/` (cli, commands, interactive, config,
    bot_pool, uploader, downloader, crypto, compression, chunk_header, db,
    db_sync, constants, utils)
  - GUI moved to `gui/app.py` (was root-level `gui.py`)
  - Added `pyproject.toml` with proper setuptools metadata + `tg-vault` script entry point
  - Added `tests/test_smoke.py` with 17 smoke tests
  - Root `tg.py` and `gui.py` kept as thin backward-compat shims so
    `python tg.py <cmd>` and `python gui.py` keep working

### Added — Documentation
- `docs/USAGE.md` — comprehensive usage guide with all commands and flags
- `docs/CONFIGURATION.md` — full config file reference with examples
- `docs/SECURITY.md` — encryption design, threat model, best practices
- Updated `docs/ARCHITECTURE.md` to reflect the new package layout

### Fixed
- `ls` command: only parse the first line of the manifest text when extracting
  the header (previously printed the entire JSON body because `hash_prefix`
  contained the rest of the manifest after the first `|`-split)
- `db_search.py` example: updated to import `Database` from the new
  `tg_vault.db` module (was `tg_db`)
- All examples that build subprocess commands: `--config` is now placed
  *before* the subcommand (it's a global argparse flag)

### Notes
- All 17 smoke tests pass
- Real-world upload/download tested with a 13 MB single-part file
  (BERSERK Ch500 PDF) and a 33 MB multi-part file (Fairy Tail Vol 5 CBZ)
- Encrypted upload + download tested and verified (SHA256 round-trip OK)
- DB list/search/stats/query/download tested

## [v8.0.0] — 2026-07-11

Inspired by studying [TAS (Telegram as Storage)](https://github.com/ixchio/tas) — adopted the best ideas while keeping our 19 MB chunk size (which actually works for downloads, unlike TAS's 49 MB).

### Added
- **AES-256-GCM encryption** (optional, `--encrypt` flag):
  - PBKDF2-HMAC-SHA512 with 600,000 iterations (OWASP 2025 recommendation)
  - 32-byte random salt stored in manifest
  - 12-byte IV per chunk (deterministic, derived from chunk index — avoids storing per-chunk IVs)
  - 128-bit auth tag (built into AESGCM output)
  - Separate password verification hash (timing-safe comparison) for fail-fast on wrong passwords
  - Key NEVER stored — only the user knows it
  - Password can be provided via `--password`, `TG_VAULT_PASSWORD` env var, or interactive prompt
- **Smart gzip compression** (on by default, `--no-compress` to disable):
  - Automatically skips already-compressed formats (jpg, mp4, zip, pdf, docx, etc.)
  - Only uses compression if it actually reduces size
  - Compression flag stored in manifest and chunk header
- **Self-describing chunk headers (TGV1)**:
  - 40-byte binary header at the start of each chunk
  - Contains: magic bytes, version, flags, chunk index, total chunks, original size, SHA256 prefix
  - Lets you identify a chunk without consulting the database
- **Improved progress bar** (inspired by TAS):
  - Speed sampled every 200ms (instantaneous) instead of cumulative average
  - More responsive ETA calculation
  - Falls back to cumulative average if instantaneous speed is zero
- **New modules**:
  - `tg_crypto.py` — AES-256-GCM encryptor with PBKDF2 key derivation
  - `tg_compression.py` — smart gzip with format-aware bypass
  - `tg_chunk_header.py` — TGV1 binary header packer/parser
- **Enhanced database schema**:
  - New `chunks` table for per-chunk metadata (file_id, chunk_index, message_id, size)
  - New `tags` table for proper many-to-many tag organization
  - New fields on `files`: `encrypted`, `compressed`, `has_chunk_header`, `encryption_algorithm`, `encryption_kdf`, `encryption_salt`, `tags`
  - Search now queries across name, description, hashtags, AND tags tables
- **Global signal handlers** (inspired by TAS):
  - SIGINT (Ctrl+C) → exit code 130
  - SIGTERM → exit code 143
- **New CLI flags**:
  - `upload --encrypt` / `upload -e`
  - `upload --password <pw>` / `upload --no-compress`
  - `download --password <pw>`

### Changed
- `Uploader.upload()` accepts `encrypt`, `password`, `compress` parameters
- `Downloader.download()` accepts `password` parameter
- `_send_manifest()` stores v8 metadata (encrypted, compressed, has_chunk_header, encryption_*)
- `Uploader` now applies pipeline: raw → compress → encrypt → prepend header
- `Downloader` reverses pipeline: strip header → decrypt → decompress → write
- `ProgressTracker` uses TAS-style instantaneous speed sampling (200ms intervals)
- `tg_db.py` `insert_file()` now also inserts per-chunk records and tag records
- `tg_db.py` `search_files()` now searches across `tags` table too

### Security
- Encryption key is NEVER stored anywhere — only the user's password
- PBKDF2 with 600k iterations makes brute-force attacks expensive (~1 second per attempt)
- AES-GCM auth tag detects any tampering with encrypted chunks
- Password verification uses `hmac.compare_digest()` (constant-time) to prevent side-channel attacks
- Salt is unique per file (random 32 bytes) — same password produces different keys for different files

### Backward Compatibility
- v7 manifests (without `encrypted`/`compressed` fields) still download correctly
- v7 config files work as-is (`db_enabled` defaults to `false` if not set)
- v7 database schemas auto-upgrade (new columns added with defaults)
- Old downloads without TGV1 header still work (header detection is opt-in)

## [v7.0.0] — 2026-07-11

### Added
- **Bulk upload**: `python tg.py upload file1 file2 file3 --desc ... --tag ...`
  - Multiple file paths as positional arguments (shell wildcards supported)
  - Sequential upload with summary at the end
- **Bulk download**: `python tg.py download link1 link2 link3`
  - Multiple manifest links as positional arguments
  - `--links-file` flag to read links from a text file (one per line, # comments supported)
  - `--output-dir` to specify a destination directory
- **SQLite database** (`tg_db.py`):
  - Optional metadata storage for every uploaded file
  - Schema includes: name, size, SHA256, parts, message IDs, description, hashtags,
    channels, share link, session ID, timestamps, status
  - Separate `downloads` table for download history
  - Automatic logging on every upload/download
  - `db` command with subcommands: `enable`, `disable`, `info`, `list`, `search`, `stats`, `export`
  - Database path stored in config (priority: explicit `db_path` → next to config file)
  - Setup wizard asks whether to enable database
- **Hashtag sanitization** (carried over from v6.x):
  - Replaces invalid chars with underscore (`sci-fi` → `sci_fi`)
  - Prepends underscore if starts with digit (`2026` → `_2026`)
  - Deduplicates case-insensitively
- **Setup wizard** with database step: now 4 steps (bot → main channel → temp channel → database)
- **Interactive menu** updated with bulk support and database submenu
- **New examples**:
  - `examples/bulk_upload.py` — wrapper for bulk upload command
  - `examples/bulk_download.py` — wrapper for bulk download with `--links-file` support
  - `examples/db_search.py` — search the database from a script (with `--json` output)

### Changed
- `upload` command now takes `files` (nargs="+") instead of `file` (single)
- `download` command now takes `links` (nargs="+") instead of `link` (single)
- `_send_manifest()` now returns `(share_link, manifest_dict)` tuple so Uploader can log full metadata to DB
- `Uploader.upload()` and `Downloader.download()` accept an optional `db` parameter
- `Config` class has new `db_enabled` and `db_path` fields plus `get_db()` / `get_db_path()` helpers

### Removed
- **Web app** (`docs/index.html`) — removed per user request; the CLI is the primary interface
- **Cloudflare Worker** (`docs/cloudflare-worker.js`) — no longer needed without the web app
- **CORS proxy documentation** in README — N/A without the web app
- **`.nojekyll`, `_config.yml`** in `docs/` — were only for the web app
- **`.github/workflows/pages.yml`** — no longer needed without the web app
- Root-level `index.html` (duplicate of `docs/index.html`)

### Migration from v6
- Existing config files (~/.tg-vault.json) still work — `db_enabled` defaults to `false`
- To enable the database: `python tg.py db enable`
- Old `upload <file>` and `download <link>` commands still work (single-arg is a subset of nargs="+")
- Old `--output` flag for download still works for single-file downloads

## [v6.0.0] — 2026-07-10

### Added
- **Multi-bot support** with thread-safe round-robin `BotPool`
- **Parallel chunk download** via `ThreadPoolExecutor`
- **Per-bot rate limiting** (50 ms min interval, FloodWait-safe)
- **Connection pooling** (`requests.Session` per bot)
- **Description message** before parts (name + size + SHA256 + custom text + hashtags)
- **Manifest message** after parts (reply to last part, JSON metadata)
- **Resume** for both upload (`.resume.json`) and download (`.downloading` truncation)
- **Filename sanitization** (removes illegal chars, truncates to 60 chars)
- **Caption/text length validation** (Telegram limits: 1024/4096 chars)
- **Progress bar** with speed and ETA
- **`--output` and `--output-dir` flags** for download
- **`ls` command** to list recent manifest files in main channel
- **`delete` command** to delete a file's messages (description + parts + manifest)
- **Graceful `Ctrl+C` cleanup** — temp messages are deleted on interrupt
- **Concurrency-safe sessions** — each session gets a UUID tag in every caption
- **Config file** at `~/.tg-vault.json` for bots, channels, defaults
- **Interactive menu** with 11 options
- **Bilingual README** (English + Persian)
- **Examples** directory with `parallel_uploads.py`, `backup_directory.py`, `download_all.py`
- **Architecture docs** explaining key design decisions

### Fixed
- Switched from `copyMessage` to `forwardMessage` because `copyMessage` does NOT return caption for channel messages (Telegram quirk)
- Handle FloodWait (429) by waiting `retry_after` seconds, then retrying
- Handle 5xx server errors with exponential backoff

### Security
- Config file with tokens is gitignored
- Tokens are never logged or printed in full

## [v5.0.0] — 2026-07-10 (internal, not tagged)

Initial architecture with multi-bot, config, temp channel, description/end messages, resume.

## [v4.0.0] — 2026-07-10 (internal, not tagged)

Added reply-chain upload + manifest message + link-based download.

## [v3.0.0] — 2026-07-10 (internal, not tagged)

Added link parsing, manifest message, single-file download.

## [v2.0.0] — 2026-07-10 (internal, not tagged)

Added SHA256, JSON metadata, FloodWait handling, resume, progress bar.

## [v1.0.0] — 2026-07-10 (internal, not tagged)

Original script by user. 15 MB chunks, fragile `first_message_id` calculation, no checksum.
