# Architecture Overview

This document describes the internal architecture of tg-vault, the rationale behind key design decisions, and how the moving pieces fit together.

## High-level diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                          tg-vault                                    │
│                                                                      │
│  ┌──────────────────┐    ┌──────────────────┐    ┌────────────────┐ │
│  │   CLI (argparse) │ ←→ │ Interactive Menu │    │   GUI (tk)     │ │
│  │  tg_vault/cli.py │    │tg_vault/         │    │  gui/app.py    │ │
│  │                  │    │ interactive.py   │    │                │ │
│  └────────┬─────────┘    └────────┬─────────┘    └────────────────┘ │
│           │                       │                                   │
│           └───────────┬───────────┘                                   │
│                       ↓                                               │
│              ┌─────────────────┐                                     │
│              │     Config      │  (~/.tg-vault.json)                 │
│              │  - bots[]       │                                     │
│              │  - main_channel │                                     │
│              │  - temp_channel │                                     │
│              │  - chunk_size   │                                     │
│              │  - db_*         │                                     │
│              └────────┬────────┘                                     │
│                       ↓                                               │
│              ┌─────────────────┐                                     │
│              │    BotPool      │  (round-robin, thread-safe)         │
│              │  ┌───┐ ┌───┐    │                                     │
│              │  │B1 │ │B2 │... │  Each Bot has:                      │
│              │  └───┘ └───┘    │   - requests.Session (pooling)     │
│              └─────┬───────────┘   - rate limiter (50ms min)        │
│                    │               - request/error counters          │
│        ┌───────────┴───────────┐                                    │
│        ↓                       ↓                                     │
│  ┌──────────┐            ┌──────────┐                               │
│  │ Uploader │            │Downloader │                               │
│  │          │            │           │                               │
│  │ - parts  │            │ - parts   │                               │
│  │   chain  │            │   (paral) │                               │
│  │ - desc   │            │ - SHA256  │                               │
│  │   msg    │            │   verify  │                               │
│  │ - mani-  │            │ - resume  │                               │
│  │   fest   │            │           │                               │
│  │ - resume │            │           │                               │
│  └────┬─────┘            └─────┬────┘                               │
│       │                        │                                     │
└───────┼────────────────────────┼─────────────────────────────────────┘
        ↓                        ↓
  ┌──────────────────────────────────────────┐
  │           Telegram Bot API                │
  │  https://api.telegram.org/bot<token>/     │
  └──────────────────────────────────────────┘
        ↓                        ↓
  ┌──────────────────────────────────────────┐
  │      Your Telegram Channel(s)             │
  │                                           │
  │  Main channel:                            │
  │    [Description]                          │
  │      ↓ reply                              │
  │    [Part 1/4]                             │
  │      ↓ reply                              │
  │    [Part 2/4]                             │
  │      ↓ reply                              │
  │    [Part 3/4]                             │
  │      ↓ reply                              │
  │    [Part 4/4]                             │
  │      ↓ reply                              │
  │    [Manifest]  ← download link points here│
  │                                           │
  │  Temp channel (optional, defaults to main)│
  │    [forwarded copy] → deleted after use   │
  └──────────────────────────────────────────┘
