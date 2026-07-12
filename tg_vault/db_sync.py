"""
Database sync to Telegram channel — backup and restore.

The local SQLite database can be backed up to a Telegram channel (the "sync
channel"). This is useful for keeping metadata across machines.

Two modes:
  - **Single-part** (DB < 19 MB): one message with the DB file as document
  - **Multi-part** (DB ≥ 19 MB): split into chunks + a manifest message

Caption prefixes:
  - ``TG_VAULT_DB_BACKUP``   — single-part backup
  - ``TG_VAULT_DB_PART``     — multi-part chunk
  - ``TG_VAULT_DB_MANIFEST`` — multi-part manifest (pointed to by config.db_sync_msg_id)
"""

import io
import json
import math
import os
import time

import requests

from .constants import TG_FILE_SIZE_LIMIT
from .utils import (
    compute_sha256,
    format_size,
    truncate_caption,
    build_share_link,
)
from .bot_pool import BotPool
from .db import Database


def sync_db_to_channel(config, bot_pool=None, verbose=False):
    """Upload the local DB file to the sync channel as a backup.

    Supports multi-part uploads for DBs larger than 19 MB:
      - DB < 19 MB: single message (caption starts with TG_VAULT_DB_BACKUP)
      - DB >= 19 MB: split into chunks, manifest message at the end
        (caption starts with TG_VAULT_DB_MANIFEST)

    - Finds and deletes ALL previous DB backup messages (single or multi-part).
    - Saves the manifest message ID in config.db_sync_msg_id.
    """
    db_path = config.get_db_path()
    if not os.path.exists(db_path):
        if verbose:
            print(f"❌ Database file does not exist: {db_path}")
        return False

    sync_channel = config.get_db_sync_channel()
    if not sync_channel:
        if verbose:
            print("❌ No sync channel configured (set temp_channel or db_sync_channel).")
        return False

    if bot_pool is None:
        bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        if verbose:
            print("❌ No active bots.")
        return False

    bot = bot_pool.get_next()
    db_size = os.path.getsize(db_path)

    # Clean up previous DB backup messages
    if verbose:
        print("🔍 Scanning for previous DB backup messages to clean up...")

    marker_res = bot.request("sendMessage", data={
        "chat_id": sync_channel, "text": "_db_sync_marker_",
        "disable_notification": True,
    })
    marker_msg_id = None
    if marker_res and marker_res.get("ok"):
        marker_msg_id = marker_res["result"]["message_id"]
        bot.request("deleteMessage", data={
            "chat_id": sync_channel, "message_id": marker_msg_id,
        })

    deleted_count = 0
    if marker_msg_id:
        # First: directly delete the known db_sync_msg_id (if set and different from current)
        if config.db_sync_msg_id and config.db_sync_msg_id != marker_msg_id:
            old_res = bot.request("deleteMessage", data={
                "chat_id": sync_channel, "message_id": config.db_sync_msg_id,
            })
            if old_res and old_res.get("ok"):
                deleted_count += 1
                if verbose:
                    print(f"   🗑️  Deleted old DB backup at msg {config.db_sync_msg_id}")

        # Also scan backward for any other DB backup messages (in case db_sync_msg_id was stale)
        for check_id in range(marker_msg_id, max(0, marker_msg_id - 200), -1):
            if check_id == marker_msg_id:
                continue
            if check_id == config.db_sync_msg_id:
                continue  # already deleted above
            # Use copyMessage to check the caption (forwardMessage doesn't return caption
            # for channel messages — this is a known Telegram quirk)
            fwd_res = bot.request("forwardMessage", data={
                "chat_id": sync_channel, "from_chat_id": sync_channel,
                "message_id": check_id, "disable_notification": True,
            })
            if not fwd_res or not fwd_res.get("ok"):
                continue
            fwd_msg_id = fwd_res["result"]["message_id"]
            caption = fwd_res["result"].get("caption", "")
            # Also check document filename for DB backups
            doc = fwd_res["result"].get("document", {})
            filename = doc.get("file_name", "") if doc else ""
            bot.request("deleteMessage", data={
                "chat_id": sync_channel, "message_id": fwd_msg_id,
            })
            # Check if this is a DB backup message (by caption OR filename)
            is_db_backup = (
                caption.startswith("TG_VAULT_DB_BACKUP")
                or caption.startswith("TG_VAULT_DB_MANIFEST")
                or caption.startswith("TG_VAULT_DB_PART")
                or filename.startswith("tg-vault-db-")
                or filename.endswith(".manifest.json") and "tg-vault-db" in filename
            )
            if is_db_backup:
                bot.request("deleteMessage", data={
                    "chat_id": sync_channel, "message_id": check_id,
                })
                deleted_count += 1
                if verbose and deleted_count <= 10:
                    print(f"   🗑️  Deleted old DB message at msg {check_id}")

    if verbose and deleted_count > 0:
        print(f"   ✅ Cleaned up {deleted_count} old DB message(s)")

    # Compute stats for caption
    stats = None
    try:
        db = Database(db_path)
        stats = db.stats()
    except Exception:
        pass

    # Decide: single-part or multi-part
    # Use 19 MB threshold (same as file chunks) to be safe under 20MB download limit
    DB_CHUNK_SIZE = 19 * 1024 * 1024
    is_multipart = db_size > DB_CHUNK_SIZE

    if verbose:
        if is_multipart:
            total_parts = math.ceil(db_size / DB_CHUNK_SIZE)
            print(f"📤 Uploading database ({format_size(db_size)}) as {total_parts} parts...")
        else:
            print(f"📤 Uploading database ({format_size(db_size)}) as single part...")

    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())

    if not is_multipart:
        # Single-part upload (DB < 19 MB)
        db_filename = f"tg-vault-db-{timestamp}.sqlite"
        with open(db_path, "rb") as f:
            files = {"document": (db_filename, f)}
            caption_parts = ["TG_VAULT_DB_BACKUP"]
            if stats:
                caption_parts.append(f"files:{stats['total_files']}")
                caption_parts.append(f"size:{stats['total_size']}")
                caption_parts.append(f"downloads:{stats['total_downloads']}")
            caption_parts.append(f"synced:{int(time.time())}")
            caption_parts.append("parts:1")
            caption = "|".join(caption_parts)
            res = bot.request("sendDocument", data={
                "chat_id": sync_channel, "caption": truncate_caption(caption),
            }, files=files)

        if not res or not res.get("ok"):
            err = res.get("description") if res else "No response"
            if verbose:
                print(f"❌ Failed to sync DB: {err}")
            return False

        msg_id = res["result"]["message_id"]
        config.db_sync_msg_id = msg_id
        config.db_sync_multipart = False
        config.save()

        if verbose:
            print(f"✅ Database synced (single-part)!")
            print(f"   Message ID: {msg_id}")
            print(f"   File: {db_filename} ({format_size(db_size)})")
            share_link = build_share_link(sync_channel, msg_id)
            if share_link:
                print(f"   Link: {share_link}")
        return True

    # Multi-part upload (DB >= 19 MB)
    total_parts = math.ceil(db_size / DB_CHUNK_SIZE)
    message_ids = []

    with open(db_path, "rb") as f:
        for part_num in range(1, total_parts + 1):
            chunk = f.read(DB_CHUNK_SIZE)
            part_name = f"tg-vault-db-{timestamp}.part{part_num:04d}of{total_parts:04d}"
            files = {"document": (part_name, chunk)}
            caption = truncate_caption(f"TG_VAULT_DB_PART|{part_num}|{total_parts}")
            res = bot.request("sendDocument", data={
                "chat_id": sync_channel, "caption": caption,
            }, files=files)
            if not res or not res.get("ok"):
                err = res.get("description") if res else "No response"
                if verbose:
                    print(f"❌ Failed to upload DB part {part_num}: {err}")
                # Clean up parts already uploaded
                for mid in message_ids:
                    bot.request("deleteMessage", data={
                        "chat_id": sync_channel, "message_id": mid,
                    })
                return False
            message_ids.append(res["result"]["message_id"])
            if verbose:
                print(f"   ✅ Part {part_num}/{total_parts} uploaded (msg {message_ids[-1]})")
            time.sleep(0.3)

    # Send manifest as final message
    db_hash = compute_sha256(db_path)
    manifest = {
        "type": "tg-vault-db-manifest",
        "version": 1,
        "size": db_size,
        "sha256": db_hash,
        "total_parts": total_parts,
        "chunk_size": DB_CHUNK_SIZE,
        "message_ids": message_ids,
        "synced_at": int(time.time()),
    }
    if stats:
        manifest["stats"] = stats
    manifest_blob = io.BytesIO(json.dumps(manifest, indent=2).encode("utf-8"))
    manifest_name = f"tg-vault-db-{timestamp}.manifest.json"
    manifest_caption_parts = ["TG_VAULT_DB_MANIFEST", f"parts:{total_parts}", f"size:{db_size}"]
    if stats:
        manifest_caption_parts.append(f"files:{stats['total_files']}")
    manifest_caption = truncate_caption("|".join(manifest_caption_parts))

    res = bot.request("sendDocument", data={
        "chat_id": sync_channel,
        "caption": manifest_caption,
        "reply_to_message_id": message_ids[-1] if message_ids else None,
    }, files={"document": (manifest_name, manifest_blob)})

    if not res or not res.get("ok"):
        err = res.get("description") if res else "No response"
        if verbose:
            print(f"❌ Failed to send DB manifest: {err}")
        return False

    manifest_msg_id = res["result"]["message_id"]
    config.db_sync_msg_id = manifest_msg_id
    config.db_sync_multipart = True
    config.save()

    if verbose:
        print(f"✅ Database synced (multi-part, {total_parts} parts)!")
        print(f"   Manifest message ID: {manifest_msg_id}")
        print(f"   Total size: {format_size(db_size)}")
        print(f"   SHA256: {db_hash}")
        share_link = build_share_link(sync_channel, manifest_msg_id)
        if share_link:
            print(f"   Link: {share_link}")
    return True


