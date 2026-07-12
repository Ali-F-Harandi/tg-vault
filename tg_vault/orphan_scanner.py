"""
Orphan scanner for tg-vault.

Finds messages in the channel(s) that are NOT tracked in the ``files``
table, and stores them in the local ``orphans`` table.

An orphan is ANY message in the channel that:
  - Has content (text, photo, video, document, audio, voice, sticker,
    animation, video_note, etc.) — i.e. NOT a service message
  - AND whose ``message_id`` is NOT in the database's known set

Each orphan = ONE individual message. The user can select individual
orphans and delete them.

Scanning strategy:
  - Scan in batches of ``batch_size`` messages (default 500).
  - Pause ``delay`` seconds between batches to avoid FloodWait.
  - ``max_scan`` caps the total messages scanned.
  - Already-known message_ids are skipped without an API call.
"""

import json
import time

from .constants import MANIFEST_PREFIX
from .utils import build_share_link, format_size
from .bot_pool import BotPool


# Message types we detect as orphans (everything except service messages)
# Each entry: (telegram_field, type_label, has_file_size)
MESSAGE_TYPES = [
    ("document",    "📄", True),   # generic file
    ("photo",       "🖼️", True),   # photo
    ("video",       "🎬", True),   # video
    ("animation",   "🎞️", True),   # GIF / animation
    ("audio",       "🎵", True),   # audio file
    ("voice",       "🎤", True),   # voice message
    ("video_note",  "⭕", True),   # round video note
    ("sticker",     "🏷️", True),   # sticker
    ("contact",     "👤", False),  # contact
    ("location",    "📍", False),  # location
    ("venue",       "📍", False),  # venue
    ("poll",        "📊", False),  # poll
    ("dice",        "🎲", False),  # dice/game
]


def _parse_manifest_header(text_or_caption):
    """Parse the first line of a manifest message.

    Returns (name, total_parts, sha256_prefix) or None if not a manifest.
    """
    if not text_or_caption or not text_or_caption.startswith(MANIFEST_PREFIX):
        return None
    first_line = text_or_caption.split("\n", 1)[0]
    parts = first_line.split("|")
    if len(parts) < 4:
        return None
    try:
        return parts[1], int(parts[2]), parts[3]
    except (IndexError, ValueError):
        return None


