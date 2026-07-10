# Telegram Bot API Limits Reference

This document summarizes the **hard limits** and **soft rate limits** of the Telegram Bot API that affect tg-vault's design.

## Hard Size Limits (Cloud Bot API)

| Item | Limit | Source |
|------|-------|--------|
| `sendDocument` upload (multipart) | **50 MB** | [Bot API: Sending files](https://core.telegram.org/bots/api#sending-files) |
| `sendDocument` via HTTP URL | 20 MB | same |
| `getFile` download | **20 MB** | [Bot API: getFile](https://core.telegram.org/bots/api#getfile) — *"The maximum file size to download is 20 MB"* |
| `file_id` resend (file already on Telegram) | up to 2000 MB (4000 MB with Premium) | [tdlib/telegram-bot-api#583](https://github.com/tdlib/telegram-bot-api/issues/583) |
| Caption length | 0–1024 chars | [Bot API: sendDocument](https://core.telegram.org/bots/api#senddocument) |
| Message text length | 1–4096 chars | [Bot API: sendMessage](https://core.telegram.org/bots/api#sendmessage) |
| Filename (`file_name`) | Not officially documented; community practice: ≤ 64 chars | [Bot API: Document](https://core.telegram.org/bots/api#document) |

## Soft Rate Limits (per-bot)

Source: [Telegram Bots FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)

| Scope | Limit |
|-------|-------|
| Same chat | ~1 msg/sec |
| Same group | ≤ 20 msgs/min |
| Different chats / bulk (free) | ~30 msgs/sec aggregate |
| Paid broadcasts (opt-in via @BotFather) | up to 1000 msgs/sec (0.1 Stars/msg over free) |

When limits are exceeded, Telegram returns HTTP **429** with `parameters.retry_after` (seconds).

## Other Constraints

- **Bot messaging itself**: ❌ Not possible. A bot cannot send messages to its own chat.
  - Workaround: use a private channel/group where the bot is the only member, or a dedicated user chat.

- **`copyMessage` on channel messages**: ✅ Works, but **does NOT return the caption** in the response for channel messages (Telegram quirk).
  - Workaround: use `forwardMessage` instead (does return caption), but adds a "Forwarded from" header.

- **`forwardMessage` from a channel**: Bot must be a member of the source chat. No admin rights needed to forward *out*.

- **`deleteMessage` in channels**: Bot must be an admin with the "Delete messages" right.

- **`reply_to_message_id`**: When forwarding/copying with `reply_to_message_id`, the reply is to a message in the *destination* chat, not the source.

## Multi-Bot Workarounds

- Each bot token has its **own independent ~30 msg/sec quota** → running N bots in parallel multiplies throughput ~N×.
- Each bot should have its own session/poller (a token is bound to one `getUpdates` stream).
- Practical limit: ~20 bots per Telegram user account via @BotFather (`/newbot`).
- For higher quotas on a single bot, contact **@BotSupport** to request a limit increase.

## Local Bot API Server

Self-hosting the [Local Bot API Server](https://github.com/tdlib/telegram-bot-api) lifts the limits:

| | Cloud | Local Server |
|---|---|---|
| Upload (`sendDocument` multipart) | 50 MB | **2000 MB** |
| Download (`getFile`) | 20 MB | **No size limit** |
| `file_path` returned | relative (must re-download via HTTPS) | **absolute local path** (no re-download) |
| Webhook | HTTPS + public IPs only | any HTTP URL, local IPs, up to 100000 connections |

**Auth requirement**: Running the server requires `--api-id` and `--api-hash` (from https://my.telegram.org). Client requests still authenticate with the bot token.

## References

- [Telegram Bot API Reference](https://core.telegram.org/bots/api)
- [Telegram Bots FAQ — hitting limits](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)
- [Local Bot API Server (tdlib/telegram-bot-api)](https://github.com/tdlib/telegram-bot-api)
- [grammy.dev — flood control](https://grammy.dev/advanced/flood)
- [gramio.dev — rate limits](https://gramio.dev/rate-limits)