def find_latest_db_backup(config, bot_pool=None, verbose=False):
    """Scan the sync channel for the latest DB backup message.

    Looks for messages with caption starting with:
      - TG_VAULT_DB_BACKUP (single-part)
      - TG_VAULT_DB_MANIFEST (multi-part manifest)

    Returns the message_id of the latest backup, or None if not found.
    Also updates config.db_sync_msg_id if found.
    """
    sync_channel = config.get_db_sync_channel()
    if not sync_channel:
        return None

    if bot_pool is None:
        bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        return None

    bot = bot_pool.get_next()

    # Send a marker to get the current latest message_id
    marker_res = bot.request("sendMessage", data={
        "chat_id": sync_channel, "text": "_db_find_marker_",
        "disable_notification": True,
    })
    if not marker_res or not marker_res.get("ok"):
        return None

    marker_id = marker_res["result"]["message_id"]
    bot.request("deleteMessage", data={
        "chat_id": sync_channel, "message_id": marker_id,
    })

    if verbose:
        print(f"🔍 Scanning channel for latest DB backup (checking last 200 messages)...")

    # Scan backwards from marker_id
    scan_range = min(200, marker_id)
    for check_id in range(marker_id, max(0, marker_id - scan_range), -1):
        if check_id == marker_id:
            continue

        fwd_res = bot.request("forwardMessage", data={
            "chat_id": sync_channel,
            "from_chat_id": sync_channel,
            "message_id": check_id,
            "disable_notification": True,
        })
        if not fwd_res or not fwd_res.get("ok"):
            continue

        fwd_msg_id = fwd_res["result"]["message_id"]
        caption = fwd_res["result"].get("caption", "")

        # Delete forwarded copy immediately
        bot.request("deleteMessage", data={
            "chat_id": sync_channel, "message_id": fwd_msg_id,
        })

        if caption.startswith("TG_VAULT_DB_BACKUP") or caption.startswith("TG_VAULT_DB_MANIFEST"):
            if verbose:
                print(f"   ✅ Found DB backup at msg {check_id}: {caption[:80]}")
            # Update config with the found ID
            config.db_sync_msg_id = check_id
            config.db_sync_multipart = caption.startswith("TG_VAULT_DB_MANIFEST")
            config.save()
            return check_id

    if verbose:
        print(f"   ❌ No DB backup found in last {scan_range} messages")
    return None


