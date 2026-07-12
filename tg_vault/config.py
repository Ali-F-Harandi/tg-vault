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
        # Multi-channel support: a list of additional storage channels.
        # The main_channel is always the first/default.
        # All channels share the same database and orphan table.
        self.storage_channels = list(channels.get("storage", []) or [])
        # Ensure main_channel is always in the list
        if self.main_channel and self.main_channel not in self.storage_channels:
            self.storage_channels.insert(0, self.main_channel)
        self.chunk_size = int(data.get("chunk_size_mb", DEFAULT_CHUNK_MB)) * 1024 * 1024
        self.upload_delay = float(data.get("upload_delay", DEFAULT_UPLOAD_DELAY))
        self.download_delay = float(data.get("download_delay", DEFAULT_DOWNLOAD_DELAY))
        self.parallel_workers = int(data.get("parallel_workers", DEFAULT_PARALLEL_WORKERS))
        # Default manifest type: 'text' (editable), 'file' (not editable), 'auto' (text if fits)
        self.default_manifest_type = data.get("default_manifest_type", "text")
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
        # Build the storage list (excluding main, which is stored separately)
        storage_list = [ch for ch in self.storage_channels if ch != self.main_channel]
        data = {
            "bots": self.bots,
            "channels": {
                "main": self.main_channel,
                "temp": self.temp_channel if self.temp_channel != self.main_channel else None,
                "storage": storage_list if storage_list else None,
            },
            "chunk_size_mb": self.chunk_size // (1024 * 1024),
            "upload_delay": self.upload_delay,
            "download_delay": self.download_delay,
            "parallel_workers": self.parallel_workers,
            "default_manifest_type": self.default_manifest_type,
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

    # ─────────────── Multi-channel helpers ───────────────

    def get_all_storage_channels(self):
        """Return a list of all storage channel IDs.

        Always includes main_channel as the first element, followed by
        any additional channels in self.storage_channels.
        """
        channels = []
        if self.main_channel:
            channels.append(self.main_channel)
        for ch in self.storage_channels:
            if ch != self.main_channel and ch not in channels:
                channels.append(ch)
        return channels

    def is_storage_channel(self, channel_id):
        """Check if a channel ID is in the storage channels list."""
        return channel_id in self.get_all_storage_channels()

    def add_storage_channel(self, channel_id):
        """Add a channel to the storage list. Returns True if added."""
        if channel_id in self.storage_channels:
            return False
        self.storage_channels.append(channel_id)
        return True

    def remove_storage_channel(self, channel_id):
        """Remove a channel from the storage list. Returns True if removed.

        Note: main_channel cannot be removed (it's the default).
        """
        if channel_id == self.main_channel:
            return False
        if channel_id not in self.storage_channels:
            return False
        self.storage_channels.remove(channel_id)
        return True

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