```

## Package layout

```
tg_vault/
├── __init__.py          # Re-exports public API
├── __main__.py          # python -m tg_vault entry
├── cli.py               # argparse CLI + main()
├── commands.py          # cmd_* functions
├── interactive.py       # Interactive menu
├── config.py            # Config class
├── bot_pool.py          # Bot + BotPool (Bot API)
├── pyrogram_bot.py      # HybridBot (Pyrogram + Bot API, optional)
├── uploader.py          # Uploader class
├── downloader.py        # Downloader class
├── crypto.py            # AES-256-GCM encryptor
├── compression.py       # Smart gzip
├── chunk_header.py      # TGV1 header
├── db.py                # SQLite database
├── db_sync.py           # DB backup/restore
├── constants.py         # VERSION + limits
└── utils.py             # Helpers + ProgressTracker
```

Two backward-compatibility shims (`tg.py`, `gui.py`) live at the project root so that `python tg.py <cmd>` and `python gui.py` keep working after the reorganization.

## Key design decisions

### 1. Why 19 MB chunks (not 20)?

Telegram's `getFile` limit is **exactly 20 MB**. Using exactly 20 MB risks hitting the limit due to overhead or rounding. 19 MB provides a safe margin while maximizing chunk size (fewer chunks = fewer API calls = faster).

### 2. Why `forwardMessage` instead of `copyMessage`?

`copyMessage` does NOT return the caption for channel messages — this is a Telegram quirk we discovered during testing. Since we need to parse the caption (to identify manifests), we use `forwardMessage` instead.

The downside: forwarded messages get a "Forwarded from" header. But since we delete them immediately from the temp channel, this doesn't matter.

### 3. Why forward to a temp channel (not the bot's own chat)?

A bot **cannot send messages to itself** in Telegram. So we cannot forward to `bot.id` to extract `file_id`s.

Workaround: forward to the **temp channel** (which can be the same as the main channel if no separate temp is configured). The bot must be an admin there with delete rights.

### 4. Why round-robin per part (not per request)?

For uploads, parts must be sent in order (to maintain the reply chain). Within that constraint, we rotate bots between parts, so each bot handles roughly 1/N of the parts.

For downloads, parts are independent — we download them in **parallel** using `ThreadPoolExecutor`, with each thread grabbing the next available bot from the pool.

### 5. Why store resume state in a separate JSON file?

Storing resume state in `<filename>.resume.json` (not in the manifest or channel) means:
- Resume works even if the manifest was never sent (upload interrupted before manifest).
- The file is local — no Telegram API call needed to check resume state.
- The file is small and easy to inspect manually.

### 6. Why a UUID session tag in every caption?

Each upload/download session gets a unique 8-char UUID. This tag is included in every chunk's caption, enabling:
- Multiple `tg-vault` processes to run in parallel without conflicts.
- Temp channel cleanup only deletes messages from the current session.
- Easier debugging (grep for session ID in logs).

### 7. Why a self-describing chunk header (TGV1)?

Each chunk starts with a 40-byte binary header containing the file's SHA256 prefix, chunk index, total chunks, original size, and flags (compressed/encrypted). This means:
- A chunk can be identified **without consulting the database**.
- If the manifest is lost, the chunks can still be recognized and reassembled.
- The compression/encryption flags are embedded — no need to check the manifest for every chunk.

### 8. Why deterministic IVs for AES-GCM?

In v8, the IV for each chunk is derived deterministically from its chunk index: `iv = (part_num - 1).to_bytes(12, "big")`. This avoids storing per-chunk IVs in the manifest. The (key, IV) pair is never reused because:
- The key is unique per file (random 32-byte salt per encryption).
- The chunk index is unique within a file.

This is safe per NIST SP 800-38D as long as the key is never reused with the same IV.

### 9. Why a hybrid Bot API + Pyrogram approach?

Starting with v8.4.0, tg-vault supports an optional **Pyrogram hybrid mode**. The `HybridBot` class (`tg_vault/pyrogram_bot.py`) uses:
- **Bot API** (`requests`) for all small operations (sendMessage, deleteMessage, forwardMessage, getMe) — faster, simpler, no event loop overhead
- **Pyrogram** (MTProto) for large file operations — bypasses the 50 MB upload / 20 MB download Bot API limits, supporting up to 2 GB chunks

**Why not use Pyrogram for everything?**
- Bot API is simpler for small payloads (no asyncio event loop needed)
- Bot API is faster for tiny operations (no MTProto handshake overhead)
- Pyrogram adds a dependency and an event loop thread per bot

**Why not use Bot API for everything?**
- 50 MB upload limit → files need many small chunks
- 20 MB download limit → requires `forwardMessage` to temp channel (a bot can't `getFile` directly from a channel it doesn't own)
- More chunks = more API calls = slower + more FloodWait risk

**How HybridBot works:**
1. On `init_info()`, it starts a Pyrogram client in a dedicated thread with a persistent event loop
2. `request("sendDocument", ...)` checks file size — if >45 MB, routes to Pyrogram; otherwise uses Bot API
3. `download_media(chat_id, msg_id)` uses Pyrogram directly — no `forwardMessage` needed
4. All other requests (sendMessage, deleteMessage, etc.) go through Bot API
5. If Pyrogram fails to start (not installed, bad credentials), it falls back to Bot API only with a warning

**Thread safety:** Each `HybridBot` has its own event loop thread. The `_pyro_lock` serializes Pyrogram calls to prevent concurrent access to the same Pyrogram client (Pyrogram is not thread-safe for concurrent calls on the same client).

**Event loop management:** Pyrogram's sync wrappers use `asyncio.get_event_loop()` which conflicts with our dedicated loop. We bypass this by calling `method.__wrapped__(self._pyro, ...)` to access the raw coroutine, then run it via `asyncio.run_coroutine_threadsafe(coro, self._loop)`.

## Pipeline

### Upload pipeline (per chunk)

```
raw_chunk
   ↓
