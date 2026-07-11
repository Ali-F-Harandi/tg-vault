#!/usr/bin/env python3
"""
tg-vault — Telegram Bot API cloud storage
==========================================
Use Telegram as a personal cloud storage backend using ONLY Bot API tokens.
No phone number, no api_id/api_hash, no MTProto/Telethon/Pyrogram required.

Philosophy
----------
Telegram's Bot API has an asymmetric size limit:
  • sendDocument: 50 MB upload
  • getFile:      20 MB download  ← the real bottleneck

tg-vault splits large files into ~19 MB chunks, uploads each chunk as a
document message to a channel where your bot is admin, and stores a final
"manifest" message containing the file's metadata (name, size, SHA256, and
the list of every chunk's message_id). To download, you only need the link
to the manifest message — tg-vault reads it, downloads each chunk, and
verifies the SHA256.

Features
--------
  ✓ Multi-bot support with round-robin rotation (multiply throughput)
  ✓ Parallel chunk download (uses all bots concurrently)
  ✓ Per-bot rate limiting (FloodWait-safe, ~50 ms min interval)
  ✓ Connection pooling (requests.Session per bot)
  ✓ Description message before parts (name + size + SHA256 + custom text + hashtags)
  ✓ Manifest message after parts (acts as "end" marker + reply to last part)
  ✓ Resume for both upload and download
  ✓ Filename & caption length validation/sanitization
  ✓ Graceful KeyboardInterrupt cleanup
  ✓ Config file (~/.tg-vault.json) for bots, channels, defaults
  ✓ CLI commands + interactive menu
  ✓ Concurrency-safe (each session has unique UUID tag)

Installation
------------
  pip install requests

Quick start
-----------
  python tg.py init
  python tg.py bots add 123456:ABC-DEF...
  python tg.py channels set main -1001234567890
  python tg.py channels set temp -1009876543210   # optional
  python tg.py test
  python tg.py upload movie.mp4 --desc "Backup" --tag movies,2026
  python tg.py download https://t.me/c/1234567890/42
  python tg.py info    https://t.me/c/1234567890/42
  python tg.py ls      --limit 10
  python tg.py delete  https://t.me/c/1234567890/42
  python tg.py cleanup --max-count 100

Author: kesafatkari
License: MIT
"""

import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' not installed. Install with: pip install requests")
    sys.exit(1)

# Database module (same directory)
try:
    from tg_db import Database
except ImportError:
    # Allow tg.py to run even if tg_db.py is missing (db features disabled)
    Database = None

# Encryption module (optional — only needed for --encrypt)
try:
    from tg_crypto import Encryptor as CryptoEncryptor, is_encryption_available
except ImportError:
    CryptoEncryptor = None
    is_encryption_available = lambda: False  # noqa: E731

# Compression module (optional — graceful degradation)
try:
    from tg_compression import compress_file, decompress_file, should_skip_compression
except ImportError:
    compress_file = None
    decompress_file = None
    should_skip_compression = lambda f: True  # noqa: E731 — skip if unavailable

# Chunk header module (optional)
try:
    from tg_chunk_header import (
        create_header as chunk_create_header,
        parse_header as chunk_parse_header,
        is_chunk_with_header,
        HEADER_SIZE as CHUNK_HEADER_SIZE,
        FLAG_COMPRESSED,
        FLAG_ENCRYPTED,
    )
except ImportError:
    chunk_create_header = None
    chunk_parse_header = None
    is_chunk_with_header = lambda d: False  # noqa: E731
    CHUNK_HEADER_SIZE = 0
    FLAG_COMPRESSED = 0
    FLAG_ENCRYPTED = 0

# ==========================================
# Constants & Telegram Limits
# ==========================================
VERSION = 8
DEFAULT_CONFIG_PATH = str(Path.home() / ".tg-vault.json")

MANIFEST_PREFIX = "TG_VAULT_MANIFEST"
DESCRIPTION_PREFIX = "TG_VAULT_DESC"

# Telegram Bot API hard limits
TG_FILE_SIZE_LIMIT = 20 * 1024 * 1024          # getFile download cap (cloud Bot API)
TG_UPLOAD_SIZE_LIMIT = 50 * 1024 * 1024        # sendDocument upload cap
TG_CAPTION_MAX = 1024                          # caption char limit
TG_TEXT_MAX = 4096                             # message text char limit
TG_FILENAME_MAX = 60                           # safe max (Telegram undocumented, use 60)

# Rate limit (per-bot, conservative)
BOT_MIN_INTERVAL = 0.05  # 50 ms = max 20 req/sec per bot

# Retry settings
MAX_RETRIES = 5
BASE_RETRY_DELAY = 2

# Default config
DEFAULT_CHUNK_MB = 19           # under 20MB download limit
DEFAULT_UPLOAD_DELAY = 0.3
DEFAULT_DOWNLOAD_DELAY = 0.2
DEFAULT_PARALLEL_WORKERS = 4    # parallel download chunks