def restore_db_from_channel(config, bot_pool=None, verbose=False):
    """Download the DB file from the sync channel and replace the local one.

    This function is ROBUST — it doesn't just rely on config.db_sync_msg_id.
    If the stored ID is stale (message was deleted), it scans the channel
    to find the latest DB backup automatically.

    Supports both single-part and multi-part DB backups:
      - Single-part: message caption starts with TG_VAULT_DB_BACKUP
      - Multi-part: message caption starts with TG_VAULT_DB_MANIFEST
    """
    sync_channel = config.get_db_sync_channel()
    if not sync_channel:
        if verbose:
            print("❌ No sync channel configured.")
        return False

    if bot_pool is None:
        bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        if verbose:
            print("❌ No active bots.")
        return False

    bot = bot_pool.get_next()
    msg_id = config.db_sync_msg_id

    # Step 1: Try the stored msg_id first
    if msg_id:
        if verbose:
            print(f"📥 Trying stored DB sync message {msg_id}...")

        fwd_res = bot.request("forwardMessage", data={
            "chat_id": sync_channel,
            "from_chat_id": sync_channel,
            "message_id": msg_id,
            "disable_notification": True,
        })

        if fwd_res and fwd_res.get("ok"):
            # Message exists! Proceed with restore.
            return _do_restore_from_msg(config, bot, sync_channel, fwd_res, verbose)

        # Message not found — fall through to scanning
        if verbose:
            err = fwd_res.get("description") if fwd_res else "No response"
            print(f"⚠️ Stored message {msg_id} not found: {err}")
            print(f"🔍 Scanning channel for latest DB backup...")

    # Step 2: Scan the channel for the latest DB backup
    found_msg_id = find_latest_db_backup(config, bot_pool, verbose)
    if not found_msg_id:
        if verbose:
            print("❌ No DB backup found in channel.")
            print("   Run `python tg.py db sync` to create a new backup.")
        return False

    # Step 3: Fetch the found message and restore
    msg_id = found_msg_id
    if verbose:
        print(f"📥 Fetching DB backup at msg {msg_id}...")

    fwd_res = bot.request("forwardMessage", data={
        "chat_id": sync_channel,
        "from_chat_id": sync_channel,
        "message_id": msg_id,
        "disable_notification": True,
    })
    if not fwd_res or not fwd_res.get("ok"):
        err = fwd_res.get("description") if fwd_res else "No response"
        if verbose:
            print(f"❌ Failed to fetch DB message: {err}")
        return False

    return _do_restore_from_msg(config, bot, sync_channel, fwd_res, verbose)