def _classify_message(msg_data):
    """Classify a Telegram message into an orphan record.

    Returns a dict with keys: name, file_size, is_manifest, message_type,
    total_parts, sha256_prefix, manifest_text — or None if the message
    is a service message / empty / not an orphan candidate.
    """
    text = msg_data.get("text", "")
    caption = msg_data.get("caption", "")

    # Check for manifest text first (text message starting with TG_VAULT_MANIFEST)
    if text and text.startswith(MANIFEST_PREFIX):
        manifest_info = _parse_manifest_header(text)
        if manifest_info:
            name, total_parts, sha256_prefix = manifest_info
            return {
                "name": name,
                "file_size": None,
                "is_manifest": True,
                "message_type": "manifest",
                "total_parts": total_parts,
                "sha256_prefix": sha256_prefix,
                "manifest_text": text.split("\n", 1)[0],
            }

    # Check for document with manifest caption (old-style file manifest)
    doc = msg_data.get("document")
    if doc:
        name = doc.get("file_name", "?")
        file_size = doc.get("file_size")
        manifest_info = _parse_manifest_header(caption)
        if manifest_info:
            # old-style file manifest
            mname, total_parts, sha256_prefix = manifest_info
            return {
                "name": mname,
                "file_size": file_size,
                "is_manifest": True,
                "message_type": "manifest",
                "total_parts": total_parts,
                "sha256_prefix": sha256_prefix,
                "manifest_text": caption.split("\n", 1)[0] if caption else "",
            }
        # Regular document
        return {
            "name": name,
            "file_size": file_size,
            "is_manifest": False,
            "message_type": "document",
            "total_parts": None,
            "sha256_prefix": None,
            "manifest_text": None,
        }

    # Check other media types
    for field, label, has_size in MESSAGE_TYPES[1:]:  # skip "document" (already handled)
        media = msg_data.get(field)
        if media:
            name = None
            file_size = None
            if isinstance(media, dict):
                # For photos, file_size is in the largest size entry
                if field == "photo":
                    # photo is a list of PhotoSize, pick the largest
                    sizes = media if isinstance(media, list) else [media]
                    if sizes:
                        largest = max(sizes, key=lambda s: s.get("file_size", 0))
                        file_size = largest.get("file_size")
                else:
                    file_size = media.get("file_size")
                # Try to get a name
                name = media.get("file_name") or media.get("file_id", "")[:20]
            elif isinstance(media, list) and media:
                # photo is a list
                if field == "photo":
                    largest = max(media, key=lambda s: s.get("file_size", 0))
                    file_size = largest.get("file_size")
            # Friendly display name
            if not name:
                type_names = {
                    "photo": "Photo", "video": "Video", "animation": "GIF/Animation",
                    "audio": "Audio", "voice": "Voice message",
                    "video_note": "Video note", "sticker": "Sticker",
                    "contact": "Contact", "location": "Location",
                    "venue": "Venue", "poll": "Poll", "dice": "Dice/Game",
                }
                name = type_names.get(field, field.title())
            return {
                "name": name,
                "file_size": file_size,
                "is_manifest": False,
                "message_type": field,
                "total_parts": None,
                "sha256_prefix": None,
                "manifest_text": None,
            }

    # Plain text message (no media)
    if text and text.strip():
        # Truncate for display
        preview = text.strip()[:50]
        if len(text.strip()) > 50:
            preview += "..."
        return {
            "name": f"Text: {preview}",
            "file_size": None,
            "is_manifest": False,
            "message_type": "text",
            "total_parts": None,
            "sha256_prefix": None,
            "manifest_text": None,
        }

    # Caption-only message (e.g. a photo without text, but caption is separate)
    if caption and caption.strip():
        preview = caption.strip()[:50]
        if len(caption.strip()) > 50:
            preview += "..."
        return {
            "name": f"Caption: {preview}",
            "file_size": None,
            "is_manifest": False,
            "message_type": "caption",
            "total_parts": None,
            "sha256_prefix": None,
            "manifest_text": None,
        }

    # Service message or empty message — not an orphan
    return None


def _build_known_set(db):
    """Build a set of all message_ids known to the database.

    This includes:
      - All part message_ids (from files.message_ids JSON array)
      - All manifest_msg_id values
      - All description_msg_id values
      - All message_ids extracted from share_link (safety: even if
        share_link points to a different message than manifest_msg_id,
        we should never delete it)
    """
    known = set()
    all_files = db.list_files(limit=100000, status=None)
    for f in all_files:
        try:
            ids = json.loads(f.get("message_ids", "[]"))
            known.update(ids)
        except Exception:
            pass
        if f.get("manifest_msg_id"):
            known.add(f["manifest_msg_id"])
        if f.get("description_msg_id"):
            known.add(f["description_msg_id"])
        # Also add share_link message_id as a safety measure
        link = f.get("share_link") or ""
        if link:
            try:
                link_msg = int(link.rsplit("/", 1)[1])
                known.add(link_msg)
            except (ValueError, IndexError):
                pass
    return known


