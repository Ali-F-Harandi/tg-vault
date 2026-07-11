#!/usr/bin/env python3
"""
tg-vault — backward-compatibility shim
======================================
This file allows the legacy invocation::

    python tg.py upload file.zip
    python tg.py download https://t.me/c/.../42
    python tg.py setup

to keep working after the code was reorganized into the ``tg_vault`` package.
All real logic now lives in ``tg_vault/`` (see :mod:`tg_vault.cli`).

New code should prefer::

    python -m tg_vault upload file.zip

or, after ``pip install .``::

    tg-vault upload file.zip
"""

# Re-export everything from the package so that `import tg` still works
# (e.g. the GUI imports `tg` as a module).
from tg_vault import *  # noqa: F401, F403
from tg_vault.constants import *  # noqa: F401, F403
from tg_vault.utils import *  # noqa: F401, F403
from tg_vault.config import Config
from tg_vault.bot_pool import Bot, BotPool
from tg_vault.uploader import Uploader
from tg_vault.downloader import Downloader
from tg_vault.db import Database
from tg_vault.crypto import Encryptor, is_encryption_available
from tg_vault.compression import (
    compress_file, decompress_file, compress_data, decompress_data,
    should_skip_compression,
)
from tg_vault.chunk_header import (
    create_header as chunk_create_header,
    parse_header as chunk_parse_header,
    is_chunk_with_header,
    HEADER_SIZE as CHUNK_HEADER_SIZE,
    FLAG_COMPRESSED, FLAG_ENCRYPTED,
)
from tg_vault.db_sync import (
    sync_db_to_channel, restore_db_from_channel, find_latest_db_backup,
    auto_sync_db,
)
from tg_vault.commands import (
    cmd_init, cmd_setup, cmd_bots, cmd_channels,
    cmd_upload, cmd_download, cmd_info, cmd_test, cmd_ls, cmd_delete, cmd_cleanup,
    cmd_db, _build_filters_from_args, _db_download,
)
from tg_vault.interactive import interactive_menu, install_signal_handlers
from tg_vault.cli import main, build_parser

# Also expose requests (some external scripts may have relied on it being importable)
import requests  # noqa: F401

if __name__ == "__main__":
    install_signal_handlers()
    main()
