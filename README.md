# tg-vault

> Use Telegram as a personal cloud storage backend using **only Bot API tokens** — no phone number, no `api_id`/`api_hash`, no MTProto/Telethon/Pyrogram required.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

📖 **[فارسی / Persian documentation](README.fa.md)**

---

## The Problem

Telegram's Bot API has an asymmetric file size limit:

| Operation | Limit |
|-----------|-------|
| `sendDocument` upload | **50 MB** |
| `getFile` download | **20 MB** ← the real bottleneck |

This means: a bot can upload a 50 MB file, but it can never download anything larger than 20 MB via the official Bot API. Tools like [teldrive](https://github.com/tgdrive/teldrive) work around this by using **MTProto** (which requires `api_id`/`api_hash` and a phone-number-authenticated user session).

## The Solution

`tg-vault` splits large files into ≤19 MB chunks, uploads each chunk as a document message to a channel where your bot is admin, and stores a final **manifest message** containing the file's metadata (name, size, SHA256, and the list of every chunk's `message_id`).

To download, you only need the **link to the manifest message** — `tg-vault` reads it, downloads each chunk, and verifies the SHA256.

```
┌──────────────────────────────────────────────────────┐
│  Your Channel                                        │
│                                                      │
│  [Description]  ← name + size + SHA256 + tags        │
│       ↓ reply                                        │
│  [Part 1/4]    ← chunk (~19 MB)                      │
│       ↓ reply                                        │
│  [Part 2/4]                                          │
│       ↓ reply                                        │
│  [Part 3/4]                                          │
│       ↓ reply                                        │
│  [Part 4/4]                                          │
│       ↓ reply                                        │
│  [Manifest]     ← JSON with all message_ids + SHA256 │
│                  (this is the link you keep)         │
└──────────────────────────────────────────────────────┘
```

## Features

- ✅ **Multi-bot support** with round-robin rotation (multiply throughput)
- ✅ **Parallel chunk download** (uses all bots concurrently)
- ✅ **Bulk upload & download** (multiple files / multiple links in one command)
- ✅ **AES-256-GCM encryption** (optional, zero-knowledge, with PBKDF2 600k iterations)
- ✅ **Smart gzip compression** (skips already-compressed formats like .mp4, .zip)
- ✅ **Self-describing chunk headers** (TGV1 magic — each chunk identifies itself)
- ✅ **SQLite database** for metadata storage (search, stats, export)
- ✅ **Per-bot rate limiting** (FloodWait-safe, ~50 ms min interval)
- ✅ **Connection pooling** (`requests.Session` per bot)
- ✅ **Description message** before parts (name + size + SHA256 + custom text + hashtags)
- ✅ **Manifest message** after parts (acts as "end" marker + reply to last part)
- ✅ **Resume** for both upload and download
- ✅ **Filename & caption length validation/sanitization**
- ✅ **Hashtag sanitization** (Telegram-compatible: `sci-fi` → `sci_fi`, `2026` → `_2026`)
- ✅ **Graceful `Ctrl+C` cleanup** (deletes temp messages)
- ✅ **Improved progress bar** (instantaneous speed sampled every 200ms, like TAS)
- ✅ **Config file** (`~/.tg-vault.json`) for bots, channels, defaults
- ✅ **CLI commands + interactive menu**
- ✅ **Concurrency-safe** (each session has unique UUID tag)

## Quick Start

### Install

```bash
git clone https://github.com/kesafatkari/tg-vault.git
cd tg-vault
pip install -r requirements.txt
```

### Configure

