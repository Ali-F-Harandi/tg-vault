"""
tg-vault — Telegram Bot API cloud storage
=========================================
Use Telegram as a personal cloud storage backend using ONLY Bot API tokens.
No phone number, no api_id/api_hash, no MTProto/Telethon/Pyrogram required.

This is the main package. For the CLI entry point, see :mod:`tg_vault.cli`.
"""

from .constants import (
    VERSION,
    MANIFEST_PREFIX,
    DESCRIPTION_PREFIX,
    TG_FILE_SIZE_LIMIT,
    TG_UPLOAD_SIZE_LIMIT,
    TG_CAPTION_MAX,
    TG_TEXT_MAX,
    TG_FILENAME_MAX,
    BOT_MIN_INTERVAL,
    MAX_RETRIES,
    BASE_RETRY_DELAY,
    DEFAULT_CHUNK_MB,
    DEFAULT_UPLOAD_DELAY,
    DEFAULT_DOWNLOAD_DELAY,
    DEFAULT_PARALLEL_WORKERS,
    DEFAULT_CONFIG_PATH,
)
from .utils import (
    compute_sha256,
    format_size,
    format_speed,
    format_eta,
    sanitize_filename,
    sanitize_hashtag,
    sanitize_hashtags,
    truncate_caption,
    truncate_text,
    parse_telegram_link,
    build_share_link,
    ProgressTracker,
)
from .config import Config
from .bot_pool import Bot, BotPool
from .uploader import Uploader
from .downloader import Downloader
from .db import Database
from .crypto import Encryptor, is_encryption_available
from .compression import (
    compress_file,
    decompress_file,
    compress_data,
    decompress_data,
    should_skip_compression,
)
from .chunk_header import (
    create_header as chunk_create_header,
    parse_header as chunk_parse_header,
    is_chunk_with_header,
    HEADER_SIZE as CHUNK_HEADER_SIZE,
    FLAG_COMPRESSED,
    FLAG_ENCRYPTED,
)
from .db_sync import (
    auto_sync_db,
    sync_db_to_channel,
    restore_db_from_channel,
    find_latest_db_backup,
)
from .orphan_scanner import (
    scan_orphans,
    delete_orphan_from_telegram,
)

__version__ = VERSION
__author__ = "kesafatkari"
__license__ = "MIT"

__all__ = [
    # Constants
    "VERSION", "MANIFEST_PREFIX", "DESCRIPTION_PREFIX",
    "TG_FILE_SIZE_LIMIT", "TG_UPLOAD_SIZE_LIMIT",
    "TG_CAPTION_MAX", "TG_TEXT_MAX", "TG_FILENAME_MAX",
    "BOT_MIN_INTERVAL", "MAX_RETRIES", "BASE_RETRY_DELAY",
    "DEFAULT_CHUNK_MB", "DEFAULT_UPLOAD_DELAY", "DEFAULT_DOWNLOAD_DELAY",
    "DEFAULT_PARALLEL_WORKERS", "DEFAULT_CONFIG_PATH",
    # Utils
    "compute_sha256", "format_size", "format_speed", "format_eta",
    "sanitize_filename", "sanitize_hashtag", "sanitize_hashtags",
    "truncate_caption", "truncate_text",
    "parse_telegram_link", "build_share_link", "ProgressTracker",
    # Core classes
    "Config", "Bot", "BotPool", "Uploader", "Downloader", "Database",
    # Optional features
    "Encryptor", "is_encryption_available",
    "compress_file", "decompress_file", "compress_data", "decompress_data",
    "should_skip_compression",
    "chunk_create_header", "chunk_parse_header", "is_chunk_with_header",
    "CHUNK_HEADER_SIZE", "FLAG_COMPRESSED", "FLAG_ENCRYPTED",
    # DB sync + orphans
    "auto_sync_db", "sync_db_to_channel", "restore_db_from_channel",
    "find_latest_db_backup",
    "scan_orphans", "delete_orphan_from_telegram",
]
