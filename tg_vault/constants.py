"""
Constants and Telegram API limits for tg-vault.
"""

import os
from pathlib import Path

VERSION = 8

# Config path priority:
#   1. ~/.tg-vault.json (default, created by `tg.py setup`)
#   2. config.json (in the same directory as tg.py, useful for portable setups)
# The first existing file is used.
_LOCAL_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))),
    "config.json",
)
if os.path.exists(_LOCAL_CONFIG):
    DEFAULT_CONFIG_PATH = _LOCAL_CONFIG
else:
    DEFAULT_CONFIG_PATH = str(Path.home() / ".tg-vault.json")

# Message prefixes (used to identify manifest / description messages)
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