# ==========================================
# Helpers (pure functions)
# ==========================================
def compute_sha256(file_path, chunk_size=8192 * 1024):
    """Stream-compute SHA256 of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def format_size(size_bytes):
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_speed(bytes_per_sec):
    """Human-readable speed."""
    if bytes_per_sec <= 0:
        return "—"
    return f"{format_size(bytes_per_sec)}/s"


def format_eta(seconds):
    """Human-readable ETA."""
    if seconds <= 0 or seconds > 86400:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m{int(seconds % 60)}s"
    return f"{int(seconds / 3600)}h{int((seconds % 3600) / 60)}m"


def sanitize_filename(name, max_len=TG_FILENAME_MAX):
    """Remove illegal chars and truncate filename for Telegram."""
    # Remove illegal chars (Windows + Unix + Telegram)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Truncate, preserving extension
    if len(name) <= max_len:
        return name
    if "." in name:
        base, ext = name.rsplit(".", 1)
        ext = "." + ext
        if len(ext) >= max_len:
            return name[:max_len]
        return base[:max_len - len(ext)] + ext
    return name[:max_len]


def sanitize_hashtag(tag):
    """Sanitize a single hashtag to Telegram rules.

    Telegram hashtag rules (similar to Python variable naming):
      - Must start with a letter (a-z, A-Z) or underscore
      - Can contain letters, digits, and underscores
      - Other characters are not allowed

    Examples:
      "movies,2026" → "movies_2026"
      "123abc"      → "_123abc"
      "sci-fi"      → "sci_fi"
      "hello world" → "hello_world"
    """
    tag = tag.strip().lstrip("#").strip()
    if not tag:
        return None
    # Replace any invalid char with underscore
    tag = re.sub(r"[^a-zA-Z0-9_]", "_", tag)
    # If starts with a digit, prepend underscore
    if tag[0].isdigit():
        tag = "_" + tag
    # Collapse multiple underscores
    tag = re.sub(r"_+", "_", tag)
    # Strip trailing underscores
    tag = tag.rstrip("_")
    if not tag:
        return None
    return tag


def sanitize_hashtags(tags):
    """Sanitize a list of hashtags, dedupe (case-insensitive), filter empty."""
    seen = set()
    result = []
    for t in tags:
        s = sanitize_hashtag(t)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
    return result


def truncate_caption(text, max_len=TG_CAPTION_MAX):
    """Truncate caption to fit Telegram limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def truncate_text(text, max_len=TG_TEXT_MAX):
    """Truncate message text to fit Telegram limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def parse_telegram_link(url):
    """Parse a Telegram message link → (chat_id, message_id).

    Supports:
      - https://t.me/c/2417735052/9072   (private channel)
      - https://t.me/mychannel/123        (public channel)
      - tg://resolve?domain=x&start=123
    """
    url = url.strip()
    m = re.match(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", url)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))
    m = re.match(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{4,})/(\d+)", url)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    m = re.match(r"tg://resolve\?domain=([a-zA-Z][a-zA-Z0-9_]+)&start=(\d+)", url)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    raise ValueError(f"Invalid Telegram link: {url}")


def build_share_link(chat_id, message_id):
    """Build a shareable link from chat_id and message_id."""
    if isinstance(chat_id, str):
        if chat_id.startswith("@"):
            return f"https://t.me/{chat_id[1:]}/{message_id}"
        try:
            chat_id = int(chat_id)
        except ValueError:
            return None
    if isinstance(chat_id, int) and chat_id < 0:
        s = str(chat_id)
        if s.startswith("-100"):
            return f"https://t.me/c/{s[4:]}/{message_id}"
    return None


class ProgressTracker:
    """Thread-safe progress bar with speed/ETA calculation.

    Inspired by TAS: calculates speed every 200ms based on bytes-delta,
    giving a more responsive "current speed" rather than cumulative average.
    """

    def __init__(self, total, prefix=""):
        self.total = total
        self.prefix = prefix
        self.current = 0
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._last_print = 0
        # TAS-style speed sampling
        self._speed_sample_time = self.start_time
        self._speed_sample_value = 0
        self._current_speed = 0  # bytes per second (instantaneous)

    def update(self, n=1):
        with self._lock:
            self.current += n
            now = time.time()
            # Sample speed every 200ms (like TAS)
            elapsed_since_sample = now - self._speed_sample_time
            if elapsed_since_sample >= 0.2:
                delta = self.current - self._speed_sample_value
                self._current_speed = (delta / elapsed_since_sample) if elapsed_since_sample > 0 else 0
                self._speed_sample_time = now
                self._speed_sample_value = self.current
            # Throttle print to 10 Hz
            if now - self._last_print < 0.1 and self.current < self.total:
                return
            self._last_print = now
            self._print()

    def _print(self):
        if self.total == 0:
            return
        percent = (self.current / self.total) * 100
        bar_len = 30
        filled = int(bar_len * self.current // self.total)
        bar = "█" * filled + "░" * (bar_len - filled)

        # Use instantaneous speed (sampled) instead of cumulative average
        speed_str = format_speed(self._current_speed)

        # ETA based on instantaneous speed, fallback to average
        if self._current_speed > 0:
            eta = (self.total - self.current) / self._current_speed
            eta_str = format_eta(eta)
        else:
            # Fallback to cumulative average
            elapsed = time.time() - self.start_time
            if self.current > 0 and elapsed > 0:
                avg_speed = self.current / elapsed
                eta = (self.total - self.current) / avg_speed if avg_speed > 0 else 0
                eta_str = format_eta(eta)
            else:
                eta_str = "—"

        print(f"\r{self.prefix} |{bar}| {self.current}/{self.total} "
              f"({percent:.1f}%) {speed_str} ETA:{eta_str}    ",
              end="", flush=True)
        if self.current >= self.total:
            print()


# ==========================================
# Config
# ==========================================
class Config:
    """JSON-backed config file (~/.tg-vault.json)."""

    def __init__(self, data=None, path=None):
        self.path = path or DEFAULT_CONFIG_PATH
        data = data or {}
        self.bots = data.get("bots", [])
        channels = data.get("channels", {}) or {}
        self.main_channel = channels.get("main")
        self.temp_channel = channels.get("temp") or self.main_channel
        self.chunk_size = int(data.get("chunk_size_mb", DEFAULT_CHUNK_MB)) * 1024 * 1024
        self.upload_delay = float(data.get("upload_delay", DEFAULT_UPLOAD_DELAY))
        self.download_delay = float(data.get("download_delay", DEFAULT_DOWNLOAD_DELAY))
        self.parallel_workers = int(data.get("parallel_workers", DEFAULT_PARALLEL_WORKERS))
        # Database settings
        self.db_enabled = bool(data.get("db_enabled", False))
        self.db_path = data.get("db_path")  # None → default location

    @classmethod
    def load(cls, path=None):
        path = path or DEFAULT_CONFIG_PATH
        if not os.path.exists(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f), path)

    def save(self):
        data = {
            "bots": self.bots,
            "channels": {
                "main": self.main_channel,
                "temp": self.temp_channel if self.temp_channel != self.main_channel else None,
            },
            "chunk_size_mb": self.chunk_size // (1024 * 1024),
            "upload_delay": self.upload_delay,
            "download_delay": self.download_delay,
            "parallel_workers": self.parallel_workers,
            "db_enabled": self.db_enabled,
            "db_path": self.db_path,
            "version": VERSION,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def validate(self):
        errors = []
        if not self.bots:
            errors.append("No bots configured. Run: tg.py bots add <TOKEN>")
        if not self.main_channel:
            errors.append("Main channel not set. Run: tg.py channels set main <ID>")
        if self.chunk_size > TG_FILE_SIZE_LIMIT:
            errors.append(f"chunk_size_mb too large (max {TG_FILE_SIZE_LIMIT // (1024*1024)} MB)")
        return errors

    # ─────────────── Database helpers ───────────────

    def get_db_path(self):
        """Resolve the database path with priority:
          1. config.db_path (explicit)
          2. alongside the config file: <config_dir>/tg-vault.db
          3. ~/.tg-vault.db
        """
        if self.db_path:
            return os.path.expanduser(self.db_path)
        # Default: next to config file
        config_dir = os.path.dirname(os.path.abspath(self.path))
        return os.path.join(config_dir, "tg-vault.db")

    def get_db(self):
        """Return a Database instance if enabled, else None."""
        if not self.db_enabled or Database is None:
            return None
        return Database(self.get_db_path())


# ==========================================
# Bot Pool — thread-safe multi-bot rotation with rate limiting
# ==========================================
class Bot:
    """A single bot with its own session, rate limiter, and stats."""

    def __init__(self, token, username=""):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()
        self.username = username
        self.id = None
        self.first_name = ""
        self._last_request_time = 0.0
        self._lock = threading.Lock()
        self.request_count = 0
        self.error_count = 0

    def init_info(self):
        """Fetch bot's id and username from Telegram."""
        try:
            r = self.session.get(self.api_url + "getMe", timeout=30).json()
            if r.get("ok"):
                self.id = r["result"]["id"]
                self.username = r["result"].get("username", self.username)
                self.first_name = r["result"].get("first_name", "")
                return True
        except Exception as e:
            print(f"Warning: failed to fetch bot info for {self.token[:15]}...: {e}")
        return False

    def throttle(self):
        """Enforce min interval between requests to avoid FloodWait."""
        with self._lock:
            now = time.time()
            wait = BOT_MIN_INTERVAL - (now - self._last_request_time)
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.time()

    def request(self, method, data=None, files=None, retries=MAX_RETRIES):
        """Send a request to Telegram with FloodWait handling and retry."""
        self.throttle()
        url = self.api_url + method
        for attempt in range(1, retries + 1):
            try:
                if files:
                    res = self.session.post(url, data=data, files=files, timeout=300)
                else:
                    res = self.session.post(url, data=data, timeout=60)
                self.request_count += 1
                result = res.json()

                if result.get("ok"):
                    return result

                error_code = result.get("error_code", 0)

                # FloodWait — wait and retry
                if error_code == 429:
                    params = result.get("parameters", {}) or {}
                    retry_after = params.get("retry_after", 5)
                    print(f"\n  ⏳ FloodWait @{self.username}: {retry_after}s...")
                    time.sleep(retry_after + 1)
                    continue

                # 5xx — retry with backoff
                if 500 <= error_code < 600:
                    delay = BASE_RETRY_DELAY * attempt
                    time.sleep(delay)
                    continue

                # 4xx — return error (caller decides)
                return result

            except requests.exceptions.RequestException as e:
                delay = BASE_RETRY_DELAY * attempt
                print(f"\n  ⚠️ Network error @{self.username}: {e}. Retry in {delay}s...")
                time.sleep(delay)

        self.error_count += 1
        return None


class BotPool:
    """Thread-safe round-robin pool of bots."""

    def __init__(self, bots_config):
        self.bots = []
        self._counter = 0
        self._lock = threading.Lock()

        for b in bots_config:
            bot = Bot(b["token"], b.get("username", ""))
            if bot.init_info():
                self.bots.append(bot)
            else:
                print(f"Warning: bot {b['token'][:15]}... could not be initialized.")

    def get_next(self):
        """Get the next bot in round-robin order (thread-safe)."""
        with self._lock:
            if not self.bots:
                return None
            bot = self.bots[self._counter % len(self.bots)]
            self._counter += 1
            return bot

    def __len__(self):
        return len(self.bots)

    def list_bots(self):
        return list(self.bots)

    def stats(self):
        return [
            {
                "username": b.username,
                "id": b.id,
                "requests": b.request_count,
                "errors": b.error_count,
            }
            for b in self.bots
        ]


