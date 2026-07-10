# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Client-side AES-256-GCM encryption
- Local Bot API Server support (`api_url` per bot)
- HTTP Range streaming for video files
- Docker image + REST API wrapper
- TUI (Textual / Rich) for the interactive menu

## [v6.0.0] — 2026-07-10

### Added
- **Multi-bot support** with thread-safe round-robin `BotPool`
- **Parallel chunk download** via `ThreadPoolExecutor`
- **Per-bot rate limiting** (50 ms min interval, FloodWait-safe)
- **Connection pooling** (`requests.Session` per bot)
- **Description message** before parts (name + size + SHA256 + custom text + hashtags)
- **Manifest message** after parts (reply to last part, JSON metadata)
- **Resume** for both upload (`.resume.json`) and download (`.downloading` truncation)
- **Filename sanitization** (removes illegal chars, truncates to 60 chars)
- **Caption/text length validation** (Telegram limits: 1024/4096 chars)
- **Progress bar** with speed and ETA
- **`--output` and `--output-dir` flags** for download
- **`ls` command** to list recent manifest files in main channel
- **`delete` command** to delete a file's messages (description + parts + manifest)
- **Graceful `Ctrl+C` cleanup** — temp messages are deleted on interrupt
- **Concurrency-safe sessions** — each session gets a UUID tag in every caption
- **Config file** at `~/.tg-vault.json` for bots, channels, defaults
- **Interactive menu** with 11 options
- **Bilingual README** (English + Persian)
- **Examples** directory with `parallel_uploads.py`, `backup_directory.py`, `download_all.py`
- **Architecture docs** explaining key design decisions

### Fixed
- Switched from `copyMessage` to `forwardMessage` because `copyMessage` does NOT return caption for channel messages (Telegram quirk)
- Handle FloodWait (429) by waiting `retry_after` seconds, then retrying
- Handle 5xx server errors with exponential backoff

### Security
- Config file with tokens is gitignored
- Tokens are never logged or printed in full

## [v5.0.0] — 2026-07-10 (internal, not tagged)

Initial architecture with multi-bot, config, temp channel, description/end messages, resume.

## [v4.0.0] — 2026-07-10 (internal, not tagged)

Added reply-chain upload + manifest message + link-based download.

## [v3.0.0] — 2026-07-10 (internal, not tagged)

Added link parsing, manifest message, single-file download.

## [v2.0.0] — 2026-07-10 (internal, not tagged)

Added SHA256, JSON metadata, FloodWait handling, resume, progress bar.

## [v1.0.0] — 2026-07-10 (internal, not tagged)

Original script by user. 15 MB chunks, fragile `first_message_id` calculation, no checksum.
