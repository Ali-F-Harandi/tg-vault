"""
Smoke tests for tg-vault.

These tests verify that:
  - All package modules import without errors
  - Public API symbols are exported
  - Helper functions behave correctly
  - The CLI parser builds without errors
"""

import os
import sys
import tempfile
import json
import pytest

# Make sure the package is importable when running tests from inside the repo
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_package_imports():
    """All package modules should import cleanly."""
    import tg_vault
    assert tg_vault.VERSION == 8
    assert tg_vault.__author__ == "kesafatkari"


def test_module_imports():
    """Each sub-module should be importable."""
    from tg_vault import (
        constants, utils, config, bot_pool,
        uploader, downloader, crypto, compression,
        chunk_header, db, db_sync, commands, interactive, cli,
    )
    assert constants.VERSION == 8
    assert hasattr(utils, "compute_sha256")
    assert hasattr(config, "Config")
    assert hasattr(bot_pool, "BotPool")
    assert hasattr(uploader, "Uploader")
    assert hasattr(downloader, "Downloader")
    assert hasattr(crypto, "Encryptor")
    assert hasattr(compression, "compress_file")
    assert hasattr(chunk_header, "create_header")
    assert hasattr(db, "Database")
    assert hasattr(cli, "main")


def test_backward_compat_shim():
    """The legacy `tg.py` shim should re-export everything."""
    import tg
    assert hasattr(tg, "Config")
    assert hasattr(tg, "Uploader")
    assert hasattr(tg, "Downloader")
    assert hasattr(tg, "BotPool")
    assert hasattr(tg, "main")


def test_helpers_format_size():
    """format_size should produce human-readable strings."""
    from tg_vault.utils import format_size
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024) == "1.00 MB"
    assert format_size(1024 * 1024 * 1024) == "1.00 GB"


def test_helpers_sanitize_filename():
    """sanitize_filename should clean illegal chars and preserve extension."""
    from tg_vault.utils import sanitize_filename
    assert sanitize_filename("hello.txt") == "hello.txt"
    assert sanitize_filename("a/b\\c:d") == "a_b_c_d"
    # Long name should be truncated
    long_name = "a" * 100 + ".txt"
    assert len(sanitize_filename(long_name)) <= 60
    assert sanitize_filename(long_name).endswith(".txt")


def test_helpers_sanitize_hashtags():
    """sanitize_hashtags should handle Telegram rules correctly."""
    from tg_vault.utils import sanitize_hashtags
    assert sanitize_hashtags(["movies", "2026"]) == ["movies", "_2026"]
    assert sanitize_hashtags(["sci-fi", "Sci-Fi"]) == ["sci_fi"]  # dedup case-insensitive
    assert sanitize_hashtags(["hello world"]) == ["hello_world"]


def test_helpers_parse_telegram_link():
    """parse_telegram_link should parse various link formats."""
    from tg_vault.utils import parse_telegram_link
    # Private channel
    chat_id, msg_id = parse_telegram_link("https://t.me/c/2417735052/9072")
    assert chat_id == -1002417735052
    assert msg_id == 9072
    # Public channel
    chat_id, msg_id = parse_telegram_link("https://t.me/mychannel/123")
    assert chat_id == "@mychannel"
    assert msg_id == 123


def test_helpers_build_share_link():
    """build_share_link should produce correct URLs."""
    from tg_vault.utils import build_share_link
    # Private channel
    assert build_share_link(-1002417735052, 42) == "https://t.me/c/2417735052/42"
    # Public channel
    assert build_share_link("@mychannel", 42) == "https://t.me/mychannel/42"


def test_chunk_header_round_trip():
    """chunk header create + parse should round-trip correctly."""
    from tg_vault.chunk_header import create_header, parse_header, FLAG_COMPRESSED, FLAG_ENCRYPTED
    import hashlib
    sha_prefix = bytes.fromhex(hashlib.sha256(b"test").hexdigest())[:16]
    header = create_header(
        chunk_index=3,
        total_chunks=10,
        original_size=1024 * 1024 * 100,
        sha256_prefix=sha_prefix,
        flags=FLAG_COMPRESSED | FLAG_ENCRYPTED,
    )
    assert len(header) == 40
    parsed = parse_header(header)
    assert parsed["chunk_index"] == 3
    assert parsed["total_chunks"] == 10
    assert parsed["original_size"] == 1024 * 1024 * 100
    assert parsed["compressed"] is True
    assert parsed["encrypted"] is True