# ==========================================
# Uploader
# ==========================================
class Uploader:
    """Upload a file as a chain of reply-linked chunks + manifest.

    v8 features (inspired by TAS):
      - Optional AES-256-GCM encryption (zero-knowledge)
      - Optional smart compression (skips already-compressed formats)
      - Self-describing chunk header (TGV1 magic)
    """

    def __init__(self, config, bot_pool, db=None):
        self.config = config
        self.bot_pool = bot_pool
        self.db = db  # optional Database instance
        self.session_id = uuid.uuid4().hex[:8]
        self._interrupted = False

    def upload(self, file_path, description="", hashtags=None, resume=False,
               encrypt=False, password=None, compress=True):
        """Upload file with optional description + hashtags.

        Args:
            encrypt: If True, encrypt chunks with AES-256-GCM using `password`.
            password: Password for encryption (required if encrypt=True).
            compress: If True, gzip-compress chunks (skips already-compressed formats).

        Returns a dict with keys: share_link, manifest (full manifest dict).
        """
        if not os.path.exists(file_path):
            print(f"Error: file not found: {file_path}")
            return None

        file_size = os.path.getsize(file_path)
        if file_size > 2000 * 1024 * 1024:
            print(f"Error: file too large ({format_size(file_size)}). "
                  "Telegram Bot API max is 2 GB even with Local Bot API Server.")
            return None

        file_name = os.path.basename(file_path)
        file_name = sanitize_filename(file_name)
        hashtags = hashtags or []
        resume_path = f"{file_name}.resume.json"

        # Encryption setup
        encryptor = None
        encryption_salt = None
        password_hash = None
        if encrypt:
            if not is_encryption_available():
                print("❌ Encryption requires 'cryptography' library. Install with: pip install cryptography")
                return None
            if not password:
                print("❌ Encryption requires a password. Use --password or set TG_VAULT_PASSWORD env var.")
                return None
            encryptor = CryptoEncryptor(password)
            encryption_salt = encryptor.salt
            password_hash = encryptor.get_password_hash(password)
            print(f"🔐 Encryption: ENABLED (AES-256-GCM, PBKDF2 600k iterations)")
            print(f"   Salt: {encryptor.salt_to_str(encryption_salt)[:24]}...")
            print(f"   Password hash: {password_hash[:16]}...")

        # Compression setup
        will_compress = compress and compress_file is not None and not should_skip_compression(file_name)
        if compress and not will_compress and compress_file is not None:
            print(f"📦 Compression: SKIPPED (already-compressed format: {file_name})")
        elif will_compress:
            print(f"📦 Compression: ENABLED (gzip level 6)")

        total_parts = max(1, math.ceil(file_size / self.config.chunk_size))
        print(f"\n📦 File: {file_name}")
        print(f"   Size: {format_size(file_size)}")
        print(f"   Parts: {total_parts}")
        print(f"   Session: {self.session_id}")

        # SHA256 (computed on ORIGINAL file, before any processing)
        print("\n🔍 Computing SHA256 of original file...")
        file_hash = compute_sha256(file_path)
        print(f"✅ SHA256: {file_hash}")

        # First 16 bytes of SHA256 for chunk headers
        sha256_prefix = bytes.fromhex(file_hash)[:16]

        # Resume state
        message_ids = []
        prev_msg_id = None
        desc_msg_id = None
        start_part = 1

        if resume and os.path.exists(resume_path):
            try:
                with open(resume_path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if (old.get("sha256") == file_hash
                        and old.get("name") == file_name):
                    message_ids = old.get("message_ids", [])
                    desc_msg_id = old.get("description_msg_id")
                    if len(message_ids) >= total_parts:
                        print("\n✅ All parts already uploaded. Sending manifest only.")
                        share_link, manifest_dict = self._send_manifest(
                            file_name, file_size, total_parts,
                            message_ids, file_hash, desc_msg_id,
                            description, hashtags,
                            encrypt=bool(encryptor),
                            encryption_salt=encryption_salt,
                            password_hash=password_hash,
                            compress=will_compress,
                            sha256_prefix=sha256_prefix,
                        )
                        if os.path.exists(resume_path):
                            os.remove(resume_path)
                        if share_link:
                            self._print_summary(file_name, file_size, total_parts,
                                                file_hash, message_ids, desc_msg_id,
                                                share_link)
                            if self.db and manifest_dict:
                                self._log_to_db(manifest_dict, share_link)
                                print(f"💾 Database updated: {self.config.get_db_path()}")
                            return {"share_link": share_link, "manifest": manifest_dict}
                    elif len(message_ids) > 0:
                        start_part = len(message_ids) + 1
                        prev_msg_id = message_ids[-1]
                        print(f"\n▶️ Resuming from part {start_part} "
                              f"({len(message_ids)} parts already uploaded)")
                else:
                    print("\n⚠️ Hash or name mismatch. Starting fresh.")
            except Exception as e:
                print(f"\n⚠️ Failed to read resume state ({e}). Starting fresh.")

        # Send description (if starting fresh)
        if start_part == 1:
            desc_msg_id = self._send_description(
                file_name, file_size, total_parts, file_hash,
                description, hashtags
            )
            if desc_msg_id is None:
                print("Error: failed to send description message.")
                return None
            prev_msg_id = desc_msg_id

        # Upload chunks
        progress = ProgressTracker(total_parts, prefix="Upload  ")
        try:
            with open(file_path, "rb") as f:
                f.seek((start_part - 1) * self.config.chunk_size)
                for part_num in range(start_part, total_parts + 1):
                    if self._interrupted:
                        raise KeyboardInterrupt
                    raw_chunk = f.read(self.config.chunk_size)

                    # Pipeline: raw → compress (optional) → encrypt (optional) → prepend header
                    processed = raw_chunk

                    if will_compress and compress_file is not None:
                        from tg_compression import compress_data
                        processed, _ = compress_data(processed, file_name)

                    iv = None
                    if encryptor:
                        # Use deterministic IV derived from chunk index.
                        # This is acceptable in GCM as long as (key, IV) is never reused —
                        # and since chunk_index is unique per file, this is safe.
                        # Using random IV per chunk would require storing all IVs in the
                        # manifest, which would bloat it for large files.
                        iv = (part_num - 1).to_bytes(12, "big")
                        processed = encryptor.encrypt_chunk_with_iv(processed, iv)

                    # Prepend self-describing header (TGV1)
                    flags = 0
                    if will_compress:
                        flags |= FLAG_COMPRESSED
                    if encryptor:
                        flags |= FLAG_ENCRYPTED
                    if chunk_create_header is not None:
                        header = chunk_create_header(
                            chunk_index=part_num - 1,
                            total_chunks=total_parts,
                            original_size=file_size,
                            sha256_prefix=sha256_prefix,
                            flags=flags,
                        )
                        processed = header + processed

                    part_name = sanitize_filename(
                        f"{file_name}.part{part_num:04d}of{total_parts:04d}"
                    )

                    bot = self.bot_pool.get_next()
                    files = {"document": (part_name, processed)}
                    caption = truncate_caption(
                        f"📦 part {part_num}/{total_parts} | {file_name} | "
                        f"#{self.session_id}"
                    )
                    data = {
                        "chat_id": self.config.main_channel,
                        "caption": caption,
                    }
                    if prev_msg_id is not None:
                        data["reply_to_message_id"] = prev_msg_id

                    res = bot.request("sendDocument", data=data, files=files)

                    if res and res.get("ok"):
                        msg_id = res["result"]["message_id"]
                        message_ids.append(msg_id)
                        prev_msg_id = msg_id
                        self._save_resume(resume_path, file_name, file_hash,
                                          message_ids, desc_msg_id)
                        progress.update(1)
                    else:
                        err = res.get("description") if res else "No response"
                        print(f"\nError uploading part {part_num}: {err}")
                        print(f"\nTo resume: python tg.py upload \"{file_path}\" --resume")
                        return None

                    time.sleep(self.config.upload_delay)

        except KeyboardInterrupt:
            self._interrupted = True
            print("\n\n⚠️ Interrupted! Resume state saved.")
            print(f"To resume: python tg.py upload \"{file_path}\" --resume")
            return None

        # Send manifest
        print("\n📋 Sending manifest...")
        share_link, manifest_dict = self._send_manifest(
            file_name, file_size, total_parts,
            message_ids, file_hash, desc_msg_id,
            description, hashtags,
            encrypt=bool(encryptor),
            encryption_salt=encryption_salt,
            password_hash=password_hash,
            compress=will_compress,
            sha256_prefix=sha256_prefix,
        )

        # Clean up resume state
        if os.path.exists(resume_path):
            os.remove(resume_path)

        if share_link:
            self._print_summary(file_name, file_size, total_parts,
                                file_hash, message_ids, desc_msg_id, share_link)
            # Log to database if enabled
            if self.db and manifest_dict:
                self._log_to_db(manifest_dict, share_link)
                print(f"💾 Database updated: {self.config.get_db_path()}")
            return {"share_link": share_link, "manifest": manifest_dict}
        return None

    def _send_description(self, file_name, file_size, total_parts,
                          file_hash, description, hashtags):
        """Send the description message (before any parts)."""
        lines = [
            f"📦 File: {file_name}",
            f"💾 Size: {format_size(file_size)}",
            f"🔢 Parts: {total_parts}",
            f"🔐 SHA256: {file_hash}",
            f"🆔 Session: {self.session_id}",
        ]
        if description:
            lines.append("")
            lines.append("📝 Description:")
            lines.append(description)
        if hashtags:
            lines.append("")
            tag_str = " ".join(f"#{t.lstrip('#')}" for t in hashtags)
            lines.append(tag_str)

        text = truncate_text("\n".join(lines))
        bot = self.bot_pool.get_next()
        res = bot.request("sendMessage", data={
            "chat_id": self.config.main_channel,
            "text": text,
            "disable_web_page_preview": True,
        })
        if res and res.get("ok"):
            return res["result"]["message_id"]
        return None

    def _send_manifest(self, file_name, file_size, total_parts,
                       message_ids, file_hash, desc_msg_id,
                       description, hashtags,
                       encrypt=False, encryption_salt=None, password_hash=None,
                       compress=False, sha256_prefix=None):
        """Send the manifest as the final message (reply to last part).
        Returns (share_link, manifest_dict) or (None, None)."""
        manifest = {
            "name": file_name,
            "size": file_size,
            "total_parts": total_parts,
            "chunk_size": self.config.chunk_size,
            "message_ids": message_ids,
            "sha256": file_hash,
            "channel_id": self.config.main_channel,
            "description_msg_id": desc_msg_id,
            "description": description,
            "hashtags": hashtags,
            "session_id": self.session_id,
            "version": VERSION,
            "created_at": time.time(),
            # v8 features
            "encrypted": encrypt,
            "compressed": compress,
            "has_chunk_header": chunk_create_header is not None,
        }
        if encrypt and encryption_salt is not None:
            manifest["encryption_salt"] = CryptoEncryptor.salt_to_str(encryption_salt)
            manifest["encryption_algorithm"] = "aes-256-gcm"
            manifest["encryption_kdf"] = "pbkdf2-sha512-600k"
            manifest["password_hash"] = password_hash  # for verification only
        if sha256_prefix is not None:
            # Store as base64-encoded string for JSON safety
            import base64
            manifest["sha256_prefix_b64"] = base64.b64encode(sha256_prefix).decode("ascii")
        json_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        manifest_filename = sanitize_filename(f"{file_name}.manifest.json")

        bot = self.bot_pool.get_next()
        files = {"document": (manifest_filename, io.BytesIO(json_bytes))}
        caption = truncate_caption(
            f"{MANIFEST_PREFIX}|{file_name}|{total_parts}|{file_hash[:16]}"
        )
        data = {
            "chat_id": self.config.main_channel,
            "caption": caption,
        }
        if message_ids:
            data["reply_to_message_id"] = message_ids[-1]

        res = bot.request("sendDocument", data=data, files=files)
        if not res or not res.get("ok"):
            err = res.get("description") if res else "No response"
            print(f"Error sending manifest: {err}")
            return None, None

        manifest_msg_id = res["result"]["message_id"]
        manifest["manifest_message_id"] = manifest_msg_id
        share_link = build_share_link(self.config.main_channel, manifest_msg_id)
        return share_link, manifest

    def _save_resume(self, path, file_name, file_hash, message_ids, desc_msg_id):
        data = {
            "name": file_name,
            "sha256": file_hash,
            "message_ids": message_ids,
            "description_msg_id": desc_msg_id,
            "session_id": self.session_id,
            "saved_at": time.time(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _print_summary(self, file_name, file_size, total_parts,
                       file_hash, message_ids, desc_msg_id, share_link):
        print("\n" + "=" * 60)
        print("✅ Upload complete!")
        print(f"   File: {file_name}")
        print(f"   Size: {format_size(file_size)}")
        print(f"   Parts: {total_parts}")
        print(f"   SHA256: {file_hash}")
        print(f"   Description msg: {desc_msg_id}")
        print(f"   First part: {message_ids[0] if message_ids else '?'}")
        print(f"   Last part: {message_ids[-1] if message_ids else '?'}")
        print(f"\n🔗 ★ Download link:")
        print(f"   {share_link}")
        print("=" * 60)

    def _log_to_db(self, manifest, share_link):
        """Insert a record into the database. Silent on errors."""
        if not self.db:
            return
        try:
            # Check if file already exists (by SHA256)
            existing = self.db.get_file_by_sha(manifest["sha256"])
            if existing:
                # Update share_link, status
                self.db.update_share_link(existing["id"], share_link)
                return existing["id"]
            return self.db.insert_file(manifest, share_link,
                                        temp_channel=self.config.temp_channel)
        except Exception as e:
            print(f"⚠️ Database log failed: {e}")
            return None


# ==========================================
# Downloader (with parallel chunk download)
# ==========================================
class Downloader:
    """Download a file from its manifest link, with parallel chunks."""

    def __init__(self, config, bot_pool, db=None):
        self.config = config
        self.bot_pool = bot_pool
        self.db = db  # optional Database instance
        self.session_id = uuid.uuid4().hex[:8]
        self._temp_msg_ids = []  # (chat_id, msg_id) tuples
        self._temp_lock = threading.Lock()
        self._interrupted = False

    def download(self, link, resume=False, output=None, output_dir=".", password=None):
        """Download file from manifest link.

        Args:
            password: Required if manifest indicates encryption.
        """
        print(f"\n🌐 Link: {link}")
        print(f"🆔 Download session: {self.session_id}")

        try:
            chat_id, message_id = parse_telegram_link(link)
        except ValueError as e:
            print(f"Error: {e}")
            return False
        print(f"🔗 Parsed: chat_id={chat_id}, message_id={message_id}")

        # Fetch manifest
        manifest = self._fetch_manifest(chat_id, message_id)
        if not manifest:
            return False

        return self._download_from_manifest(manifest, resume, output, output_dir, password=password)

    def info(self, link):
        """Show manifest info without downloading."""
        print(f"\n🌐 Link: {link}")
        try:
            chat_id, message_id = parse_telegram_link(link)
        except ValueError as e:
            print(f"Error: {e}")
            return False
        manifest = self._fetch_manifest(chat_id, message_id)
        if not manifest:
            return False

        print("\n" + "=" * 60)
        print("📋 File info:")
        print(f"   Name: {manifest['name']}")
        print(f"   Size: {format_size(manifest['size'])}")
        print(f"   Parts: {manifest['total_parts']}")
        print(f"   SHA256: {manifest['sha256']}")
        print(f"   Channel: {manifest.get('channel_id', '?')}")
        if manifest.get("description"):
            print(f"   Description: {manifest['description']}")
        if manifest.get("hashtags"):
            print(f"   Hashtags: {', '.join(manifest['hashtags'])}")
        print(f"   Created: {time.ctime(manifest.get('created_at', 0))}")
        print(f"   Version: v{manifest.get('version', '?')}")
        print("=" * 60)
        return True

    def _fetch_manifest(self, chat_id, message_id):
        """Fetch and parse the manifest message."""
        print("📡 Fetching manifest...")
        copied = self._fetch_message(chat_id, message_id)
        if not copied:
            return None

        caption = copied.get("caption", "")
        if not caption.startswith(MANIFEST_PREFIX):
            print(f"Error: not a manifest message. caption: {caption[:100]}")
            self._cleanup()
            return None

        try:
            parts = caption.split("|")
            file_name = parts[1]
            total_parts = int(parts[2])
            print(f"✅ Manifest found: '{file_name}' with {total_parts} parts")
        except (IndexError, ValueError):
            print("Error: malformed manifest caption.")
            self._cleanup()
            return None

        content = self._download_document(copied)
        self._cleanup()

        if not content:
            return None

        try:
            return json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Error parsing manifest JSON: {e}")
            return None

    def _fetch_message(self, source_chat_id, message_id):
        """
        Forward a message from source channel to temp channel.
        Note: we use forwardMessage (not copyMessage) because copyMessage
        does not return caption for channel messages (Telegram quirk).
        """
        bot = self.bot_pool.get_next()
        res = bot.request("forwardMessage", data={
            "chat_id": self.config.temp_channel,
            "from_chat_id": source_chat_id,
            "message_id": message_id,
            "disable_notification": True,
        })
        if not res or not res.get("ok"):
            err = res.get("description") if res else "No response"
            print(f"\n  Error in forwardMessage: {err}")
            if "not enough rights" in str(err).lower():
                print("  ⚠️ Bot lacks permissions (admin required).")
            return None
        temp_msg_id = res["result"]["message_id"]
        with self._temp_lock:
            self._temp_msg_ids.append((self.config.temp_channel, temp_msg_id))
        return res["result"]

    def _download_document(self, message_dict):
        """Download document content from a forwarded message (in temp channel)."""
        doc = message_dict.get("document")
        if not doc:
            return None
        file_id = doc["file_id"]
        file_size = doc.get("file_size", 0)

        if file_size > TG_FILE_SIZE_LIMIT:
            print(f"\n  Error: file {format_size(file_size)} > 20MB. "
                  "Bot cannot download via getFile.")
            return None

        bot = self.bot_pool.get_next()
        file_res = bot.request("getFile", data={"file_id": file_id})
        if not file_res or not file_res.get("ok"):
            return None

        file_path = file_res["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"

        try:
            r = bot.session.get(url, timeout=300)
            if r.status_code != 200:
                print(f"\n  HTTP {r.status_code}")
                return None
            return r.content
        except requests.exceptions.RequestException as e:
            print(f"\n  Download error: {e}")
            return None

    def _cleanup(self):
        """Delete all forwarded temp messages."""
        with self._temp_lock:
            msgs = list(self._temp_msg_ids)
            self._temp_msg_ids.clear()
        if not msgs:
            return
        for chat_id, msg_id in msgs:
            bot = self.bot_pool.get_next()
            bot.request("deleteMessage", data={
                "chat_id": chat_id,
                "message_id": msg_id,
            })

    def _download_part(self, source_chat_id, msg_id, part_num):
        """Download a single part (worker function for parallel download)."""
        try:
            copied = self._fetch_message(source_chat_id, msg_id)
            if not copied:
                return part_num, None
            content = self._download_document(copied)
            # Cleanup this specific forward
            # (already appended to _temp_msg_ids; cleaned up periodically)
            return part_num, content
        except Exception as e:
            print(f"\n  Error in part {part_num}: {e}")
            return part_num, None

    def _download_from_manifest(self, manifest, resume=False, output=None, output_dir=".",
                                  password=None):
        """Download all parts in parallel and assemble.

        Args:
            password: Required if manifest indicates encryption.
        """
        file_name = manifest["name"]
        expected_size = manifest["size"]
        total_parts = manifest["total_parts"]
        message_ids = manifest["message_ids"]
        expected_hash = manifest["sha256"]
        source_chat_id = manifest["channel_id"]
        is_encrypted = manifest.get("encrypted", False)
        is_compressed = manifest.get("compressed", False)
        has_chunk_header = manifest.get("has_chunk_header", False)

        if len(message_ids) != total_parts:
            print(f"Error: manifest inconsistent: {len(message_ids)} ids for {total_parts} parts")
            return False

        # Encryption setup
        encryptor = None
        if is_encrypted:
            if not is_encryption_available():
                print("❌ This file is encrypted. Install cryptography: pip install cryptography")
                return False
            if not password:
                # Try env var
                password = os.environ.get("TG_VAULT_PASSWORD")
                if not password:
                    import getpass
                    print("🔐 This file is encrypted with AES-256-GCM.")
                    password = getpass.getpass("Enter password: ")
            # Verify password
            stored_hash = manifest.get("password_hash")
            if stored_hash and not CryptoEncryptor.verify_password_hash(password, stored_hash):
                print("❌ Wrong password (verification hash mismatch).")
                return False
            salt = CryptoEncryptor.salt_from_str(manifest["encryption_salt"])
            encryptor = CryptoEncryptor(password, salt=salt)
            print(f"🔐 Decryption: ENABLED (AES-256-GCM)")

        if is_compressed:
            print(f"📦 Decompression: ENABLED (gzip)")
        if has_chunk_header:
            print(f"🏷️  Self-describing chunks: ENABLED (TGV1 header)")

        # Determine output path
        if output:
            out_path = os.path.join(output_dir, output) if not os.path.isabs(output) else output
        else:
            out_path = os.path.join(output_dir, sanitize_filename(file_name))

        temp_file = out_path + ".downloading"

        print(f"\n📥 Downloading: {file_name}")
        print(f"   Size: {format_size(expected_size)}")
        print(f"   Parts: {total_parts}")
        print(f"   SHA256: {expected_hash}")
        print(f"   Output: {out_path}")
        print(f"   Parallel workers: {min(self.config.parallel_workers, len(self.bot_pool))}\n")

        start_part = 1
        if resume and os.path.exists(temp_file):
            current_size = os.path.getsize(temp_file)
            completed = current_size // self.config.chunk_size
            if 0 < completed < total_parts:
                start_part = completed + 1
                with open(temp_file, "r+b") as f:
                    f.truncate(completed * self.config.chunk_size)
                print(f"▶️ Resuming from part {start_part} ({completed} parts done)\n")
            elif completed >= total_parts:
                print("🔍 File looks complete. Verifying...\n")
                start_part = total_parts + 1

        try:
            if start_part <= total_parts:
                # Strategy: download N parts in parallel, write in order
                workers = min(self.config.parallel_workers, max(1, len(self.bot_pool)))
                progress = ProgressTracker(total_parts - start_part + 1, prefix="Download")

                # Use a queue: download parts in batches of `workers`
                # to keep memory bounded
                next_to_download = start_part
                next_to_write = start_part
                pending = {}  # part_num -> content (out of order)
                write_lock = threading.Lock()

                mode = "ab" if start_part > 1 else "wb"
                with open(temp_file, mode) as out_file:
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        # Submit initial batch
                        in_flight = 0
                        max_in_flight = workers * 2  # buffer ahead

                        futures = {}
                        while next_to_write <= total_parts or futures:
                            # Submit new tasks up to max_in_flight
                            while (next_to_download <= total_parts
                                   and in_flight < max_in_flight):
                                msg_id = message_ids[next_to_download - 1]
                                fut = executor.submit(
                                    self._download_part,
                                    source_chat_id,
                                    msg_id,
                                    next_to_download
                                )
                                futures[fut] = next_to_download
                                next_to_download += 1
                                in_flight += 1

                            if not futures:
                                break

                            # Wait for at least one to complete
                            done, _ = next(iter(
                                [(f, f.result()) for f in as_completed(list(futures.keys()))]
                                + [([], None)]
                            )) if False else (set(), None)

                            # Use as_completed properly
                            for fut in as_completed(list(futures.keys())):
                                part_num = futures.pop(fut)
                                in_flight -= 1
                                _, content = fut.result()
                                if content is None:
                                    print(f"\nError downloading part {part_num}")
                                    self._cleanup()
                                    return False
                                pending[part_num] = content

                                # Cleanup temp messages periodically
                                if len(pending) >= workers:
                                    self._cleanup()

                                # Write any parts that are ready in order
                                with write_lock:
                                    while next_to_write in pending:
                                        raw = pending.pop(next_to_write)
                                        # Strip header if present
                                        if has_chunk_header and is_chunk_with_header(raw):
                                            raw = raw[CHUNK_HEADER_SIZE:]
                                        # Decrypt if needed
                                        if encryptor:
                                            # AESGCM ciphertext includes tag at end
                                            # We need to know the IV. For simplicity in v8,
                                            # we derive IV deterministically from chunk index
                                            # (counter mode). For true random IV, would need
                                            # to store IVs in manifest.
                                            iv = (next_to_write - 1).to_bytes(12, "big")
                                            try:
                                                raw = encryptor.decrypt_chunk(raw, iv)
                                            except Exception as e:
                                                print(f"\n❌ Decryption failed for part {next_to_write}: {e}")
                                                return False
                                        # Decompress if needed
                                        if is_compressed and decompress_file is not None:
                                            from tg_compression import decompress_data
                                            raw = decompress_data(raw, True)
                                        out_file.write(raw)
                                        next_to_write += 1
                                        progress.update(1)

                                # Check if we should submit more
                                if next_to_download <= total_parts and in_flight < max_in_flight:
                                    break

                        # Final cleanup
                        self._cleanup()

        except KeyboardInterrupt:
            self._interrupted = True
            self._cleanup()
            print("\n\n⚠️ Interrupted! Partial download saved.")
            print(f"To resume: python tg.py download \"{manifest.get('share_link', '')}\" --resume")
            return False

        # Verify size
        actual_size = os.path.getsize(temp_file)
        if actual_size != expected_size:
            print(f"\n⚠️ Size mismatch: {actual_size} bytes (expected {expected_size})")

        # Verify SHA256
        print("\n🔍 Verifying SHA256...")
        actual_hash = compute_sha256(temp_file)

        if actual_hash == expected_hash:
            print("✅ SHA256 verified!")
            if os.path.exists(out_path):
                os.remove(out_path)
            os.rename(temp_file, out_path)
            print(f"\n✅ File saved: {out_path}")
            self._cleanup()
            # Log download to database
            if self.db:
                try:
                    existing = self.db.get_file_by_sha(expected_hash)
                    if existing:
                        self.db.log_download(existing["id"], out_path, True)
                    else:
                        # File not in DB — insert with what we know from manifest
                        file_id = self.db.insert_file(manifest, "", temp_channel=manifest.get("channel_id"))
                        self.db.log_download(file_id, out_path, True)
                except Exception as e:
                    print(f"⚠️ Database log failed: {e}")
            return True
        else:
            print(f"❌ SHA256 mismatch!")
            print(f"   Expected: {expected_hash}")
            print(f"   Got:      {actual_hash}")
            print(f"   Partial file kept: {temp_file}")
            return False


# ==========================================
# CLI Commands
# ==========================================
def cmd_init(args, config_path):
    """Initialize a sample config file."""
    if os.path.exists(config_path):
        print(f"⚠️ Config file already exists: {config_path}")
        if input("Overwrite? (y/N): ").strip().lower() != "y":
            return
    config = Config(path=config_path)
    config.save()
    print(f"✅ Config file created: {config_path}")
    print("\nNext steps:")
    print("  Option A — Interactive wizard (recommended):")
    print("    python tg.py setup")
    print("  Option B — Manual commands:")
    print("    python tg.py bots add <TOKEN>")
    print("    python tg.py channels set main <CHANNEL_ID>")
    print("    python tg.py channels set temp <CHANNEL_ID>  (optional)")
    print("    python tg.py test")
    print("  Option C — Edit the config file directly:")
    print(f"    $EDITOR {config_path}")


def cmd_setup(args, config):
    """Interactive setup wizard — bot token + channels in one go."""
    print("\n" + "=" * 55)
    print("    🪄 tg-vault setup wizard")
    print("=" * 55)
    print()

    # Show current state if any
    if config.bots or config.main_channel:
        print("📋 Current configuration:")
        if config.bots:
            print(f"   Bots: {len(config.bots)} ({', '.join('@' + b.get('username', '?') for b in config.bots)})")
        if config.main_channel:
            print(f"   Main channel: {config.main_channel}")
        if config.temp_channel and config.temp_channel != config.main_channel:
            print(f"   Temp channel: {config.temp_channel}")
        print()
        if input("Reconfigure? (y/N): ").strip().lower() != "y":
            return
        print()

    # Step 1: Bot token
    print("─" * 55)
    print("Step 1/4 — Bot token")
    print("─" * 55)
    print("Get a token from @BotFather (https://t.me/BotFather):")
    print("  1. Send /newbot to @BotFather")
    print("  2. Choose a name and username")
    print("  3. Copy the token (looks like 123456789:ABC-DEF...)")
    print()
    token = input("Bot token: ").strip()
    if not token:
        print("Cancelled.")
        return

    # Verify token
    print("\n🔍 Verifying token...")
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
        if not r.get("ok"):
            print(f"❌ Invalid token: {r.get('description')}")
            return
        username = r["result"].get("username", "")
        bot_id = r["result"]["id"]
        print(f"✅ Bot verified: @{username} (id: {bot_id})")
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # Step 2: Main channel
    print("\n" + "─" * 55)
    print("Step 2/4 — Main channel")
    print("─" * 55)
    print("Create a Telegram channel (private recommended), then:")
    print("  1. Add your bot as administrator")
    print("  2. Give it 'Post messages' and 'Delete messages' rights")
    print("  3. Get the channel ID (see https://github.com/kesafatkari/tg-vault#getting-a-channel-id)")
    print()
    print("Common formats:")
    print("  • Private channel:  -1001234567890  (starts with -100)")
    print("  • Public channel:   @mychannel_username")
    print()
    main_channel = input("Main channel ID: ").strip()
    if not main_channel:
        print("Cancelled.")
        return

    # Verify channel access
    print("\n🔍 Verifying channel access...")
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getChat",
            params={"chat_id": main_channel},
            timeout=30
        ).json()
        if not r.get("ok"):
            print(f"❌ Cannot access channel: {r.get('description')}")
            print("   Make sure the bot is added as admin to the channel.")
            return
        chat = r["result"]
        print(f"✅ Channel: {chat.get('title', '?')} (type: {chat.get('type')})")

        # Check admin rights
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getChatMember",
            params={"chat_id": main_channel, "user_id": bot_id},
            timeout=30
        ).json()
        if r.get("ok"):
            status = r["result"]["status"]
            can_post = r["result"].get("can_post_messages", status == "administrator")
            can_delete = r["result"].get("can_delete_messages", status == "administrator")
            if status != "administrator" or not can_post or not can_delete:
                print(f"⚠️ Bot status: {status}, post={can_post}, delete={can_delete}")
                print("   The bot needs admin rights with Post + Delete messages!")
                if input("Continue anyway? (y/N): ").strip().lower() != "y":
                    return
            else:
                print(f"✅ Bot is admin with proper rights (post={can_post}, delete={can_delete})")
    except Exception as e:
        print(f"⚠️ Could not verify channel: {e}")
        if input("Continue anyway? (y/N): ").strip().lower() != "y":
            return

    # Step 3: Temp channel
    print("\n" + "─" * 55)
    print("Step 3/4 — Temp channel (optional)")
    print("─" * 55)
    print("A separate temp channel keeps your main channel clean.")
    print("The bot uses it for temporary forwarded messages during downloads.")
    print("Press Enter to use the main channel as temp.")
    print()
    temp_channel = input("Temp channel ID (or Enter for same as main): ").strip()
    if not temp_channel:
        temp_channel = main_channel
        print(f"   Using main channel as temp: {temp_channel}")

    # Step 4: Database
    print("\n" + "─" * 55)
    print("Step 4/4 — Database (optional, recommended)")
    print("─" * 55)
    print("A SQLite database stores metadata for every uploaded file:")
    print("  name, size, SHA256, parts, message IDs, description, hashtags,")
    print("  share link, timestamps, download history.")
    print()
    db_choice = input("Enable database? [Y/n]: ").strip().lower()
    db_enabled = db_choice != "n"
    db_path = None
    if db_enabled:
        default_db = os.path.join(os.path.dirname(os.path.abspath(config.path)), "tg-vault.db")
        db_input = input(f"Database path [default: {default_db}]: ").strip()
        if db_input:
            db_path = os.path.expanduser(db_input)
        else:
            db_path = default_db
        print(f"   Database will be created at: {db_path}")

    # Save
    config.bots = [{"token": token, "username": username}]
    config.main_channel = main_channel
    config.temp_channel = temp_channel
    config.db_enabled = db_enabled
    config.db_path = db_path
    config.save()

    print("\n" + "=" * 55)
    print("✅ Configuration saved!")
    print(f"   Config file: {config.path}")
    print(f"   Bot: @{username}")
    print(f"   Main channel: {main_channel}")
    print(f"   Temp channel: {temp_channel}")
    if db_enabled:
        print(f"   Database: {db_path}")
    print("=" * 55)

    # Test
    print("\n🧪 Running final connectivity test...")
    cmd_test(None, config)

    print("\n💡 You're ready! Try:")
    print(f"   python tg.py upload some-file.zip --desc 'My first upload' --tag test")


def cmd_bots(args, config):
    if args.bots_action == "add":
        token = args.token
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
            if not r.get("ok"):
                print(f"❌ Invalid token: {r.get('description')}")
                return
            username = r["result"].get("username", "")
            bot_id = r["result"]["id"]
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return

        # Check duplicate
        for b in config.bots:
            if b["token"] == token:
                print("⚠️ This bot is already added.")
                return

        config.bots.append({"token": token, "username": username})
        config.save()
        print(f"✅ Bot added: @{username} (id: {bot_id})")

    elif args.bots_action == "list":
        if not config.bots:
            print("❌ No bots added yet.")
            return
        print(f"📋 Bots ({len(config.bots)}):")
        for i, b in enumerate(config.bots, 1):
            print(f"   {i}. @{b.get('username', '?')} | token: {b['token'][:15]}...")

    elif args.bots_action == "remove":
        if not config.bots:
            print("❌ No bots to remove.")
            return
        idx = args.index - 1
        if 0 <= idx < len(config.bots):
            removed = config.bots.pop(idx)
            config.save()
            print(f"✅ Removed: @{removed.get('username', '?')}")
        else:
            print(f"❌ Invalid index. Use 1 to {len(config.bots)}")


def cmd_channels(args, config):
    if args.channels_action == "set":
        if args.name == "main":
            config.main_channel = args.value
            if not config.temp_channel:
                config.temp_channel = args.value
            config.save()
            print(f"✅ Main channel set: {args.value}")
            print(f"   Temp channel: {config.temp_channel}")
        elif args.name == "temp":
            config.temp_channel = args.value
            config.save()
            print(f"✅ Temp channel set: {args.value}")
        else:
            print("❌ name must be 'main' or 'temp'")

    elif args.channels_action == "show":
        print("📋 Channels:")
        print(f"   main: {config.main_channel or '(not set)'}")
        print(f"   temp: {config.temp_channel or '(not set)'}")


def cmd_upload(args, config):
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    hashtags = []
    if args.tag:
        raw = [t.strip() for t in args.tag.split(",") if t.strip()]
        hashtags = sanitize_hashtags(raw)
        if len(hashtags) != len(raw):
            print(f"⚠️ Some hashtags were sanitized (invalid chars removed/replaced)")
            print(f"   Original: {raw}")
            print(f"   Sanitized: {hashtags}")

    db = config.get_db()
    if db:
        print(f"💾 Database: {config.get_db_path()}")

    # Encryption
    encrypt = getattr(args, "encrypt", False)
    password = getattr(args, "password", None) or os.environ.get("TG_VAULT_PASSWORD")
    if encrypt and not password:
        import getpass
        print("🔐 Encryption enabled. Enter a password.")
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("❌ Passwords don't match.")
            return

    compress = not getattr(args, "no_compress", False)

    # Collect file list (supports glob expansion and multiple files)
    files = list(args.files)
    if not files:
        print("❌ No files specified.")
        return

    uploader = Uploader(config, bot_pool, db=db)

    # Bulk upload
    results = []
    total = len(files)
    for i, file_path in enumerate(files, 1):
        print(f"\n{'=' * 60}")
        print(f"📤 Uploading file {i}/{total}: {file_path}")
        print(f"{'=' * 60}")
        result = uploader.upload(
            file_path,
            description=args.desc or "",
            hashtags=hashtags,
            resume=args.resume,
            encrypt=encrypt,
            password=password,
            compress=compress,
        )
        results.append((file_path, result))

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 Bulk upload summary ({total} files):")
    print(f"{'=' * 60}")
    success_count = 0
    for file_path, result in results:
        if result and result.get("share_link"):
            success_count += 1
            print(f"  ✅ {os.path.basename(file_path)}: {result['share_link']}")
        else:
            print(f"  ❌ {file_path}: failed")
    print(f"\n{success_count}/{total} files uploaded successfully.")
    if success_count > 0:
        print(f"\n💡 To download any file:")
        print(f"   python tg.py download \"<link>\"")


def cmd_download(args, config):
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    db = config.get_db()
    if db:
        print(f"💾 Database: {config.get_db_path()}")

    # Collect links (supports multiple links and --links-file)
    links = list(args.links)
    if args.links_file:
        try:
            with open(args.links_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        links.append(line)
        except OSError as e:
            print(f"❌ Cannot read links file: {e}")
            sys.exit(1)

    if not links:
        print("❌ No links specified.")
        sys.exit(1)

    downloader = Downloader(config, bot_pool, db=db)

    # Bulk download
    total = len(links)
    success_count = 0
    for i, link in enumerate(links, 1):
        print(f"\n{'=' * 60}")
        print(f"📥 Downloading file {i}/{total}: {link}")
        print(f"{'=' * 60}")
        try:
            success = downloader.download(
                link,
                resume=args.resume,
                output=args.output if total == 1 else None,  # only allow --output for single file
                output_dir=args.output_dir,
                password=getattr(args, "password", None),
            )
            if success:
                success_count += 1
        except ValueError as e:
            print(f"❌ {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 Bulk download summary ({total} files):")
    print(f"{'=' * 60}")
    print(f"{success_count}/{total} files downloaded successfully.")
    sys.exit(0 if success_count == total else 1)


def cmd_info(args, config):
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return

    bot_pool = BotPool(config.bots)
    downloader = Downloader(config, bot_pool)
    try:
        downloader.info(args.link)
    except ValueError as e:
        print(f"❌ {e}")


def cmd_test(args, config):
    """Test connectivity for all bots and channels."""
    print("🧪 Testing connectivity...\n")

    if not config.bots:
        print("❌ No bots configured.")
        return

    bot_pool = BotPool(config.bots)
    print(f"📊 Bots: {len(bot_pool)}")
    for b in bot_pool.list_bots():
        status = "✅" if b.id else "❌"
        print(f"   {status} @{b.username} (id: {b.id})")

    if not config.main_channel:
        print("\n❌ Main channel not set.")
        return

    print(f"\n📡 Testing channels:")
    channels = {"main": config.main_channel}
    if config.temp_channel != config.main_channel:
        channels["temp"] = config.temp_channel

    for name, ch_id in channels.items():
        bot = bot_pool.get_next()
        res = bot.request("getChat", data={"chat_id": ch_id})
        if res and res.get("ok"):
            chat = res["result"]
            print(f"   ✅ {name}: {chat.get('title', '?')} ({ch_id})")
            # Check each bot's permissions
            for b in bot_pool.list_bots():
                if not b.id:
                    continue
                mres = b.request("getChatMember", data={
                    "chat_id": ch_id, "user_id": b.id
                })
                if mres and mres.get("ok"):
                    status = mres["result"]["status"]
                    can_post = mres["result"].get("can_post_messages",
                                                   status == "administrator")
                    can_delete = mres["result"].get("can_delete_messages",
                                                    status == "administrator")
                    icon = "✅" if (can_post and can_delete) else "⚠️"
                    print(f"      {icon} @{b.username}: "
                          f"status={status}, post={can_post}, delete={can_delete}")
                else:
                    print(f"      ❌ @{b.username}: no access")
        else:
            err = res.get("description") if res else "No response"
            print(f"   ❌ {name}: {err}")


def cmd_ls(args, config):
    """List recent files in main channel (using forwardMessage trick)."""
    if not config.main_channel:
        print("❌ Main channel not set.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    # Send a marker message, then inspect messages backward
    # (Telegram Bot API doesn't have getHistory, so this is the workaround)
    bot = bot_pool.get_next()
    marker_res = bot.request("sendMessage", data={
        "chat_id": config.main_channel,
        "text": "_ls_marker_",
        "disable_web_page_preview": True,
        "disable_notification": True,
    })
    if not marker_res or not marker_res.get("ok"):
        print("❌ Cannot send marker message.")
        return
    marker_id = marker_res["result"]["message_id"]

    # Delete the marker immediately
    bot.request("deleteMessage", data={
        "chat_id": config.main_channel,
        "message_id": marker_id,
    })

    # Inspect previous messages by forwarding them one-by-one to temp channel
    print(f"\n📋 Recent files in main channel (scanning last {args.limit} messages):\n")
    found = 0
    for msg_id in range(marker_id - 1, max(0, marker_id - args.limit - 1), -1):
        if found >= args.limit:
            break
        bot = bot_pool.get_next()
        res = bot.request("forwardMessage", data={
            "chat_id": config.temp_channel,
            "from_chat_id": config.main_channel,
            "message_id": msg_id,
            "disable_notification": True,
        })
        if not res or not res.get("ok"):
            continue
        msg = res["result"]
        # Delete the forwarded copy
        bot.request("deleteMessage", data={
            "chat_id": config.temp_channel,
            "message_id": msg["message_id"],
        })

        caption = msg.get("caption", "") or msg.get("text", "")
        if caption.startswith(MANIFEST_PREFIX):
            try:
                parts = caption.split("|")
                fname = parts[1]
                total_parts = int(parts[2])
                hash_prefix = parts[3]
                link = build_share_link(config.main_channel, msg_id)
                print(f"  📄 {fname} ({total_parts} parts) | {hash_prefix}...")
                print(f"     🔗 {link}")
                found += 1
            except (IndexError, ValueError):
                pass
    if found == 0:
        print("  (no manifest files found in recent messages)")


def cmd_delete(args, config):
    """Delete a file's messages (description, parts, manifest) from channel."""
    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    try:
        chat_id, message_id = parse_telegram_link(args.link)
    except ValueError as e:
        print(f"❌ {e}")
        return

    print(f"🌐 Fetching manifest at {args.link}...")
    downloader = Downloader(config, bot_pool)
    manifest = downloader._fetch_manifest(chat_id, message_id)
    if not manifest:
        print("❌ Could not fetch manifest.")
        return
    downloader._cleanup()

    msg_ids = []
    if manifest.get("description_msg_id"):
        msg_ids.append(manifest["description_msg_id"])
    msg_ids.extend(manifest.get("message_ids", []))
    msg_ids.append(message_id)  # manifest itself

    print(f"🗑️ Deleting {len(msg_ids)} messages...")
    if not args.force:
        confirm = input(f"   Type 'yes' to confirm deletion of {len(msg_ids)} messages: ")
        if confirm.strip().lower() != "yes":
            print("Cancelled.")
            return

    deleted = 0
    for mid in msg_ids:
        bot = bot_pool.get_next()
        res = bot.request("deleteMessage", data={
            "chat_id": manifest["channel_id"],
            "message_id": mid,
        })
        if res and res.get("ok"):
            deleted += 1
        else:
            err = res.get("description") if res else "No response"
            print(f"   ⚠️ Failed to delete {mid}: {err}")
    print(f"✅ Deleted {deleted}/{len(msg_ids)} messages.")


def cmd_cleanup(args, config):
    """Clean up temp channel by deleting recent messages."""
    if not config.temp_channel:
        print("❌ Temp channel not set.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    bot = bot_pool.get_next()
    print(f"🧹 Cleaning temp channel ({config.temp_channel})...")

    # Send marker
    test_res = bot.request("sendMessage", data={
        "chat_id": config.temp_channel,
        "text": "_cleanup_marker_",
        "disable_notification": True,
    })
    if not test_res or not test_res.get("ok"):
        print("❌ Cannot send marker.")
        return
    test_msg_id = test_res["result"]["message_id"]
    print(f"   Marker at: {test_msg_id}")

    count = 0
    for msg_id in range(test_msg_id, max(0, test_msg_id - args.max_count), -1):
        res = bot.request("deleteMessage", data={
            "chat_id": config.temp_channel,
            "message_id": msg_id,
        })
        if res and res.get("ok"):
            count += 1

    print(f"✅ Deleted {count} messages.")


# ==========================================
# Database commands
# ==========================================
def cmd_db(args, config):
    """Database management commands."""
    # Special case: 'enable' doesn't require DB to be already enabled
    if args.db_action == "enable":
        config.db_enabled = True
        if not config.db_path:
            config.db_path = config.get_db_path()
        config.save()
        # Initialize the database file
        if Database:
            Database(config.get_db_path())
            print(f"✅ Database enabled: {config.get_db_path()}")
        else:
            print("❌ Database module not available (tg_db.py missing)")
        return

    # All other actions require DB to be enabled
    db = config.get_db()
    if db is None:
        print("❌ Database is not enabled.")
        print("   Run: python tg.py db enable")
        return

    elif args.db_action == "disable":
        config.db_enabled = False
        config.save()
        print("✅ Database disabled. (File kept on disk.)")
        print(f"   To re-enable: python tg.py db enable")
        return

    elif args.db_action == "info":
        path = config.get_db_path()
        if not os.path.exists(path):
            print(f"❌ Database file does not exist yet: {path}")
            print("   It will be created automatically on first upload.")
            return
        size = os.path.getsize(path)
        print(f"📍 Database: {path}")
        print(f"   Size: {format_size(size)}")
        print(f"   Enabled: {'yes' if config.db_enabled else 'no'}")
        stats = db.stats()
        print(f"\n📊 Stats:")
        print(f"   Files: {stats['total_files']}")
        print(f"   Total size: {format_size(stats['total_size'])}")
        print(f"   Total downloads: {stats['total_downloads']}")
        if stats["top_files"]:
            print(f"\n   Top downloaded files:")
            for f in stats["top_files"]:
                print(f"     • {f['name']} ({format_size(f['size'])}) — {f['dl_count']} downloads")

    elif args.db_action == "list":
        limit = args.limit or 50
        rows = db.list_files(limit=limit, status="uploaded")
        if not rows:
            print("No files in database.")
            return
        print(f"📋 Files in database ({len(rows)} shown):")
        print(f"{'─' * 80}")
        print(f"{'ID':<4} {'Name':<30} {'Size':<10} {'Parts':<6} {'Date':<20} {'Link'}")
        print(f"{'─' * 80}")
        for r in rows:
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            name = r["name"][:30]
            link = r["share_link"] or ""
            print(f"{r['id']:<4} {name:<30} {format_size(r['size']):<10} {r['total_parts']:<6} {date:<20} {link}")

    elif args.db_action == "search":
        # Support both positional and --query
        query = args.query or getattr(args, "query_opt", None)
        if not query:
            print("❌ Search query required: python tg.py db search <query>")
            return
        rows = db.search_files(query)
        if not rows:
            print(f"No files matching '{query}'.")
            return
        print(f"🔍 Search results for '{query}' ({len(rows)} found):")
        print(f"{'─' * 80}")
        for r in rows:
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            print(f"  #{r['id']}  {r['name']}  ({format_size(r['size'])})  {date}")
            if r["description"]:
                print(f"         {r['description'][:80]}")
            if r["share_link"]:
                print(f"         🔗 {r['share_link']}")

    elif args.db_action == "stats":
        stats = db.stats()
        print("📊 Database statistics:")
        print(f"   Total files: {stats['total_files']}")
        print(f"   Total size:  {format_size(stats['total_size'])}")
        print(f"   Total downloads: {stats['total_downloads']}")
        if stats["top_files"]:
            print(f"\n   Top downloaded files:")
            for f in stats["top_files"]:
                print(f"     • {f['name']} ({format_size(f['size'])}) — {f['dl_count']} downloads")

    elif args.db_action == "export":
        if not args.output:
            default = "tg-vault-export.json"
            args.output = default
        n = db.export_json(args.output)
        print(f"✅ Exported {n} records to {args.output}")


# ==========================================
# Interactive Menu
# ==========================================
def interactive_menu(config_path):
    config = Config.load(config_path)

    while True:
        print("\n" + "=" * 55)
        print("    tg-vault — Telegram Cloud Storage")
        print("=" * 55)
        print(f"   bots: {len(config.bots)} | channel: {config.main_channel or '?'}")
        print(f"   db: {'✅' if config.db_enabled else '❌'}")
        print("=" * 55)
        print("1. Upload file(s)")
        print("2. Upload file (resume)")
        print("3. Download by link(s)")
        print("4. Show file info")
        print("5. List recent files")
        print("6. Delete a file")
        print("7. Setup wizard (bot + channels + db)")
        print("8. Add bot")
        print("9. Set channel")
        print("10. Test connectivity")
        print("11. Cleanup temp channel")
        print("12. Database: list/search/stats")
        print("13. Exit")

        choice = input("\nChoice: ").strip()

        if choice == "1":
            paths_raw = input("File path(s) — space-separated for bulk: ").strip()
            if not paths_raw:
                continue
            # Split by space, strip quotes
            import shlex
            try:
                paths = shlex.split(paths_raw)
            except ValueError:
                paths = paths_raw.split()
            desc = input("Description (optional): ").strip()
            tags = input("Hashtags (comma-separated, optional): ").strip()
            raw_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            hashtags = sanitize_hashtags(raw_tags) if raw_tags else []
            if raw_tags and hashtags != raw_tags:
                print(f"⚠️ Hashtags sanitized: {raw_tags} → {hashtags}")
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            db = config.get_db()
            uploader = Uploader(config, bot_pool, db=db)
            args_mock = argparse.Namespace(
                files=paths, desc=desc, tag=tags, resume=False
            )
            cmd_upload(args_mock, config)

        elif choice == "2":
            path = input("File path: ").strip().strip('"').strip("'")
            if not path:
                continue
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            db = config.get_db()
            uploader = Uploader(config, bot_pool, db=db)
            uploader.upload(path, resume=True)

        elif choice == "3":
            links_raw = input("Manifest link(s) — space-separated for bulk: ").strip()
            if not links_raw:
                continue
            import shlex
            try:
                links = shlex.split(links_raw)
            except ValueError:
                links = links_raw.split()
            output_dir = input("Output directory (default: .): ").strip() or "."
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            db = config.get_db()
            downloader = Downloader(config, bot_pool, db=db)
            for i, link in enumerate(links, 1):
                if len(links) > 1:
                    print(f"\n{'=' * 60}")
                    print(f"📥 Downloading file {i}/{len(links)}: {link}")
                    print(f"{'=' * 60}")
                try:
                    downloader.download(link, resume=True, output_dir=output_dir)
                except ValueError as e:
                    print(f"❌ {e}")

        elif choice == "4":
            link = input("Manifest link: ").strip()
            if not link:
                continue
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            downloader = Downloader(config, bot_pool)
            try:
                downloader.info(link)
            except ValueError as e:
                print(f"❌ {e}")

        elif choice == "5":
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            cmd_ls(argparse.Namespace(limit=20), config)

        elif choice == "6":
            link = input("Manifest link: ").strip()
            if not link:
                continue
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            cmd_delete(argparse.Namespace(link=link, force=False), config)

        elif choice == "7":
            cmd_setup(None, config)

        elif choice == "8":
            token = input("Bot token: ").strip()
            if not token:
                continue
            cmd_bots(argparse.Namespace(bots_action="add", token=token), config)

        elif choice == "9":
            print("1. Main channel")
            print("2. Temp channel")
            sub = input("Choice: ").strip()
            value = input("Channel ID: ").strip()
            name = "main" if sub == "1" else "temp"
            cmd_channels(argparse.Namespace(channels_action="set", name=name, value=value), config)

        elif choice == "10":
            cmd_test(None, config)

        elif choice == "11":
            count = input("Max messages to delete (default 100): ").strip()
            try:
                count = int(count) if count else 100
            except ValueError:
                count = 100
            cmd_cleanup(argparse.Namespace(max_count=count), config)

        elif choice == "12":
            # Database submenu
            if not config.db_enabled:
                print("❌ Database not enabled.")
                enable = input("Enable now? (y/N): ").strip().lower()
                if enable == "y":
                    cmd_db(argparse.Namespace(db_action="enable", query=None, limit=50, output=None), config)
                continue
            print("\n--- Database ---")
            print("a. List files")
            print("b. Search")
            print("c. Stats")
            print("d. Info")
            print("e. Export to JSON")
            sub = input("Choice: ").strip().lower()
            if sub == "a":
                limit = input("Limit (default 50): ").strip()
                try:
                    limit = int(limit) if limit else 50
                except ValueError:
                    limit = 50
                cmd_db(argparse.Namespace(db_action="list", query=None, limit=limit, output=None), config)
            elif sub == "b":
                q = input("Search query: ").strip()
                cmd_db(argparse.Namespace(db_action="search", query=q, limit=50, output=None), config)
            elif sub == "c":
                cmd_db(argparse.Namespace(db_action="stats", query=None, limit=50, output=None), config)
            elif sub == "d":
                cmd_db(argparse.Namespace(db_action="info", query=None, limit=50, output=None), config)
            elif sub == "e":
                out = input("Output file (default: tg-vault-export.json): ").strip() or "tg-vault-export.json"
                cmd_db(argparse.Namespace(db_action="export", query=None, limit=50, output=out), config)

        elif choice == "13":
            print("Goodbye!")
            break
        else:
            print("❌ Invalid choice")


# ==========================================
# Main
# ==========================================
def main():
    parser = argparse.ArgumentParser(
        prog="tg-vault",
        description="tg-vault — Telegram Bot API cloud storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tg.py init
  tg.py setup                                      # interactive wizard (recommended)
  tg.py bots add 123456:ABC-DEF...
  tg.py channels set main -1001234567890
  tg.py channels set temp -1009876543210
  tg.py test

  # Single file
  tg.py upload movie.mp4 --desc "Backup" --tag movies,2026
  tg.py download https://t.me/c/1234567890/42

  # Bulk upload (multiple files)
  tg.py upload file1.zip file2.zip file3.zip --desc "Backup batch"
  tg.py upload *.mp4 --tag movies

  # Bulk download (multiple links)
  tg.py download https://t.me/c/.../42 https://t.me/c/.../43 https://t.me/c/.../44
  tg.py download --links-file my_links.txt --output-dir ~/Downloads

  # Database
  tg.py db enable                                  # enable DB
  tg.py db info                                    # show DB info + stats
  tg.py db list --limit 20                         # list recent files
  tg.py db search "movie"                          # search by name/desc/tags
  tg.py db stats                                   # show statistics only
  tg.py db export -o backup.json                   # export all records

  # Other
  tg.py info    https://t.me/c/1234567890/42
  tg.py ls      --limit 10
  tg.py delete  https://t.me/c/1234567890/42 --force
  tg.py cleanup --max-count 100
        """
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--version", action="version",
                        version=f"tg-vault v{VERSION}")

    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="Create a sample config file")

    # setup
    subparsers.add_parser("setup", help="Interactive setup wizard (bot + channels)")

    # bots
    sp = subparsers.add_parser("bots", help="Manage bots")
    sp.add_argument("bots_action", choices=["add", "list", "remove"])
    sp.add_argument("token", nargs="?", help="Bot token (for add)")
    sp.add_argument("index", nargs="?", type=int, help="Bot index (for remove)")

    # channels
    sp = subparsers.add_parser("channels", help="Manage channels")
    sp.add_argument("channels_action", choices=["set", "show"])
    sp.add_argument("name", nargs="?", choices=["main", "temp"])
    sp.add_argument("value", nargs="?")

    # upload — supports multiple files for bulk upload
    sp = subparsers.add_parser("upload", help="Upload one or more files (bulk upload supported)")
    sp.add_argument("files", nargs="+", help="One or more file paths (supports wildcards)")
    sp.add_argument("--desc", "-d", help="Description text (applied to all files)")
    sp.add_argument("--tag", "-t", help="Hashtags (comma-separated, applied to all files)")
    sp.add_argument("--resume", "-r", action="store_true", help="Resume interrupted upload")
    sp.add_argument("--encrypt", "-e", action="store_true",
                    help="Encrypt chunks with AES-256-GCM (requires --password or TG_VAULT_PASSWORD env var)")
    sp.add_argument("--password", help="Password for encryption (or set TG_VAULT_PASSWORD env var)")
    sp.add_argument("--no-compress", action="store_true",
                    help="Disable gzip compression (compression is on by default)")

    # download — supports multiple links for bulk download
    sp = subparsers.add_parser("download", help="Download one or more files (bulk download supported)")
    sp.add_argument("links", nargs="+", help="One or more manifest links")
    sp.add_argument("--links-file", "-f", help="Text file containing one link per line (in addition to CLI args)")
    sp.add_argument("--resume", "-r", action="store_true", help="Resume interrupted download")
    sp.add_argument("--output", "-o", help="Output filename (only valid for single-file download)")
    sp.add_argument("--output-dir", default=".", help="Output directory (default: .)")
    sp.add_argument("--password", help="Password for decryption (or set TG_VAULT_PASSWORD env var)")

    # info
    sp = subparsers.add_parser("info", help="Show manifest info without downloading")
    sp.add_argument("link", help="Manifest message link")

    # test
    subparsers.add_parser("test", help="Test connectivity")

    # ls
    sp = subparsers.add_parser("ls", help="List recent manifest files in main channel")
    sp.add_argument("--limit", type=int, default=10, help="Max results (default 10)")

    # delete
    sp = subparsers.add_parser("delete", help="Delete a file from channel")
    sp.add_argument("link", help="Manifest message link")
    sp.add_argument("--force", action="store_true", help="Skip confirmation")

    # cleanup
    sp = subparsers.add_parser("cleanup", help="Clean up temp channel")
    sp.add_argument("--max-count", type=int, default=100)

    # db — database management
    sp = subparsers.add_parser("db", help="Database management")
    sp.add_argument("db_action", choices=["enable", "disable", "info", "list", "search", "stats", "export"],
                    help="Action to perform")
    sp.add_argument("query", nargs="?", help="Search query (for 'search')")
    sp.add_argument("--query", "-q", dest="query_opt", help="Search query (alternative, for 'search')")
    sp.add_argument("--limit", type=int, default=50, help="Max results (for 'list')")
    sp.add_argument("--output", "-o", help="Output file (for 'export')")

    args = parser.parse_args()

    # No command → interactive menu
    if not args.command:
        interactive_menu(args.config)
        return

    if args.command == "init":
        cmd_init(args, args.config)
        return

    config = Config.load(args.config)

    if args.command == "setup":
        cmd_setup(args, config)
    elif args.command == "bots":
        cmd_bots(args, config)
    elif args.command == "channels":
        cmd_channels(args, config)
    elif args.command == "upload":
        cmd_upload(args, config)
    elif args.command == "download":
        cmd_download(args, config)
    elif args.command == "info":
        cmd_info(args, config)
    elif args.command == "test":
        cmd_test(args, config)
    elif args.command == "ls":
        cmd_ls(args, config)
    elif args.command == "delete":
        cmd_delete(args, config)
    elif args.command == "cleanup":
        cmd_cleanup(args, config)
    elif args.command == "db":
        cmd_db(args, config)
    else:
        parser.print_help()


def _install_signal_handlers():
    """Install global signal handlers for graceful shutdown.

    Inspired by TAS — prevents silent crashes and ensures temp messages
    are cleaned up on Ctrl+C / SIGTERM.
    """
    import signal

    def sigint_handler(signum, frame):
        print("\n\n⚠️ Interrupted by user (Ctrl+C).")
        sys.exit(130)

    def sigterm_handler(signum, frame):
        print("\n⚠️ Received SIGTERM. Shutting down.")
        sys.exit(143)

    try:
        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)
    except (ValueError, AttributeError):
        # On Windows, SIGTERM may not be available
        pass


if __name__ == "__main__":
    _install_signal_handlers()
    main()