compress_data()         (optional — skipped for already-compressed formats)
   ↓
encrypt_chunk_with_iv() (optional — AES-256-GCM, IV = chunk_index)
   ↓
prepend TGV1 header     (40 bytes: magic, version, flags, index, total, size, sha256_prefix)
   ↓
sendDocument            (with reply_to previous message)
```

### Download pipeline (per chunk, in parallel)

```
forwardMessage to temp channel
   ↓
getFile → HTTP GET
   ↓
deleteMessage (temp forward)
   ↓
strip TGV1 header       (40 bytes)
   ↓
decrypt_chunk()         (optional — AES-256-GCM)
   ↓
decompress_data()       (optional — gzip)
   ↓
write to .downloading file (in order)
```

After all chunks are written, the file's SHA256 is verified against the manifest.

## Error handling

| Error | Handling |
|-------|----------|
| FloodWait (429) | Wait `retry_after` seconds, then retry |
| 5xx server error | Exponential backoff (2s, 4s, 8s, 16s, 32s) |
| Network error | Exponential backoff |
| 4xx (other) | Return error to caller, no retry |
| KeyboardInterrupt | Save resume state, clean up temp messages |
| SHA256 mismatch | Keep `.downloading` file for inspection |
| Wrong encryption password | Fail-fast via password verification hash (no decryption attempt) |
| Decryption tamper | `InvalidTag` exception → abort |

## Thread safety

- `BotPool._counter` protected by `threading.Lock`
- `Bot._last_request_time` protected by `threading.Lock` (per-bot rate limiting)
- `HybridBot._pyro_lock` protects Pyrogram calls (each bot has its own event loop thread)
- `Downloader._temp_msg_ids` protected by `threading.Lock`
- `ProgressTracker.current` protected by `threading.Lock`
- `Downloader` write loop protected by `write_lock` (per-file)

## File layout (transient files)

```
~/.tg-vault.json                    # Config file
<filename>.resume.json              # Upload resume state (transient)
<filename>.downloading              # Partial download (transient)
<filename>                          # Completed download
<filename>.manifest.json            # Manifest (only in channel, never local)
tg-vault.db                         # SQLite database (optional)
tg-vault.db.backup                  # DB backup before restore (transient)
tg-vault.db.restoring               # DB restore in progress (transient)
```

## Manifest format

The manifest is sent as a **text message** (preferred) or as a JSON file (fallback when the manifest is too large for Telegram's 4096-char text limit).

Text manifest format:
```
TG_VAULT_MANIFEST|<filename>|<total_parts>|<sha256_prefix>
{
  "name": "...",
  "size": 12345,
  "total_parts": 4,
  "chunk_size": 19922944,
  "message_ids": [10, 11, 12, 13],
  "sha256": "...",
  "channel_id": -100...,
  "description_msg_id": 9,
  "description": "...",
  "hashtags": ["tag1", "tag2"],
  "session_id": "abcd1234",
  "version": 8,
  "created_at": 1234567890.123,
  "encrypted": false,
  "compressed": false,
  "has_chunk_header": true,
  "manifest_type": "text",
  "manifest_message_id": 14
}
```

If encrypted, additional fields:
```json
{
  "encryption_salt": "<base64>",
  "encryption_algorithm": "aes-256-gcm",
  "encryption_kdf": "pbkdf2-sha512-600k",
  "password_hash": "<hex>",
  "sha256_prefix_b64": "<base64>"
}
```

## Database schema

Four tables (see `tg_vault/db.py` for full DDL):

- **`files`** — one row per uploaded file (with v8 encryption/compression fields)
- **`downloads`** — download history
- **`chunks`** — per-chunk metadata (mirror of `message_ids`, queryable)
- **`tags`** — many-to-many tag organization

Indexes on `sha256`, `name`, `uploaded_at`, `status`, `encrypted`, and on the `tags` and `chunks` foreign keys for fast lookups.
