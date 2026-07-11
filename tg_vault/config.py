"""
Configuration management for tg-vault.

The config file is a JSON file at ``~/.tg-vault.json`` (or ``config.json`` next
to the package for portable setups). It stores:

  - ``bots``: list of ``{token, username}`` dicts
  - ``channels``: ``{main, temp}`` channel IDs (temp defaults to main)
  - ``chunk_size_mb``: chunk size (default 19, under the 20MB download limit)
  - ``upload_delay``, ``download_delay``: inter-request delays
  - ``parallel_workers``: parallel download chunk count
  - ``db_enabled``, ``db_path``, ``db_sync_*``: database options
"""

import json
import os
from pathlib import Path

from .constants import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CHUNK_MB,
    DEFAULT_UPLOAD_DELAY,
    DEFAULT_DOWNLOAD_DELAY,
    DEFAULT_PARALLEL_WORKERS,
    TG_FILE_SIZE_LIMIT,
    VERSION,
)


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
        self.db_sync_channel = data.get("db_sync_channel")  # channel to sync DB file to
        self.db_sync_msg_id = data.get("db_sync_msg_id")  # message ID of last DB sync
        self.db_sync_multipart = bool(data.get("db_sync_multipart", False))
        self.db_auto_sync = bool(data.get("db_auto_sync", True))  # auto-sync DB after every change

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
            "db_sync_channel": self.db_sync_channel,
            "db_sync_msg_id": self.db_sync_msg_id,
            "db_sync_multipart": self.db_sync_multipart,
            "db_auto_sync": self.db_auto_sync,
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
            errors.append(
                f"chunk_size_mb too large (max {TG_FILE_SIZE_LIMIT // (1024*1024)} MB)"
            )
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

    def get_db_sync_channel(self):
        """Channel where the DB file itself is synced (for backup).
        Defaults to temp_channel if not explicitly set.
        """
        return self.db_sync_channel or self.temp_channel

    def get_db(self):
        """Return a Database instance if enabled, else None."""
        if not self.db_enabled:
            return None
        # Import here to avoid circular import when tg_db is missing
        try:
            from .db import Database
        except ImportError:
            return None
        return Database(self.get_db_path())
