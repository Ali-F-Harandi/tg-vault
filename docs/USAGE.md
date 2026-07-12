# Usage Guide

This document covers all tg-vault commands in detail, including flags, common patterns, and troubleshooting.

## Table of contents

- [Installation](#installation)
- [First-time setup](#first-time-setup)
- [Commands](#commands)
  - [`init`](#init)
  - [`setup`](#setup)
  - [`bots`](#bots)
  - [`channels`](#channels)
  - [`test`](#test)
  - [`upload`](#upload)
  - [`download`](#download)
  - [`info`](#info)
  - [`ls`](#ls)
  - [`delete`](#delete)
  - [`cleanup`](#cleanup)
  - [`db`](#db)
- [Interactive menu](#interactive-menu)
- [GUI](#gui)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)

---

## Installation

```bash
# From source
git clone https://github.com/Ali-F-Harandi/tg-vault.git
cd tg-vault
pip install -r requirements.txt

# Or install as a package
pip install .
```

Requirements:
- Python 3.8+
- `requests` (HTTP client)
- `cryptography` (for `--encrypt`)
- `tkinter` (for GUI; built into Python on Windows/macOS, may need `python3-tk` on Linux)

### Optional: Pyrogram Hybrid Mode (2 GB chunks)

To bypass Bot API's 50 MB upload / 20 MB download limits:

```bash
pip install pyrogram tgcrypto
```

Then add `api_id` and `api_hash` to your config (see [CONFIGURATION.md](CONFIGURATION.md)).

## First-time setup

### Step 1: Get a bot token

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`
3. Choose a name and username
4. Copy the token (looks like `123456789:ABC-DEF...`)

### Step 2: Create a channel

1. Create a new Telegram channel (private recommended)
2. Add your bot as administrator
3. Give it **Post messages** and **Delete messages** rights
4. Get the channel ID:
   - Private channel: `-1001234567890` (starts with `-100`)
   - Public channel: `@mychannel_username`

> **Tip:** To find a private channel ID, forward a message from it to [@userinfobot](https://t.me/userinfobot).

### Step 3: Run the setup wizard

```bash
python tg.py init       # creates ~/.tg-vault.json
python tg.py setup      # interactive 4-step wizard
```

The wizard will:
1. Ask for the bot token and verify it
2. Ask for the main channel ID and verify the bot's admin rights
3. Ask for an optional temp channel (defaults to main)
4. Ask whether to enable the SQLite database

After setup, run `python tg.py test` to verify everything works.

## Commands

### `init`

```bash
python tg.py init
```

Creates a sample config file at `~/.tg-vault.json` (or `config.json` next to the script, if present). Does not configure anything — use `setup` for that.

### `setup`

```bash
python tg.py setup
```

Interactive 4-step wizard that configures bot token, main channel, temp channel, and database. Recommended for first-time users.

### `bots`

```bash
python tg.py bots add <TOKEN>      # Add a bot
python tg.py bots list              # List configured bots
python tg.py bots remove <INDEX>   # Remove bot by 1-based index
```

You can add multiple bots to multiply throughput. Each bot has its own independent ~30 msg/sec quota, so N bots = N× throughput.

### `channels`

```bash
python tg.py channels set main <ID>    # Set main channel
python tg.py channels set temp <ID>    # Set temp channel (optional)
python tg.py channels show             # Show current channels
```

The **temp channel** is where the bot forwards messages during downloads (because bots can't send to themselves, and `copyMessage` doesn't return captions for channel messages). If not set, defaults to main.

### `test`

```bash
python tg.py test
```

Tests connectivity for all bots and channels, and verifies each bot's admin rights (post + delete messages).

### `upload`

```bash
# Single file
python tg.py upload file.zip

# With description and hashtags
python tg.py upload movie.mp4 --desc "Backup of my movie collection" --tag movies,2026

# Multiple files (bulk upload)
python tg.py upload file1.zip file2.zip file3.zip
python tg.py upload *.mp4 --tag movies

# Encrypted upload
python tg.py upload secret.txt --encrypt
# (will prompt for password)
# or:
export TG_VAULT_PASSWORD="my-secret"
python tg.py upload secret.txt --encrypt

# Resume interrupted upload
python tg.py upload big-file.iso --resume

# Disable compression
python tg.py upload already-compressed.zip --no-compress
```

Flags:
- `--desc, -d` — description text (applied to all files in bulk upload)
- `--tag, -t` — comma-separated hashtags (applied to all files)
- `--resume, -r` — resume an interrupted upload (uses `<filename>.resume.json`)
- `--encrypt, -e` — encrypt chunks with AES-256-GCM (requires password)
- `--password` — encryption password (alternative: `TG_VAULT_PASSWORD` env var)
- `--no-compress` — disable gzip compression (compression is on by default)

### `download`

```bash
# Single file
python tg.py download https://t.me/c/1234567890/42

# Multiple files (bulk download)
python tg.py download https://t.me/c/.../42 https://t.me/c/.../43

# From a links file
python tg.py download --links-file my_links.txt --output-dir ~/Downloads

# Resume interrupted download
python tg.py download https://t.me/c/.../42 --resume

# Specify output filename (single-file only)
python tg.py download https://t.me/c/.../42 --output renamed.zip

# Encrypted download (will prompt for password, or use TG_VAULT_PASSWORD)
python tg.py download https://t.me/c/.../42
```

Flags:
- `--links-file, -f` — text file containing one link per line (`#` comments supported)
- `--resume, -r` — resume an interrupted download
- `--output, -o` — output filename (only valid for single-file download)
- `--output-dir` — output directory (default: current directory)
- `--password` — decryption password (alternative: `TG_VAULT_PASSWORD` env var)

### `info`

```bash
python tg.py info https://t.me/c/1234567890/42
```

Fetches and displays the manifest metadata (name, size, parts, SHA256, description, hashtags, creation date, version) without downloading the file.

### `ls`

```bash
python tg.py ls                  # list last 10 manifests
python tg.py ls --limit 30       # list last 30 manifests
```

Lists recent manifest messages in the main channel by scanning backward from the latest message. Uses the `forwardMessage` + delete trick because the Bot API doesn't have `getHistory`.

### `delete`

```bash
python tg.py delete https://t.me/c/1234567890/42          # asks for confirmation
python tg.py delete https://t.me/c/1234567890/42 --force  # skip confirmation
```

Deletes a file's messages (description + all parts + manifest) from the channel. Also marks the file as deleted in the database if enabled.

### `cleanup`

```bash
python tg.py cleanup                # delete up to 100 recent messages from temp channel
python tg.py cleanup --max-count 500
```

Cleans up the temp channel by deleting recent messages. Useful if a previous download was interrupted and left forwarded messages behind.

### `db`

Database management commands. Requires the database to be enabled (`db enable`).

```bash
python tg.py db enable                          # enable SQLite DB
python tg.py db disable                         # disable DB (file kept on disk)
python tg.py db info                            # show DB info + stats
python tg.py db list [--limit N]                # list recent files
python tg.py db search "<query>"                # search by name/desc/tags
python tg.py db stats                           # show statistics
python tg.py db export [-o backup.json]         # export all records to JSON

# Advanced query with filters
python tg.py db query --name "movie" --min-size 1000000 --tag backup
python py.py db query --encrypted --since 2026-01-01 --sort size --asc
python tg.py db count --tag backup              # count matches without listing

# Download from DB
python tg.py db download 5                      # by file ID
python tg.py db download --ids 1,2,3            # multiple IDs
python tg.py db download --all-matching --tag backup  # all matches

# Channel sync (backup DB to Telegram)
python tg.py db sync                            # upload DB to sync channel
python tg.py db restore                         # download DB from sync channel
python tg.py db find                            # find latest DB backup in channel

# Maintenance
python tg.py db vacuum                          # reclaim unused space
python tg.py db find-orphans                    # find manifests not in DB
python tg.py db delete <ID> [--force]           # delete file from Telegram + DB
```

Query filters (for `query`, `count`, `download --all-matching`):
- `--name` — LIKE pattern for filename
- `--desc` — LIKE pattern for description
- `--tag` — exact tag match
- `--min-size` / `--max-size` — file size in bytes
- `--min-parts` / `--max-parts` — number of parts
- `--encrypted` / `--not-encrypted`
- `--compressed` / `--not-compressed`
- `--since` / `--until` — date (`YYYY-MM-DD` or unix timestamp)
- `--sort` — `name`, `size`, `parts`, `date`, `downloads` (default: `date`)
- `--asc` — sort ascending (default: descending)
- `--offset` — pagination offset

## Interactive menu

Run `python tg.py` with no arguments to enter the interactive menu:

```
=======================================================
    tg-vault — Telegram Cloud Storage
=======================================================
   bots: 1 | channel: -100...
   db: ✅
=======================================================
1. Upload file(s)
2. Upload file (resume)
3. Download by link(s)
4. Show file info
5. List recent files
6. Delete a file
7. Setup wizard (bot + channels + db)
8. Add bot
9. Set channel
10. Test connectivity
11. Cleanup temp channel
12. Database: list/search/stats
13. Exit
```

## GUI

A tkinter-based GUI is available:

```bash
python gui.py                    # uses default config
python gui.py --config /path     # custom config
```

The GUI has 4 tabs:
- **Upload** — drag files, set description/tags/encryption
- **Download** — paste manifest links, choose output dir
- **Browse** — list/search DB, multi-select download
- **Settings** — proxy configuration, view config

All operations run in background threads to keep the UI responsive. Proxy support includes system proxy or custom HTTP/SOCKS proxy.

## Examples

See the [`examples/`](../examples) directory for ready-to-use scripts:

| Script | Description |
|--------|-------------|
| `backup_directory.py` | Recursively back up a directory tree |
| `bulk_upload.py` | Bulk upload wrapper |
| `bulk_download.py` | Bulk download with `--links-file` |
| `encrypted_upload.py` | Encrypted upload wrapper |
| `parallel_uploads.py` | Parallel uploads via subprocesses |
| `db_search.py` | Scriptable DB search (JSON output) |
| `download_all.py` | Download all manifests in a channel |

## Troubleshooting

### "Bot lacks permissions (admin required)"

The bot needs to be an admin in both main and temp channels, with **Post messages** and **Delete messages** rights. Run `python tg.py test` to verify.

### FloodWait errors

If you see `⏳ FloodWait @bot: Ns...`, the bot is being rate-limited. Solutions:
- Add more bots (each has its own quota)
- Increase `upload_delay` / `download_delay` in config
- Reduce `parallel_workers`

### "file too large (> 20MB) Bot cannot download via getFile"

This means a chunk somehow exceeded 20 MB. Shouldn't happen with default 19 MB chunks. If you manually set `chunk_size_mb` higher than 19, lower it back.

### SHA256 mismatch on download

Possible causes:
- Temp channel had stale forwarded messages (run `python tg.py cleanup`)
- Network corruption (retry)
- The file was corrupted on Telegram's side (rare)

The partial file is kept as `<filename>.downloading` for inspection.

### "Database is not enabled"

Run `python tg.py db enable` to create the SQLite database. The DB is optional but recommended — it stores metadata, search history, and download logs.

### Resume not working

- **Upload resume**: requires `<filename>.resume.json` in the current directory. If you moved the file or changed its name, resume won't recognize it.
- **Download resume**: requires `<filename>.downloading` in the output directory. The file is truncated to the last completed chunk boundary before resuming.

### Password not accepted for encrypted download

The manifest stores a password verification hash. If your password doesn't match, you'll see `❌ Wrong password (verification hash mismatch)`. Note: the hash is computed with PBKDF2 600k iterations, so brute-forcing is impractical.

If you forgot the password, **the file cannot be recovered** — the key is never stored anywhere.