def test_compression_skip():
    """should_skip_compression should recognize already-compressed formats."""
    from tg_vault.compression import should_skip_compression
    assert should_skip_compression("movie.mp4") is True
    assert should_skip_compression("photo.jpg") is True
    assert should_skip_compression("archive.zip") is True
    assert should_skip_compression("document.pdf") is True
    assert should_skip_compression("data.txt") is False
    assert should_skip_compression("database.sqlite") is False


def test_compression_round_trip():
    """compress_data + decompress_data should round-trip."""
    from tg_vault.compression import compress_data, decompress_data
    # Text data should compress well
    data = b"Hello, world! " * 1000
    compressed, was_compressed = compress_data(data, "test.txt")
    assert was_compressed is True
    assert len(compressed) < len(data)
    restored = decompress_data(compressed, was_compressed)
    assert restored == data


def test_database_round_trip(tmp_path):
    """Database insert + lookup should work."""
    from tg_vault.db import Database
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)

    manifest = {
        "name": "test.txt",
        "size": 100,
        "sha256": "abc123",
        "total_parts": 1,
        "chunk_size": 19 * 1024 * 1024,
        "message_ids": [42],
        "manifest_message_id": 43,
        "description_msg_id": 41,
        "description": "Test file",
        "hashtags": ["test", "demo"],
        "channel_id": -1001234567890,
        "session_id": "abcd1234",
        "encrypted": False,
        "compressed": False,
        "has_chunk_header": True,
    }

    file_id = db.insert_file(manifest, "https://t.me/c/1234567890/43")
    assert file_id > 0

    # Lookup by SHA
    rec = db.get_file_by_sha("abc123")
    assert rec is not None
    assert rec["name"] == "test.txt"

    # Stats
    stats = db.stats()
    assert stats["total_files"] == 1


def test_config_save_load(tmp_path):
    """Config save + load should round-trip."""
    from tg_vault.config import Config
    cfg_path = str(tmp_path / "config.json")
    cfg = Config(path=cfg_path)
    cfg.bots = [{"token": "123:ABC", "username": "testbot"}]
    cfg.main_channel = -1001234567890
    cfg.temp_channel = -1009876543210
    cfg.save()

    loaded = Config.load(cfg_path)
    assert loaded.bots == cfg.bots
    assert loaded.main_channel == cfg.main_channel
    assert loaded.temp_channel == cfg.temp_channel


def test_cli_parser_builds():
    """The CLI parser should build without errors and recognize subcommands."""
    from tg_vault.cli import build_parser
    parser = build_parser()
    # Parse a few commands to make sure they work
    args = parser.parse_args(["upload", "file.zip", "--desc", "test"])
    assert args.command == "upload"
    assert args.files == ["file.zip"]
    assert args.desc == "test"

    args = parser.parse_args(["download", "https://t.me/c/123/1"])
    assert args.command == "download"
    assert args.links == ["https://t.me/c/123/1"]

    args = parser.parse_args(["db", "list"])
    assert args.command == "db"
    assert args.db_action == "list"


def test_encryption_available():
    """is_encryption_available should return True (cryptography is a dependency)."""
    from tg_vault.crypto import is_encryption_available
    assert is_encryption_available() is True


def test_encryption_round_trip():
    """AES-256-GCM encrypt + decrypt should round-trip."""
    from tg_vault.crypto import Encryptor
    enc = Encryptor("test-password")
    plaintext = b"Hello, secret world!"
    iv = (0).to_bytes(12, "big")
    ciphertext = enc.encrypt_chunk_with_iv(plaintext, iv)

    # Decrypt with same password + salt
    enc2 = Encryptor("test-password", salt=enc.salt)
    decrypted = enc2.decrypt_chunk(ciphertext, iv)
    assert decrypted == plaintext

    # Wrong password should fail to decrypt (InvalidTag)
    enc3 = Encryptor("wrong-password", salt=enc.salt)
    with pytest.raises(Exception):
        enc3.decrypt_chunk(ciphertext, iv)


def test_password_verification_hash():
    """Password verification hash should be deterministic and timing-safe."""
    from tg_vault.crypto import Encryptor
    h1 = Encryptor.get_password_hash("my-password")
    h2 = Encryptor.get_password_hash("my-password")
    assert h1 == h2  # deterministic
    assert Encryptor.verify_password_hash("my-password", h1) is True
    assert Encryptor.verify_password_hash("wrong-password", h1) is False
