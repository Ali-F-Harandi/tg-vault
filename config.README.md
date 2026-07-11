# tg-vault Configuration

This directory contains configuration files for tg-vault.

## Files

| File | Purpose | Git tracked? |
|------|---------|-------------|
| `config.sample.json` | Sample config — copy this and fill in your values | ✅ Yes |
| `config.json` | Your actual config with real tokens | ❌ No (gitignored) |
| `~/.tg-vault.json` | Default config location (if no `--config` specified) | ❌ No |

## Setup

### Option A: Use default location (`~/.tg-vault.json`)

```bash
python tg.py setup
```

The setup wizard will create `~/.tg-vault.json` automatically.

### Option B: Use a custom config file

1. Copy the sample:
   ```bash
   cp config.sample.json config.json
   ```

2. Edit `config.json` and fill in:
   - `bots[].token` — your bot token from @BotFather
   - `channels.main` — channel ID where files are stored
   - `channels.temp` — channel ID for temp forwards + DB backup

3. Use it with `--config`:
   ```bash
   python tg.py --config config.json upload file.zip
   python tui.py --config config.json
   ```

## Config Fields

| Field | Type | Description |
|-------|------|-------------|
| `bots` | array | List of bot objects with `token` and `username` |
| `channels.main` | string/int | Channel ID for file storage (e.g. `-1001234567890` or `@username`) |
| `channels.temp` | string/int | Channel ID for temp forwards + DB backup sync |
| `chunk_size_mb` | int | Chunk size in MB (default: 19, must be ≤ 20) |
| `upload_delay` | float | Delay between uploads in seconds (default: 0.3) |
| `download_delay` | float | Delay between downloads in seconds (default: 0.2) |
| `parallel_workers` | int | Parallel download workers (default: 4) |
| `db_enabled` | bool | Enable SQLite database (default: false) |
| `db_path` | string\|null | Path to DB file (null = next to config file) |
| `db_sync_channel` | string\|null | Channel for DB backup sync (null = use temp channel) |
| `db_sync_msg_id` | int\|null | Message ID of last DB sync (managed automatically) |
| `db_sync_multipart` | bool | Whether last DB sync was multi-part (managed automatically) |
| `db_auto_sync` | bool | Auto-sync DB after every upload/download (default: true) |
| `version` | int | Config format version (don't change manually) |

## Security

⚠️ **NEVER commit `config.json` to Git!** It contains your bot token.

The `.gitignore` file already excludes:
- `config.json`
- `.tg-vault.json`
- `*.db` (database files)
- `*.resume.json` (resume state)

Only `config.sample.json` is committed, which has placeholder values.
