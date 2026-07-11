# Configuration Reference

This document describes the tg-vault config file format, location, and all available options.

## Config file location

tg-vault looks for the config file in this order:

1. **`--config <path>`** CLI argument (highest priority)
2. **`config.json`** in the same directory as the package (for portable setups)
3. **`~/.tg-vault.json`** (default, created by `tg.py init`)

The first existing file is used.

## Creating the config

### Option A: Interactive wizard (recommended)

```bash
python tg.py init       # creates the file
python tg.py setup      # fills it in interactively
```

### Option B: Manual commands

```bash
python tg.py init
python tg.py bots add 123456789:ABC-DEF...
python tg.py channels set main -1001234567890
python tg.py channels set temp -1009876543210  # optional
python tg.py db enable                          # optional
python tg.py test                               # verify
```

### Option C: Edit the file directly

Copy `config.sample.json` to `~/.tg-vault.json` and edit:

```bash
cp config.sample.json ~/.tg-vault.json
$EDITOR ~/.tg-vault.json
```

## Config file format

```json
{
  "bots": [
    {
      "token": "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
      "username": "my_bot_username"
    }
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
  "db_path": null,
  "db_sync_channel": null,
  "db_sync_msg_id": null,
  "db_sync_multipart": false,
  "db_auto_sync": true,
  "version": 8
}
```

## Field reference

### `bots` (required)

Array of bot objects. Each bot has:

| Field | Type | Description |
|-------|------|-------------|
| `token` | string | Bot token from @BotFather (format: `123456789:ABC-DEF...`) |
| `username` | string | Bot username (filled in automatically by `bots add`) |

You can add multiple bots to multiply throughput. Each bot has its own independent ~30 msg/sec quota.

```json
"bots": [
  {"token": "111:AAA...", "username": "bot1"},
  {"token": "222:BBB...", "username": "bot2"},
  {"token": "333:CCC...", "username": "bot3"}
]
```

### `channels` (required)

| Field | Type | Description |
|-------|------|-------------|
| `channels.main` | int/string | Main channel ID (storage channel) |
| `channels.temp` | int/string | Temp channel ID (for forwarded messages during download) |

Channel ID formats:
- **Private channel**: `-1001234567890` (integer, starts with `-100`)
- **Public channel**: `@mychannel_username` (string)

The bot must be an admin in both channels with **Post messages** and **Delete messages** rights.

If `temp` is not set or equals `main`, the main channel is used for temp forwards.

> **Why a temp channel?** A bot cannot send messages to itself in Telegram, so we can't forward to `bot.id` to extract `file_id`s. Instead, we forward to the temp channel, download the file, then delete the forward.

### `chunk_size_mb` (default: 19)

Size of each chunk in megabytes. Must be ≤ 19 to stay under Telegram's 20 MB `getFile` download limit.

Larger chunks = fewer API calls = faster. Smaller chunks = more parallelism but more overhead.

### `upload_delay` (default: 0.3)

Seconds to wait between chunk uploads. Helps avoid FloodWait.

### `download_delay` (default: 0.2)

Seconds to wait between chunk downloads (per worker).

### `parallel_workers` (default: 4)

Number of concurrent download workers. Each worker uses one bot from the pool, so the effective parallelism is `min(parallel_workers, len(bots))`.

### `db_enabled` (default: false)

Whether to use the SQLite database for metadata storage. When enabled, every upload/download is automatically logged.

### `db_path` (default: null)

Path to the SQLite database file. If `null`, defaults to:
1. `<config_dir>/tg-vault.db` (next to the config file)
2. `~/.tg-vault.db` (home directory)

### `db_sync_channel` (default: null)

Channel where the DB file itself is backed up. If `null`, defaults to `temp_channel`.

### `db_sync_msg_id` (default: null)

Message ID of the latest DB backup in the sync channel. Updated automatically by `db sync` and `db restore`. Used to skip channel scanning on restore.

### `db_sync_multipart` (default: false)

Whether the last DB backup was multi-part (DB > 19 MB). Set automatically.

### `db_auto_sync` (default: true)

If true, automatically sync the DB to the sync channel after every upload/download that modifies it. Silent unless there's an error.

### `version` (read-only)

Config file format version. Currently `8`. Do not edit manually.

## Environment variables

| Variable | Description |
|----------|-------------|
| `TG_VAULT_PASSWORD` | Encryption password (alternative to `--password`) |

## Getting a channel ID

### Private channel

1. Forward a message from the channel to [@userinfobot](https://t.me/userinfobot)
2. The bot replies with the channel ID (looks like `-1001234567890`)

Alternatively:
1. Open the channel in the Telegram web client
2. Look at the URL: `https://web.telegram.org/a/#-1001234567890`
3. The number after `#` is the channel ID

### Public channel

Just use `@username` (with the `@` prefix).

## Example configs

### Minimal (single bot, single channel, no DB)

```json
{
  "bots": [
    {"token": "123:ABC...", "username": "my_bot"}
  ],
  "channels": {
    "main": -1001234567890
  }
}
```

### Full (3 bots, separate temp channel, DB with auto-sync)

```json
{
  "bots": [
    {"token": "111:AAA...", "username": "bot1"},
    {"token": "222:BBB...", "username": "bot2"},
    {"token": "333:CCC...", "username": "bot3"}
  ],
  "channels": {
    "main": -1001111111111,
    "temp": -1002222222222
  },
  "chunk_size_mb": 19,
  "parallel_workers": 4,
  "db_enabled": true,
  "db_path": "/home/user/.tg-vault.db",
  "db_sync_channel": -1003333333333,
  "db_auto_sync": true,
  "version": 8
}
```

### Portable (config next to script)

Place `config.json` in the same directory as `tg.py`:

```json
{
  "bots": [{"token": "...", "username": "..."}],
  "channels": {"main": -100...},
  "db_enabled": true,
  "db_path": "./tg-vault.db"
}
```

This is useful for running tg-vault from a USB drive.