def scan_orphans(config, bot_pool=None, max_scan=500, batch_size=500,
                 delay=0.5, verbose=False, progress_callback=None,
                 channel_id=None):
    """Scan one or more channels for orphaned messages.

    Args:
        channel_id: If None, scan the main channel (config.main_channel).
                    If a channel ID, scan only that channel.
                    (Multi-channel scanning is handled by scan_all_channels.)

    Returns a dict with stats.
    """
    if bot_pool is None:
        bot_pool = BotPool(config.bots, api_id=config.api_id, api_hash=config.api_hash)
    if len(bot_pool) == 0:
        if verbose:
            print("❌ No active bots.")
        return {"error": "no_bots"}

    bot = bot_pool.get_next()
    scan_channel = channel_id if channel_id is not None else config.main_channel

    db = config.get_db()
    if db is None:
        if verbose:
            print("❌ Database is not enabled. Run: python tg.py db enable")
        return {"error": "no_db"}

    known_msg_ids = _build_known_set(db)
    existing_orphans = db.list_orphans(include_deleted=False)
    for o in existing_orphans:
        known_msg_ids.add(o["msg_id"])

    if verbose:
        print(f"📊 Known message_ids in DB: {len(known_msg_ids)}")

    if verbose:
        print(f"📍 Sending marker to {scan_channel}...")
    marker_res = bot.request("sendMessage", data={
        "chat_id": scan_channel, "text": "_orphan_scan_",
        "disable_notification": True,
    })
    if not marker_res or not marker_res.get("ok"):
        if verbose:
            err = marker_res.get("description") if marker_res else "No response"
            print(f"❌ Cannot send marker: {err}")
        return {"error": "marker_failed"}
    marker_id = marker_res["result"]["message_id"]
    bot.request("deleteMessage", data={
        "chat_id": scan_channel, "message_id": marker_id,
    })
    if verbose:
        print(f"   Marker at: {marker_id} (deleted)")

    effective_max = min(max_scan, marker_id)
    if effective_max <= 0:
        if verbose:
            print("✅ Channel is empty, nothing to scan.")
        return {
            "scanned_messages": 0, "skipped_known": 0,
            "found_new_orphans": 0, "already_known_orphans": 0,
            "batches": 0, "marker_id": marker_id,
        }

    found_new = 0
    found_known_orphans = 0
    scanned = 0
    skipped_known = 0
    batches = 0
    start_id = marker_id
    end_id = max(1, marker_id - effective_max + 1)

    if verbose:
        print(f"\n🔍 Scanning messages {start_id} → {end_id} "
              f"({effective_max} messages, batches of {batch_size})...\n")

    current_start = start_id
    while current_start >= end_id:
        batch_end = max(end_id, current_start - batch_size + 1)
        batch_range = range(current_start, batch_end - 1, -1)

        if verbose:
            print(f"   Batch {batches + 1}: msgs {current_start} → {batch_end}")

        for check_id in batch_range:
            if check_id == marker_id:
                continue
            if check_id in known_msg_ids:
                skipped_known += 1
                continue

            fwd_res = bot.request("forwardMessage", data={
                "chat_id": config.temp_channel,
                "from_chat_id": scan_channel,
                "message_id": check_id,
                "disable_notification": True,
            })
            scanned += 1
            if not fwd_res or not fwd_res.get("ok"):
                continue

            msg_data = fwd_res["result"]
            fwd_msg_id = msg_data["message_id"]
            bot.request("deleteMessage", data={
                "chat_id": config.temp_channel, "message_id": fwd_msg_id,
            })

            info = _classify_message(msg_data)
            if info is None:
                continue  # service message or empty

            share_link = build_share_link(scan_channel, check_id)

            existing = db.get_orphan_by_msg(scan_channel, check_id)
            if existing:
                db.upsert_orphan(
                    msg_id=check_id, channel_id=scan_channel,
                    name=info["name"], total_parts=info["total_parts"],
                    sha256_prefix=info["sha256_prefix"],
                    manifest_text=info["manifest_text"],
                    share_link=share_link,
                    file_size=info["file_size"],
                    is_manifest=info["is_manifest"],
                    message_type=info["message_type"],
                )
                found_known_orphans += 1
                known_msg_ids.add(check_id)
                if verbose:
                    size_str = f" {format_size(info['file_size'])}" if info["file_size"] else ""
                    print(f"      ↻ Refreshed: msg {check_id} — {info['message_type']} "
                          f"{info['name']}{size_str}")
            else:
                db.upsert_orphan(
                    msg_id=check_id, channel_id=scan_channel,
                    name=info["name"], total_parts=info["total_parts"],
                    sha256_prefix=info["sha256_prefix"],
                    manifest_text=info["manifest_text"],
                    share_link=share_link,
                    file_size=info["file_size"],
                    is_manifest=info["is_manifest"],
                    message_type=info["message_type"],
                )
                found_new += 1
                known_msg_ids.add(check_id)
                if verbose:
                    size_str = f" {format_size(info['file_size'])}" if info["file_size"] else ""
                    print(f"      ✨ New orphan: msg {check_id} — {info['message_type']} "
                          f"{info['name']}{size_str}")

        batches += 1
        if progress_callback:
            progress_callback(scanned, effective_max, found_new, found_known_orphans)

        if current_start > end_id and delay > 0:
            if verbose:
                print(f"      ⏸️  Pausing {delay}s between batches...")
            time.sleep(delay)

        current_start = batch_end - 1

    stats = {
        "scanned_messages": scanned,
        "skipped_known": skipped_known,
        "found_new_orphans": found_new,
        "already_known_orphans": found_known_orphans,
        "batches": batches,
        "marker_id": marker_id,
        "channel_id": scan_channel,
    }

    if verbose:
        print("\n" + "=" * 50)
        print("📊 Scan complete!")
        print(f"   Channel:                   {scan_channel}")
        print(f"   Messages scanned via API:  {scanned}")
        print(f"   Messages skipped (known):  {skipped_known}")
        print(f"   New orphans found:         {found_new}")
        print(f"   Existing orphans refreshed: {found_known_orphans}")
        print(f"   Batches:                   {batches}")
        print(f"   Total orphans in DB:       {db.orphan_count()}")
        print("=" * 50)

    return stats