1. **Create a Telegram bot** — talk to [@BotFather](https://t.me/BotFather), run `/newbot`, copy the token.
2. **Create a Telegram channel** (private recommended), then add your bot as an **administrator** with:
   - Post messages ✅
   - Delete messages ✅
3. **Get the channel ID** — see [Getting a Channel ID](#getting-a-channel-id) below.

### Initialize

The easiest way is the interactive setup wizard:

```bash
python tg.py setup
```

This walks you through bot token verification, channel setup, and a final connectivity test — all in one go.

Alternatively, you can use individual commands:

```bash
python tg.py init
python tg.py bots add 123456789:ABC-DEF...
python tg.py channels set main -1001234567890
python tg.py channels set temp -1009876543210   # optional, defaults to main
python tg.py test
```

Or edit the config file directly — see [Configuration File](#configuration-file).

## Getting a Channel ID

You need the channel ID in one of these formats:
- **Private channel**: `-1001234567890` (starts with `-100`, then the internal ID)
- **Public channel**: `@mychannel_username`

### Method 1: Using @userinfobot (easiest)

1. Forward any message from your channel to [@userinfobot](https://t.me/userinfobot).
2. It will reply with the chat ID (e.g. `-1001234567890`).

### Method 2: From a `t.me/c/...` link

If your channel is private, look at its message links:
- Link: `https://t.me/c/1234567890/42`
- Channel ID: `-1001234567890` (prepend `-100` to the number after `/c/`)

### Method 3: Using Telegram Web

1. Open your channel in https://web.telegram.org
2. Look at the URL: `https://web.telegram.org/#-1001234567890`
3. The number after `#` is the channel ID.

### Method 4: Using the Bot API

1. Add your bot to the channel as admin.
2. Send any message in the channel.
3. Open in browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Find your channel's `chat.id` in the JSON response.

### Adding the bot as admin

In your channel:
1. Open channel → **Manage Channel** → **Administrators**
2. Search for your bot's username
3. Add it with these rights:
   - ✅ Post messages
   - ✅ Edit messages (optional, recommended)
   - ✅ Delete messages

Without these rights, `tg-vault` cannot upload chunks or clean up temp messages.

### Upload

```bash
# Basic upload
python tg.py upload movie.mp4

# With description and hashtags
python tg.py upload movie.mp4 --desc "Blade Runner 2049 - 4K backup" --tag movies,sci-fi,2026
```

Output:
```
🔗 ★ Download link:
   https://t.me/c/1234567890/42
```

### Download

```bash
# Default: saves to current dir with original filename
python tg.py download https://t.me/c/1234567890/42

# Custom output
python tg.py download https://t.me/c/1234567890/42 --output my-movie.mp4 --output-dir ~/Downloads
```

### Resume interrupted operations

```bash
python tg.py upload movie.mp4 --resume
python tg.py download https://t.me/c/1234567890/42 --resume
```

## CLI Reference

```
tg-vault v6 — Telegram Bot API cloud storage

Commands:
  init                              Create a sample config file
  bots add <TOKEN>                  Add a bot
  bots list                         List configured bots
  bots remove <INDEX>               Remove a bot by index
  channels set main <ID>            Set main channel
  channels set temp <ID>            Set temp channel (optional)
  channels show                     Show configured channels
  test                              Test connectivity for all bots/channels
  upload <FILE> [options]           Upload a file
    --desc, -d "text"                 Description text
    --tag, -t "t1,t2,t3"              Hashtags (comma-separated)
    --resume, -r                      Resume interrupted upload
  download <LINK> [options]         Download by manifest link
    --resume, -r                      Resume interrupted download
    --output, -o "name"               Output filename
    --output-dir "path"               Output directory (default: .)
  info <LINK>                       Show manifest info without downloading
  ls [--limit N]                    List recent manifest files in main channel
  delete <LINK> [--force]           Delete a file's messages from channel
  cleanup [--max-count N]           Clean up temp channel

Global options:
  --config <PATH>                   Use a custom config file (default: ~/.tg-vault.json)
  --version                         Show version
```

Run `python tg.py` with no arguments to enter the **interactive menu**.

## Why Multi-Bot?

Telegram enforces a per-bot rate limit of ~30 messages/sec globally and ~1 msg/sec to the same chat. By adding multiple bots (all admins in your channel), `tg-vault` rotates between them — effectively multiplying your throughput.

Each bot also gets its own `requests.Session` for connection pooling and its own rate-limit token bucket.

```bash
# Add up to ~20 bots (Telegram's per-account limit)
python tg.py bots add <token1>
python tg.py bots add <token2>
python tg.py bots add <token3>
python tg.py test   # verify all have admin rights
```

## How Download Works (The Quirk)

A bot **cannot send messages to itself** in Telegram. So we cannot forward messages to the bot's own chat to extract `file_id`s.

Instead, `tg-vault`:

1. Sends a `forwardMessage` from the source channel to the **temp channel** (with `disable_notification=true` so no one is notified).
2. Reads the `file_id` from the forwarded message.
3. Calls `getFile` to get the download URL.
4. Downloads the chunk.
5. **Immediately deletes** the forwarded message from the temp channel.

If no temp channel is configured, the main channel is used as temp — but the forwarded messages will briefly appear (and disappear) there.

## Concurrency Safety

Each upload/download session gets a unique 8-character UUID tag. This tag is included in every chunk's caption, so:

- Multiple `tg-vault` processes can run in parallel without conflicts.
- Temp channel cleanup only deletes messages from the current session.
- Resume state is stored per-file in `<filename>.resume.json`.

For 100 parallel downloads, run 100 instances — the `BotPool` will round-robin between your bots automatically. With 5 bots, you can safely run ~5 concurrent operations without FloodWait.

## Limitations

- **Max file size**: 2 GB (Telegram's hard limit, even with Local Bot API Server).
- **Chunk size**: 19 MB default (under the 20 MB `getFile` limit). Configurable via `chunk_size_mb` in config.
- **Rate limits**: ~30 msgs/sec per bot. With N bots, effective rate is ~N×30.
- **No streaming**: Full file must be downloaded before SHA256 verification.
- **Bots per account**: Telegram limits ~20 bots per user account via @BotFather.

## Going Further: Local Bot API Server

If you self-host the [Local Bot API Server](https://github.com/tdlib/telegram-bot-api), the limits become:

| | Cloud | Local Server |
|---|---|---|
| Upload | 50 MB | **2000 MB** |
| Download | 20 MB | **No limit** |

This requires `api_id`/`api_hash` (from https://my.telegram.org) **to run the server**, but client requests still authenticate with the bot token only. So your end-users still don't need to expose their phone number — only you (the server operator) do.

To use a Local Bot API Server with `tg-vault`, set `api_url` per bot in the config (future feature — PRs welcome).

## Configuration File

Default location: `~/.tg-vault.json`

```json
{
  "bots": [
    {"token": "123:ABC...", "username": "my_first_bot"},
    {"token": "456:DEF...", "username": "my_second_bot"}
  ],
  "channels": {
    "main": -1001234567890,
    "temp": -1009876543210
  },
  "chunk_size_mb": 19,
  "upload_delay": 0.3,
  "download_delay": 0.2,
  "parallel_workers": 4,
  "db_enabled": true,
  "db_path": "/home/user/.tg-vault.db",
  "version": 7
}
```

## Bulk Upload & Download

tg-vault supports **bulk operations** natively — pass multiple files or multiple links in a single command.

### Bulk Upload

Upload multiple files in one command. The same `--desc` and `--tag` flags apply to all files:

```bash
# Upload multiple files
python tg.py upload file1.zip file2.zip file3.zip --desc "Backup batch" --tag backup,2026

# Use shell wildcards (expanded by your shell)
python tg.py upload *.mp4 --tag movies
python tg.py upload photos/*.jpg --desc "Vacation photos"
```

The script uploads them sequentially (one after another) and shows a summary at the end:

```
============================================================
📊 Bulk upload summary (3 files):
============================================================
  ✅ file1.zip: https://t.me/c/.../42
  ✅ file2.zip: https://t.me/c/.../46
  ❌ file3.zip: failed
3/3 files uploaded successfully.
```

### Bulk Download

Download multiple files by passing multiple manifest links:

```bash
# Multiple links
python tg.py download https://t.me/c/.../42 https://t.me/c/.../43 https://t.me/c/.../44

# Read links from a text file (one per line; # comments allowed)
python tg.py download --links-file my_links.txt --output-dir ~/Downloads

# Combine both
python tg.py download https://t.me/c/.../42 --links-file more_links.txt --output-dir ~/Downloads
```

Sample `my_links.txt`:
```
# Backup links — downloaded 2026-07-11
https://t.me/c/1234567890/42
https://t.me/c/1234567890/46
# this line is a comment
https://t.me/c/1234567890/50
```

The `--output` flag is only allowed for single-file downloads (otherwise the original filename from the manifest is used).

## Database (SQLite)

tg-vault can optionally store metadata for every uploaded file in a local SQLite database. This lets you:

- 🔍 **Search** by name, description, or hashtag
- 📊 **View statistics** (total files, total size, download count, top files)
- 📋 **List** all files with their share links
- 📤 **Export** to JSON for backup or migration
- 🔄 **Track downloads** (when each file was last downloaded)

The database is **enabled by default** when you run `tg.py setup`. You can also enable it manually:

```bash
python tg.py db enable                                  # enable + create DB
python tg.py db info                                    # show DB info + stats
python tg.py db list --limit 20                         # list recent files
python tg.py db search "movie"                          # search by name/desc/tags
python tg.py db search "backup" --limit 10              # limit results
python tg.py db stats                                   # show statistics only
python tg.py db export --output backup.json             # export all records to JSON
python tg.py db disable                                 # disable (file kept on disk)
```

### What's stored in the database?

For every uploaded file:

| Field | Description |
|-------|-------------|
| `id` | Auto-increment row ID |
| `name` | Original filename |
| `size` | File size in bytes |
| `sha256` | SHA256 hash (unique identifier) |
| `total_parts` | Number of chunks |
| `chunk_size` | Chunk size used (usually 19 MB) |
| `message_ids` | JSON array of Telegram message IDs (parts + manifest) |
| `manifest_msg_id` | Message ID of the manifest message |
| `description_msg_id` | Message ID of the description message |
| `description` | User-provided description text |
| `hashtags` | JSON array of hashtags |
| `main_channel` | Channel ID where file is stored |
| `temp_channel` | Channel ID used for temp forwards |
| `share_link` | `t.me/c/.../N` link to the manifest |
| `session_id` | 8-char UUID of the upload session |
| `uploaded_at` | Unix timestamp of upload |
| `last_accessed_at` | Unix timestamp of last download |
| `status` | `uploaded` / `deleted` / `corrupted` |

A separate `downloads` table logs each download event (file_id, output_path, sha256_verified, downloaded_at).

### Database location

The database path is determined in this order:
1. `db_path` field in config file (explicit)
2. Next to the config file: `~/.tg-vault.db`
3. Override at any time: `python tg.py db enable` then edit config

The path is stored in the config file so the script knows where to find it on every run.

### Automatic logging

When the database is enabled:
- **Every upload** automatically inserts a record (or updates if SHA256 already exists)
- **Every download** automatically logs to the `downloads` table and updates `last_accessed_at`
- If the database file is missing, it's created automatically on first use

## Encryption & Compression (v8)

tg-vault v8 adds **optional client-side encryption** and **smart compression**, inspired by [TAS](https://github.com/ixchio/tas).

### Encryption (AES-256-GCM)

Encrypt files end-to-end with a password. Even if someone gains access to your Telegram channel, they cannot read your files without the password.

```bash
# Upload with encryption (will prompt for password)
python tg.py upload secret.txt --encrypt

# Or specify password via flag
python tg.py upload secret.txt --encrypt --password "my-password"

# Or via env var (recommended for scripts)
export TG_VAULT_PASSWORD="my-password"
python tg.py upload secret.txt --encrypt

# Download (will prompt for password)
python tg.py download https://t.me/c/.../42

# Or specify password
python tg.py download https://t.me/c/.../42 --password "my-password"
```

**Technical details:**
- **Algorithm:** AES-256-GCM (authenticated encryption — detects tampering)
- **Key derivation:** PBKDF2-HMAC-SHA512 with 600,000 iterations (OWASP 2025 recommendation)
- **Salt:** 32 bytes random, stored in manifest
- **IV:** 12 bytes, deterministic per chunk (derived from chunk index) — avoids storing per-chunk IVs
- **Password verification:** Separate hash stored in manifest, so wrong passwords fail fast (before any download)
- **Key NEVER stored** — only the user knows it

**What's stored in the manifest:**
- `encrypted: true`
- `encryption_algorithm: "aes-256-gcm"`
- `encryption_kdf: "pbkdf2-sha512-600k"`
- `encryption_salt: <base64>`
- `password_hash: <hex>` (verification only — NOT the encryption key)

### Compression (smart gzip)

Compression is **on by default**. tg-vault automatically skips compression for already-compressed formats (jpg, mp4, zip, etc.) to save CPU.

```bash
# Default: compression on
python tg.py upload file.txt

# Disable compression
python tg.py upload file.txt --no-compress
```

**Skipped extensions:** `.jpg`, `.png`, `.mp4`, `.mkv`, `.zip`, `.7z`, `.gz`, `.pdf`, `.docx`, `.epub`, and [many more](tg_compression.py).

For other files, gzip level 6 is used. If compression doesn't actually reduce size, the original is kept.

### Self-describing chunk headers (TGV1)

Each chunk starts with a 40-byte header containing:
- Magic bytes (`TGV1`)
- Version
- Flags (compressed? encrypted?)
- Chunk index + total chunks
- Original file size
- First 16 bytes of file SHA256

This lets you identify a chunk without consulting the database — useful for forensic recovery.

### v8 chunk pipeline

```
Original file → split into 19MB chunks
              → compress each chunk (gzip, optional)
              → encrypt each chunk (AES-256-GCM, optional)
              → prepend TGV1 header (40 bytes)
              → upload to Telegram
```

On download, the pipeline reverses:
```
Download chunk → strip TGV1 header
               → decrypt (AES-256-GCM)
               → decompress (gzip)
               → write to file
               → verify SHA256 of original file
```

## Channel Type: Channel vs Group vs Topic Group

For tg-vault's use case (storing files as bot messages), here's how the three types compare:

| Type | Pros | Cons | Recommended? |
|------|------|------|-------------|
| **Private Channel** | ✅ Clean one-way message stream; persistent; everyone sees all messages; no member-introduced noise | Only one message stream (no categorization) | ✅ **Yes — default** |
| **Group (regular)** | Two-way chat | Members can delete others' messages; gets noisy | ❌ No |
| **Group with Topics** | ✅ Can use one topic per file/category for organization | Requires `message_thread_id` in every API call (not currently supported by tg-vault) | ⚠️ Not yet supported |

**Bottom line:** Use a **private channel** for storage. If you want categorization, use **separate channels per category** (e.g. `movies`, `photos`, `documents`) and switch between them via `python tg.py channels set main <id>`.

## Examples

See [`examples/`](examples/) for:
- [`parallel_uploads.py`](examples/parallel_uploads.py) — Upload multiple files concurrently (subprocess-per-file)
- [`bulk_upload.py`](examples/bulk_upload.py) — Bulk upload via the new `upload file1 file2 ...` syntax
- [`bulk_download.py`](examples/bulk_download.py) — Bulk download via the new `download link1 link2 ...` syntax
- [`backup_directory.py`](examples/backup_directory.py) — Recursively back up a directory
- [`download_all.py`](examples/download_all.py) — Download all manifest files from a channel
- [`db_search.py`](examples/db_search.py) — Search the SQLite database from a script

## Comparison With Similar Projects

| Project | Approach | Bot-only? | Multi-bot? | Encryption? |
|---------|----------|-----------|------------|-------------|
| **tg-vault** (this) | 19 MB chunking + manifest | ✅ | ✅ | ❌ (planned) |
| [Pentaract](https://github.com/Dominux/Pentaract) | 20 MB chunking | ✅ | ✅ (up to 20) | ❌ |
| [tas](https://github.com/ixchio/tas) | 49 MB chunking + AES-GCM | ✅ | ❌ | ✅ |
| [teldrive](https://github.com/tgdrive/teldrive) | MTProto | ❌ (needs api_id/api_hash) | ❌ | ❌ |

## Contributing

Pull requests welcome! Some ideas:
- 🔐 Client-side AES-256-GCM encryption
- 🌐 Local Bot API Server support
- 🎬 HTTP Range streaming for video files
- 🐳 Docker image + REST API wrapper
- 🖥️ TUI (Textual / Rich) for the interactive menu
- 📊 Progress reporting via WebSockets

## License

[MIT](LICENSE) © 2026 [kesafatkari](https://github.com/kesafatkari)

## Acknowledgments

- Inspired by [Pentaract](https://github.com/Dominux/Pentaract) (Rust, 20 MB chunking reference implementation)
- Inspired by [tas](https://github.com/ixchio/tas) (AES-256-GCM encryption + FUSE)
- The "message_id + copyMessage" cross-bot portability trick from [tg-bot-storage](https://github.com/DipandaAser/tg-bot-storage)
- Built with the [Telegram Bot API](https://core.telegram.org/bots/api)