def _do_restore_from_msg(config, bot, sync_channel, fwd_res, verbose=False):
    """Actually restore DB from a forwarded message. Returns True/False."""
    fwd_msg_id = fwd_res["result"]["message_id"]
    caption = fwd_res["result"].get("caption", "")
    doc = fwd_res["result"].get("document")

    is_multipart = caption.startswith("TG_VAULT_DB_MANIFEST")

    if is_multipart:
        # ─── Multi-part restore ───
        if not doc:
            bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": fwd_msg_id})
            if verbose:
                print("❌ DB manifest message has no document attachment.")
            return False

        file_id = doc["file_id"]
        file_size = doc.get("file_size", 0)
        if verbose:
            print(f"   📋 Multi-part DB manifest detected")
            print(f"   File ID: {file_id[:30]}...")
            print(f"   Manifest size: {format_size(file_size)}")

        if file_size > TG_FILE_SIZE_LIMIT:
            bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": fwd_msg_id})
            if verbose:
                print("❌ DB manifest too large (shouldn't happen — it's a small JSON)")
            return False

        file_res = bot.request("getFile", data={"file_id": file_id})
        bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": fwd_msg_id})
        if not file_res or not file_res.get("ok"):
            if verbose:
                print("❌ getFile failed for manifest.")
            return False

        manifest_url = f"https://api.telegram.org/file/bot{bot.token}/{file_res['result']['file_path']}"
        try:
            r = requests.get(manifest_url, timeout=60)
            if r.status_code != 200:
                if verbose:
                    print(f"❌ HTTP {r.status_code} downloading manifest")
                return False
            manifest = json.loads(r.content.decode("utf-8"))
        except Exception as e:
            if verbose:
                print(f"❌ Error downloading/parsing manifest: {e}")
            return False

        total_parts = manifest["total_parts"]
        expected_size = manifest["size"]
        expected_hash = manifest["sha256"]
        part_msg_ids = manifest["message_ids"]

        if verbose:
            print(f"   Total parts: {total_parts}")
            print(f"   Expected size: {format_size(expected_size)}")
            print(f"   Expected SHA256: {expected_hash[:16]}...")
            print()

        db_path = config.get_db_path()
        temp_file = db_path + ".restoring"

        with open(temp_file, "wb") as out_file:
            for i, part_msg_id in enumerate(part_msg_ids, 1):
                if verbose:
                    print(f"   📦 Downloading part {i}/{total_parts} (msg {part_msg_id})...")

                pfwd = bot.request("forwardMessage", data={
                    "chat_id": sync_channel,
                    "from_chat_id": sync_channel,
                    "message_id": part_msg_id,
                    "disable_notification": True,
                })
                if not pfwd or not pfwd.get("ok"):
                    err = pfwd.get("description") if pfwd else "No response"
                    if verbose:
                        print(f"      ❌ Failed to fetch part {i}: {err}")
                    out_file.close()
                    os.remove(temp_file)
                    return False

                pfwd_msg_id = pfwd["result"]["message_id"]
                pdoc = pfwd["result"].get("document")
                if not pdoc:
                    bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": pfwd_msg_id})
                    if verbose:
                        print(f"      ❌ Part {i} has no document")
                    out_file.close()
                    os.remove(temp_file)
                    return False

                p_file_id = pdoc["file_id"]
                p_file_size = pdoc.get("file_size", 0)

                if p_file_size > TG_FILE_SIZE_LIMIT:
                    bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": pfwd_msg_id})
                    if verbose:
                        print(f"      ❌ Part {i} too large ({format_size(p_file_size)} > 20MB)")
                    out_file.close()
                    os.remove(temp_file)
                    return False

                p_file_res = bot.request("getFile", data={"file_id": p_file_id})
                bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": pfwd_msg_id})
                if not p_file_res or not p_file_res.get("ok"):
                    if verbose:
                        print(f"      ❌ getFile failed for part {i}")
                    out_file.close()
                    os.remove(temp_file)
                    return False

                p_url = f"https://api.telegram.org/file/bot{bot.token}/{p_file_res['result']['file_path']}"
                try:
                    pr = requests.get(p_url, timeout=300)
                    if pr.status_code != 200:
                        if verbose:
                            print(f"      ❌ HTTP {pr.status_code} for part {i}")
                        out_file.close()
                        os.remove(temp_file)
                        return False
                    out_file.write(pr.content)
                except Exception as e:
                    if verbose:
                        print(f"      ❌ Download error for part {i}: {e}")
                    out_file.close()
                    os.remove(temp_file)
                    return False

        actual_size = os.path.getsize(temp_file)
        if actual_size != expected_size:
            if verbose:
                print(f"❌ Size mismatch: {actual_size} vs expected {expected_size}")
            os.remove(temp_file)
            return False

        if verbose:
            print("\n   🔍 Verifying SHA256...")
        actual_hash = compute_sha256(temp_file)
        if actual_hash != expected_hash:
            if verbose:
                print(f"❌ SHA256 mismatch!")
                print(f"   Expected: {expected_hash}")
                print(f"   Got:      {actual_hash}")
            os.remove(temp_file)
            return False
        if verbose:
            print(f"   ✅ SHA256 verified!")

        if os.path.exists(db_path):
            backup_path = db_path + ".backup"
            os.replace(db_path, backup_path)
            if verbose:
                print(f"   Backed up local DB to: {backup_path}")
        os.replace(temp_file, db_path)

        if verbose:
            print(f"\n✅ Database restored from Telegram (multi-part, {total_parts} parts)!")
            print(f"   Path: {db_path}")
            print(f"   Size: {format_size(actual_size)}")
            try:
                db = Database(db_path)
                stats = db.stats()
                print(f"\n📊 Restored DB stats:")
                print(f"   Files: {stats['total_files']}")
                print(f"   Total size: {format_size(stats['total_size'])}")
                print(f"   Total downloads: {stats['total_downloads']}")
            except Exception as e:
                print(f"⚠️ Could not read restored DB: {e}")
        return True

    # ─── Single-part restore (DB < 19 MB) ───
    if not doc:
        bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": fwd_msg_id})
        if verbose:
            print("❌ DB message has no document attachment.")
        return False

    file_id = doc["file_id"]
    file_size = doc.get("file_size", 0)

    if file_size > TG_FILE_SIZE_LIMIT:
        bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": fwd_msg_id})
        if verbose:
            print(f"❌ DB file too large ({format_size(file_size)} > 20MB).")
            print(f"   Run `python tg.py db sync` to create a new multi-part backup.")
        return False

    if verbose:
        print(f"   📄 Single-part DB backup detected")
        print(f"   File ID: {file_id[:30]}...")
        print(f"   Size: {format_size(file_size)}")

    file_res = bot.request("getFile", data={"file_id": file_id})
    bot.request("deleteMessage", data={"chat_id": sync_channel, "message_id": fwd_msg_id})
    if not file_res or not file_res.get("ok"):
        if verbose:
            print("❌ getFile failed.")
        return False

    file_path = file_res["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"

    try:
        r = requests.get(url, timeout=300)
        if r.status_code != 200:
            if verbose:
                print(f"❌ HTTP {r.status_code}")
            return False
    except Exception as e:
        if verbose:
            print(f"❌ Download error: {e}")
        return False

    db_path = config.get_db_path()
    if os.path.exists(db_path):
        backup_path = db_path + ".backup"
        os.replace(db_path, backup_path)
        if verbose:
            print(f"   Backed up local DB to: {backup_path}")

    with open(db_path, "wb") as f:
        f.write(r.content)

    if verbose:
        print(f"✅ Database restored from Telegram (single-part)!")
        print(f"   Path: {db_path}")
        print(f"   Size: {format_size(len(r.content))}")
        try:
            db = Database(db_path)
            stats = db.stats()
            print(f"\n📊 Restored DB stats:")
            print(f"   Files: {stats['total_files']}")
            print(f"   Total size: {format_size(stats['total_size'])}")
            print(f"   Total downloads: {stats['total_downloads']}")
        except Exception as e:
            print(f"⚠️ Could not read restored DB: {e}")
    return True


def auto_sync_db(config, bot_pool=None):
    """Auto-sync the DB to Telegram if db_auto_sync is enabled.
    Called after every upload/download that modifies the DB.
    Silent unless there's an error.
    """
    if not config.db_auto_sync or not config.db_enabled:
        return
    try:
        sync_db_to_channel(config, bot_pool=bot_pool, verbose=False)
    except Exception:
        pass  # Silent failure for auto-sync
