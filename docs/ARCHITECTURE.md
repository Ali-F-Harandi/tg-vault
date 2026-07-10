# Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          tg-vault                                    │
│                                                                      │
│  ┌──────────────────┐    ┌──────────────────┐    ┌────────────────┐ │
│  │   CLI (argparse) │ ←→ │ Interactive Menu │    │   Examples     │ │
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

## Key Design Decisions

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

## Error Handling

| Error | Handling |
|-------|----------|
| FloodWait (429) | Wait `retry_after` seconds, then retry |
| 5xx server error | Exponential backoff (2s, 4s, 8s, 16s, 32s) |
| Network error | Exponential backoff |
| 4xx (other) | Return error to caller, no retry |
| KeyboardInterrupt | Save resume state, clean up temp messages |
| SHA256 mismatch | Keep `.downloading` file for inspection |

## Thread Safety

- `BotPool._counter` protected by `threading.Lock`
- `Bot._last_request_time` protected by `threading.Lock` (per-bot rate limiting)
- `Downloader._temp_msg_ids` protected by `threading.Lock`
- `ProgressTracker.current` protected by `threading.Lock`

## File Layout

```
~/.tg-vault.json                    # Config file
<filename>.resume.json              # Upload resume state (transient)
<filename>.downloading              # Partial download (transient)
<filename>                          # Completed download
<filename>.manifest.json            # Manifest (only in channel, never local)
```
