# tg-vault

**Telegram Bot API cloud storage — use Telegram as a personal cloud backend.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Version: v8](https://img.shields.io/badge/version-v8-green.svg)](CHANGELOG.md)

> 📖 [فارسی](README.fa.md) | English

---

## What is tg-vault?

tg-vault turns Telegram into an **unlimited personal cloud drive** using **only a Bot token** — no phone number, no `api_id`/`api_hash`, no MTProto/Telethon/Pyrogram required.

Telegram's Bot API has an asymmetric size limit: `sendDocument` accepts 50 MB uploads but `getFile` can only download 20 MB. tg-vault splits files into ~19 MB chunks, uploads each as a reply-linked message, and stores a **manifest** message containing the file's metadata (name, size, SHA256, and the list of every chunk's `message_id`). To download, you only need the link to the manifest.

## Features

- 🚀 **Multi-bot with round-robin** — N bots = N× throughput
- ⚡ **Parallel chunk download** using `ThreadPoolExecutor`
- 🛡️ **FloodWait-safe** per-bot rate limiting (50 ms min interval)
- 🔐 **AES-256-GCM encryption** (PBKDF2-HMAC-SHA512, 600k iterations) — zero-knowledge
- 📦 **Smart gzip compression** — auto-skips already-compressed formats (mp4, jpg, zip, pdf, …)
- 🏷️ **Self-describing chunk headers** (TGV1 magic) — identify chunks without the DB
- 🗄️ **SQLite database** with full-text search, tags, download history, and channel sync
- ⏯️ **Resume** for both upload and download
- 🧹 **Graceful Ctrl+C cleanup** — temp messages are always deleted
- 🖥️ **CLI, interactive menu, and tkinter GUI** (with proxy support)
- 🌐 **Bulk upload/download** — multiple files/links at once
- 🔗 **Link-based sharing** — `https://t.me/c/<chat>/<msg>` is all you need

## Quick start

```bash
# 1. Install
pip install -r requirements.txt        # or: pip install .

# 2. Initialize (creates ~/.tg-vault.json)
python tg.py init

# 3. Interactive setup wizard (recommended)
python tg.py setup

# 4. Test connectivity
python tg.py test

# 5. Upload a file
python tg.py upload movie.mp4 --desc "Backup" --tag movies,2026

# 6. Download by link
python tg.py download https://t.me/c/1234567890/42

# 7. List / search / delete / info
python tg.py ls --limit 10
python tg.py info https://t.me/c/1234567890/42
python tg.py delete https://t.me/c/1234567890/42 --force
```

You can also run it as a module: `python -m tg_vault upload file.zip`.

## Encryption

```bash
# Encrypt on upload (will prompt for password)
python tg.py upload secret.txt --encrypt

# Or provide password via env var (recommended for scripts)
export TG_VAULT_PASSWORD="my-secret"
python tg.py upload secret.txt --encrypt

# Decrypt on download (will prompt, or use TG_VAULT_PASSWORD)
python tg.py download https://t.me/c/.../42
```

The encryption key is **never stored**. The manifest stores only: salt, password verification hash (for fail-fast on wrong passwords), and per-chunk IV derived from chunk index.

## Database (optional, recommended)

```bash
python tg.py db enable                        # enable SQLite DB
python tg.py db list                          # list recent files
python tg.py db search "movie"                # search by name/desc/tags
python tg.py db query --tag backup --min-size 1000000   # advanced filter
python tg.py db stats                         # show statistics
python tg.py db sync                          # backup DB to Telegram channel
python tg.py db restore                       # restore DB from channel
python tg.py db find-orphans                  # find manifests not in DB
```

## Project structure

```
tg-vault/
├── tg.py                    # Backward-compat shim → tg_vault.cli
├── gui.py                   # Backward-compat shim → gui.app
├── pyproject.toml           # Python package metadata
├── requirements.txt
├── config.sample.json
│
├── tg_vault/                # Main package
│   ├── __init__.py          # Re-exports public API
│   ├── __main__.py          # python -m tg_vault entry point
│   ├── cli.py               # argparse CLI + main()
│   ├── commands.py          # cmd_* functions (upload, download, db, etc.)
│   ├── interactive.py       # Interactive menu
│   ├── config.py            # Config class (~/.tg-vault.json)
│   ├── bot_pool.py          # Bot + BotPool (round-robin, thread-safe)
│   ├── uploader.py          # Uploader class
│   ├── downloader.py        # Downloader class (parallel chunks)
│   ├── crypto.py            # AES-256-GCM encryptor (PBKDF2)
│   ├── compression.py       # Smart gzip with format-aware bypass
│   ├── chunk_header.py      # TGV1 40-byte self-describing header
│   ├── db.py                # SQLite database (files, chunks, tags, downloads)
│   ├── db_sync.py           # DB backup/restore to Telegram channel
│   ├── constants.py         # VERSION + Telegram API limits
│   └── utils.py             # Helpers (SHA256, format_size, sanitize, ProgressTracker)
│
├── gui/
│   ├── __init__.py
│   └── app.py               # tkinter GUI (4 tabs: Upload/Download/Browse/Settings)
│
├── examples/
│   ├── backup_directory.py  # Recursive directory backup
│   ├── bulk_upload.py       # Bulk upload wrapper
│   ├── bulk_download.py     # Bulk download wrapper
│   ├── encrypted_upload.py  # Encrypted upload wrapper
│   ├── parallel_uploads.py  # Parallel subprocess uploads
│   ├── db_search.py         # Scriptable DB search
│   └── download_all.py      # Download all manifests in channel
│
├── docs/
│   ├── ARCHITECTURE.md      # Design decisions + thread safety
│   ├── USAGE.md             # Detailed usage guide
│   ├── CONFIGURATION.md     # Config file reference
│   ├── SECURITY.md          # Encryption + threat model
│   └── TELEGRAM_LIMITS.md   # Bot API hard/soft limits
│
├── tests/
│   └── test_smoke.py        # 17 smoke tests
│
├── README.md                # This file
├── README.fa.md             # Persian README
├── CHANGELOG.md
├── CONTRIBUTING.md
└── LICENSE
```

## How it works

```
UPLOAD:
  file → SHA256 → description msg → [chunk1 → chunk2 → ...] → manifest msg
          (raw → compress → encrypt → TGV1 header) per chunk
          (each chunk replies to previous, round-robin across bots)

DOWNLOAD:
  link → fetch manifest → parse → parallel chunk download
       → strip header → decrypt → decompress → assemble
       → verify SHA256 → rename to final filename
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for design decisions, thread safety, and the rationale behind the 19 MB chunk size and `forwardMessage` workaround.

## Requirements

- Python 3.8+
- `requests` (HTTP client)
- `cryptography` (for `--encrypt`)
- `tkinter` (for GUI; built into Python on Windows/macOS, may need `python3-tk` on Linux)

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Inspired by [TAS (Telegram as Storage)](https://github.com/ixchio/tas) — adopted its best ideas (TGV1 header, encryption pipeline, progress bar) while keeping the 19 MB chunk size that actually works for downloads.
