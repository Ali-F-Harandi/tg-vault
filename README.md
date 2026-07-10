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
- ✅ **Per-bot rate limiting** (FloodWait-safe, ~50 ms min interval)
- ✅ **Connection pooling** (`requests.Session` per bot)
- ✅ **Description message** before parts (name + size + SHA256 + custom text + hashtags)
- ✅ **Manifest message** after parts (acts as "end" marker + reply to last part)
- ✅ **Resume** for both upload and download
- ✅ **Filename & caption length validation/sanitization**
- ✅ **Graceful `Ctrl+C` cleanup** (deletes temp messages)
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
  "version": 6
}
```

## Web App (GitHub Pages)

tg-vault also ships a **fully client-side web app** — no backend, no server, no install. Your bot token never leaves your browser.

🌐 **Live demo**: https://kesafatkari.github.io/tg-vault/

Features:
- 🔐 Settings stored in `localStorage` (browser only, never sent anywhere except Telegram)
- 📤 Drag-and-drop file upload with live progress
- 📥 Download by manifest link with SHA256 verification
- 📋 Show manifest info without downloading
- 🌙 Dark theme, mobile-friendly
- 🚀 Works on any static host (GitHub Pages, Netlify, Cloudflare Pages, or just open the HTML file locally)

To run locally:
```bash
# Just open the file in your browser
open docs/index.html
# Or serve it locally
python3 -m http.server 8000 -d docs
# Then visit http://localhost:8000
```

## Examples

See [`examples/`](examples/) for:
- [`parallel_uploads.py`](examples/parallel_uploads.py) — Upload multiple files concurrently
- [`backup_directory.py`](examples/backup_directory.py) — Recursively back up a directory
- [`download_all.py`](examples/download_all.py) — Download all manifest files from a channel

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