def delete_orphan_from_telegram(config, orphan_id, bot_pool=None,
                                 verbose=False, force=False):
    """Delete a single orphan message from Telegram and mark it in the DB."""
    db = config.get_db()
    if db is None:
        if verbose:
            print("❌ Database is not enabled.")
        return False

    orphan = db.get_orphan(orphan_id)
    if not orphan:
        if verbose:
            print(f"❌ No orphan with id {orphan_id}")
        return False

    if orphan.get("deleted_from_telegram"):
        if verbose:
            print(f"⚠️ Orphan #{orphan_id} already marked as deleted from Telegram.")
        return True

    if bot_pool is None:
        bot_pool = BotPool(config.bots, api_id=config.api_id, api_hash=config.api_hash)
    if len(bot_pool) == 0:
        if verbose:
            print("❌ No active bots.")
        return False

    msg_id = orphan["msg_id"]
    channel_id = orphan["channel_id"]
    name = orphan.get("name") or "?"
    msg_type = orphan.get("message_type") or "unknown"
    file_size = orphan.get("file_size")

    if verbose:
        size_str = f" ({format_size(file_size)})" if file_size else ""
        print(f"📄 Orphan: #{orphan_id} — [{msg_type}] {name}{size_str}")
        print(f"   msg_id: {msg_id} in {channel_id}")
        print(f"   link:   {orphan.get('share_link', 'N/A')}")

    if not force:
        confirm = input(f"   Delete this message from Telegram? (yes/no): ")
        if confirm.strip().lower() != "yes":
            if verbose:
                print("Cancelled.")
            return False

    bot = bot_pool.get_next()
    res = bot.request("deleteMessage", data={
        "chat_id": channel_id, "message_id": msg_id,
    })
    if res and res.get("ok"):
        db.mark_orphan_deleted(orphan_id)
        if verbose:
            print(f"\n✅ Deleted message {msg_id} from Telegram.")
            print(f"💾 Marked orphan #{orphan_id} as deleted in DB.")
        return True
    else:
        err = res.get("description") if res else "No response"
        if verbose:
            print(f"❌ Failed to delete message {msg_id}: {err}")
            print("   (The message may have been deleted already.)")
        db.mark_orphan_deleted(orphan_id)
        return False
