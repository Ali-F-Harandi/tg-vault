"""
CLI command functions for tg-vault.

Each ``cmd_*`` function takes (args, config) and executes the corresponding
CLI command. Internal helpers (``_db_download``, ``_build_filters_from_args``)
are also defined here.
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

import requests

from .constants import (
    MANIFEST_PREFIX,
    TG_FILE_SIZE_LIMIT,
)
from .utils import (
    format_size,
    sanitize_hashtags,
    truncate_caption,
    truncate_text,
    parse_telegram_link,
    build_share_link,
)
from .config import Config
from .bot_pool import BotPool
from .uploader import Uploader
from .downloader import Downloader
from .crypto import is_encryption_available
from .db_sync import (
    sync_db_to_channel,
    restore_db_from_channel,
    find_latest_db_backup,
    auto_sync_db,
)


# ==========================================
# Setup / config commands
# ==========================================
def cmd_init(args, config_path):
    """Initialize a sample config file."""
    if os.path.exists(config_path):
        print(f"⚠️ Config file already exists: {config_path}")
        if input("Overwrite? (y/N): ").strip().lower() != "y":
            return
    config = Config(path=config_path)
    config.save()
    print(f"✅ Config file created: {config_path}")
    print("\nNext steps:")
    print("  Option A — Interactive wizard (recommended):")
    print("    python tg.py setup")
    print("  Option B — Manual commands:")
    print("    python tg.py bots add <TOKEN>")
    print("    python tg.py channels set main <CHANNEL_ID>")
    print("    python tg.py channels set temp <CHANNEL_ID>  (optional)")
    print("    python tg.py test")
    print("  Option C — Edit the config file directly:")
    print(f"    $EDITOR {config_path}")


def cmd_setup(args, config):
    """Interactive setup wizard — bot token + channels in one go."""
    print("\n" + "=" * 55)
    print("    🪄 tg-vault setup wizard")
    print("=" * 55)
    print()

    # Show current state if any
    if config.bots or config.main_channel:
        print("📋 Current configuration:")
        if config.bots:
            print(f"   Bots: {len(config.bots)} ({', '.join('@' + b.get('username', '?') for b in config.bots)})")
        if config.main_channel:
            print(f"   Main channel: {config.main_channel}")
        if config.temp_channel and config.temp_channel != config.main_channel:
            print(f"   Temp channel: {config.temp_channel}")
        print()
        if input("Reconfigure? (y/N): ").strip().lower() != "y":
            return
        print()

    # Step 1: Bot token
    print("─" * 55)
    print("Step 1/4 — Bot token")
    print("─" * 55)
    print("Get a token from @BotFather (https://t.me/BotFather):")
    print("  1. Send /newbot to @BotFather")
    print("  2. Choose a name and username")
    print("  3. Copy the token (looks like 123456789:ABC-DEF...)")
    print()
    token = input("Bot token: ").strip()
    if not token:
        print("Cancelled.")
        return

    # Verify token
    print("\n🔍 Verifying token...")
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
        if not r.get("ok"):
            print(f"❌ Invalid token: {r.get('description')}")
            return
        username = r["result"].get("username", "")
        bot_id = r["result"]["id"]
        print(f"✅ Bot verified: @{username} (id: {bot_id})")
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # Step 2: Main channel
    print("\n" + "─" * 55)
    print("Step 2/4 — Main channel")
    print("─" * 55)
    print("Create a Telegram channel (private recommended), then:")
    print("  1. Add your bot as administrator")
    print("  2. Give it 'Post messages' and 'Delete messages' rights")
    print("  3. Get the channel ID (see docs/CONFIGURATION.md)")
    print()
    print("Common formats:")
    print("  • Private channel:  -1001234567890  (starts with -100)")
    print("  • Public channel:   @mychannel_username")
    print()
    main_channel = input("Main channel ID: ").strip()
    if not main_channel:
        print("Cancelled.")
        return

    # Verify channel access
    print("\n🔍 Verifying channel access...")
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getChat",
            params={"chat_id": main_channel},
            timeout=30
        ).json()
        if not r.get("ok"):
            print(f"❌ Cannot access channel: {r.get('description')}")
            print("   Make sure the bot is added as admin to the channel.")
            return
        chat = r["result"]
        print(f"✅ Channel: {chat.get('title', '?')} (type: {chat.get('type')})")

        # Check admin rights
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getChatMember",
            params={"chat_id": main_channel, "user_id": bot_id},
            timeout=30
        ).json()
        if r.get("ok"):
            status = r["result"]["status"]
            can_post = r["result"].get("can_post_messages", status == "administrator")
            can_delete = r["result"].get("can_delete_messages", status == "administrator")
            if status != "administrator" or not can_post or not can_delete:
                print(f"⚠️ Bot status: {status}, post={can_post}, delete={can_delete}")
                print("   The bot needs admin rights with Post + Delete messages!")
                if input("Continue anyway? (y/N): ").strip().lower() != "y":
                    return
            else:
                print(f"✅ Bot is admin with proper rights (post={can_post}, delete={can_delete})")
    except Exception as e:
        print(f"⚠️ Could not verify channel: {e}")
        if input("Continue anyway? (y/N): ").strip().lower() != "y":
            return

    # Step 3: Temp channel
    print("\n" + "─" * 55)
    print("Step 3/4 — Temp channel (optional)")
    print("─" * 55)
    print("A separate temp channel keeps your main channel clean.")
    print("The bot uses it for temporary forwarded messages during downloads.")
    print("Press Enter to use the main channel as temp.")
    print()
    temp_channel = input("Temp channel ID (or Enter for same as main): ").strip()
    if not temp_channel:
        temp_channel = main_channel
        print(f"   Using main channel as temp: {temp_channel}")

    # Step 4: Database
    print("\n" + "─" * 55)
    print("Step 4/4 — Database (optional, recommended)")
    print("─" * 55)
    print("A SQLite database stores metadata for every uploaded file:")
    print("  name, size, SHA256, parts, message IDs, description, hashtags,")
    print("  share link, timestamps, download history.")
    print()
    db_choice = input("Enable database? [Y/n]: ").strip().lower()
    db_enabled = db_choice != "n"
    db_path = None
    if db_enabled:
        default_db = os.path.join(os.path.dirname(os.path.abspath(config.path)), "tg-vault.db")
        db_input = input(f"Database path [default: {default_db}]: ").strip()
        if db_input:
            db_path = os.path.expanduser(db_input)
        else:
            db_path = default_db
        print(f"   Database will be created at: {db_path}")

    # Save
    config.bots = [{"token": token, "username": username}]
    config.main_channel = main_channel
    config.temp_channel = temp_channel
    config.db_enabled = db_enabled
    config.db_path = db_path
    config.save()

    print("\n" + "=" * 55)
    print("✅ Configuration saved!")
    print(f"   Config file: {config.path}")
    print(f"   Bot: @{username}")
    print(f"   Main channel: {main_channel}")
    print(f"   Temp channel: {temp_channel}")
    if db_enabled:
        print(f"   Database: {db_path}")
    print("=" * 55)

    # Test
    print("\n🧪 Running final connectivity test...")
    cmd_test(None, config)

    print("\n💡 You're ready! Try:")
    print(f"   python tg.py upload some-file.zip --desc 'My first upload' --tag test")


def cmd_bots(args, config):
    if args.bots_action == "add":
        token = args.token
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
            if not r.get("ok"):
                print(f"❌ Invalid token: {r.get('description')}")
                return
            username = r["result"].get("username", "")
            bot_id = r["result"]["id"]
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return

        # Check duplicate
        for b in config.bots:
            if b["token"] == token:
                print("⚠️ This bot is already added.")
                return

        config.bots.append({"token": token, "username": username})
        config.save()
        print(f"✅ Bot added: @{username} (id: {bot_id})")

    elif args.bots_action == "list":
        if not config.bots:
            print("❌ No bots added yet.")
            return
        print(f"📋 Bots ({len(config.bots)}):")
        for i, b in enumerate(config.bots, 1):
            print(f"   {i}. @{b.get('username', '?')} | token: {b['token'][:15]}...")

    elif args.bots_action == "remove":
        if not config.bots:
            print("❌ No bots to remove.")
            return
        idx = args.index - 1
        if 0 <= idx < len(config.bots):
            removed = config.bots.pop(idx)
            config.save()
            print(f"✅ Removed: @{removed.get('username', '?')}")
        else:
            print(f"❌ Invalid index. Use 1 to {len(config.bots)}")


def _parse_channel_id(value):
    """Try to convert a channel ID string to int, or keep as @username."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def cmd_channels(args, config):
    if args.channels_action == "set":
        if args.name == "main":
            config.main_channel = _parse_channel_id(args.value)
            if not config.temp_channel:
                config.temp_channel = config.main_channel
            config.save()
            print(f"✅ Main channel set: {config.main_channel}")
            print(f"   Temp channel: {config.temp_channel}")
        elif args.name == "temp":
            config.temp_channel = _parse_channel_id(args.value)
            config.save()
            print(f"✅ Temp channel set: {config.temp_channel}")
        else:
            print("❌ name must be 'main' or 'temp'")
            print("   Usage: channels set main <ID>")
            print("          channels set temp <ID>")

    elif args.channels_action == "show":
        print("📋 Channels:")
        print(f"   main: {config.main_channel or '(not set)'}")
        print(f"   temp: {config.temp_channel or '(not set)'}")
        all_chs = config.get_all_storage_channels()
        if len(all_chs) > 1:
            print(f"\n   📦 Storage channels ({len(all_chs)}):")
            for i, ch in enumerate(all_chs, 1):
                tag = " (main)" if ch == config.main_channel else ""
                print(f"      {i}. {ch}{tag}")
        else:
            print(f"\n   📦 Storage channels: only main (use 'channels add <ID>' to add more)")

    elif args.channels_action == "add":
        # Add a storage channel
        # The value might be in args.value or args.name (since argparse may put
        # a negative number in the first positional arg)
        ch_str = args.value or args.name
        if not ch_str:
            print("❌ Channel ID required: channels add <ID>")
            return
        ch_id = _parse_channel_id(ch_str)
        if config.add_storage_channel(ch_id):
            config.save()
            print(f"✅ Added storage channel: {ch_id}")
            print(f"   Total storage channels: {len(config.get_all_storage_channels())}")
        else:
            print(f"⚠️ Channel {ch_id} is already in the storage list.")

    elif args.channels_action == "remove":
        # Remove a storage channel (can't remove main)
        ch_str = args.value or args.name
        if not ch_str:
            print("❌ Channel ID required: channels remove <ID>")
            return
        ch_id = _parse_channel_id(ch_str)
        if ch_id == config.main_channel:
            print("❌ Cannot remove the main channel. Use 'channels set main' to change it.")
            return
        if config.remove_storage_channel(ch_id):
            config.save()
            print(f"✅ Removed storage channel: {ch_id}")
            print(f"   Remaining storage channels: {len(config.get_all_storage_channels())}")
        else:
            print(f"❌ Channel {ch_id} is not in the storage list.")


# ==========================================
# Upload / Download commands
# ==========================================
def cmd_upload(args, config):
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    hashtags = []
    if args.tag:
        raw = [t.strip() for t in args.tag.split(",") if t.strip()]
        hashtags = sanitize_hashtags(raw)
        if len(hashtags) != len(raw):
            print(f"⚠️ Some hashtags were sanitized (invalid chars removed/replaced)")
            print(f"   Original: {raw}")
            print(f"   Sanitized: {hashtags}")

    db = config.get_db()
    if db:
        print(f"💾 Database: {config.get_db_path()}")

    # Encryption
    encrypt = getattr(args, "encrypt", False)
    password = getattr(args, "password", None) or os.environ.get("TG_VAULT_PASSWORD")
    if encrypt and not password:
        import getpass
        print("🔐 Encryption enabled. Enter a password.")
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("❌ Passwords don't match.")
            return

    compress = not getattr(args, "no_compress", False)

    # Collect file list (supports glob expansion and multiple files)
    files = list(args.files)
    if not files:
        print("❌ No files specified.")
        return

    uploader = Uploader(config, bot_pool, db=db)

    # Determine which channel(s) to upload to
    target_channel = getattr(args, "channel", None)
    all_channels = getattr(args, "all_channels", False)

    if target_channel:
        # Parse the channel ID
        target_channel = _parse_channel_id(target_channel)
        if not config.is_storage_channel(target_channel):
            print(f"⚠️ Warning: channel {target_channel} is not in the storage list.")
            print(f"   Uploading anyway. Use 'channels add {target_channel}' to add it.")
        upload_channels = [target_channel]
    elif all_channels:
        upload_channels = config.get_all_storage_channels()
        if len(upload_channels) <= 1:
            print("⚠️ Only one storage channel configured. Use 'channels add' to add more.")
        print(f"📤 Uploading to ALL {len(upload_channels)} storage channels")
    else:
        upload_channels = [config.main_channel]

    # Bulk upload
    results = []
    total = len(files) * len(upload_channels)
    idx = 0
    for file_path in files:
        for ch_id in upload_channels:
            idx += 1
            # Temporarily set main_channel so the uploader uses this channel
            original_channel = config.main_channel
            config.main_channel = ch_id
            print(f"\n{'=' * 60}")
            ch_label = f" to channel {ch_id}" if len(upload_channels) > 1 else ""
            print(f"📤 Uploading file {idx}/{total}{ch_label}: {file_path}")
            print(f"{'=' * 60}")
            result = uploader.upload(
                file_path,
                description=args.desc or "",
                hashtags=hashtags,
                resume=args.resume,
                encrypt=encrypt,
                password=password,
                compress=compress,
                manifest_type=getattr(args, "manifest_type", None) or config.default_manifest_type,
            )
            config.main_channel = original_channel  # restore
            results.append((file_path, result))

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 Bulk upload summary ({total} files):")
    print(f"{'=' * 60}")
    success_count = 0
    for file_path, result in results:
        if result and result.get("share_link"):
            success_count += 1
            print(f"  ✅ {os.path.basename(file_path)}: {result['share_link']}")
        else:
            print(f"  ❌ {file_path}: failed")
    print(f"\n{success_count}/{total} files uploaded successfully.")
    if success_count > 0:
        print(f"\n💡 To download any file:")
        print(f"   python tg.py download \"<link>\"")


def cmd_download(args, config):
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    db = config.get_db()
    if db:
        print(f"💾 Database: {config.get_db_path()}")

    # Collect links (supports multiple links and --links-file)
    links = list(args.links)
    if args.links_file:
        try:
            with open(args.links_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        links.append(line)
        except OSError as e:
            print(f"❌ Cannot read links file: {e}")
            sys.exit(1)

    if not links:
        print("❌ No links specified.")
        sys.exit(1)

    downloader = Downloader(config, bot_pool, db=db)

    # Bulk download
    total = len(links)
    success_count = 0
    for i, link in enumerate(links, 1):
        print(f"\n{'=' * 60}")
        print(f"📥 Downloading file {i}/{total}: {link}")
        print(f"{'=' * 60}")
        try:
            success = downloader.download(
                link,
                resume=args.resume,
                output=args.output if total == 1 else None,  # only allow --output for single file
                output_dir=args.output_dir,
                password=getattr(args, "password", None),
            )
            if success:
                success_count += 1
        except ValueError as e:
            print(f"❌ {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 Bulk download summary ({total} files):")
    print(f"{'=' * 60}")
    print(f"{success_count}/{total} files downloaded successfully.")
    sys.exit(0 if success_count == total else 1)


def cmd_info(args, config):
    errors = config.validate()
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return

    bot_pool = BotPool(config.bots)
    downloader = Downloader(config, bot_pool)
    try:
        downloader.info(args.link)
    except ValueError as e:
        print(f"❌ {e}")


def cmd_test(args, config):
    """Test connectivity for all bots and channels."""
    print("🧪 Testing connectivity...\n")

    if not config.bots:
        print("❌ No bots configured.")
        return

    bot_pool = BotPool(config.bots)
    print(f"📊 Bots: {len(bot_pool)}")
    for b in bot_pool.list_bots():
        status = "✅" if b.id else "❌"
        print(f"   {status} @{b.username} (id: {b.id})")

    if not config.main_channel:
        print("\n❌ Main channel not set.")
        return

    print(f"\n📡 Testing channels:")
    channels = {"main": config.main_channel}
    if config.temp_channel != config.main_channel:
        channels["temp"] = config.temp_channel

    for name, ch_id in channels.items():
        bot = bot_pool.get_next()
        if bot is None:
            print(f"   ❌ {name}: no active bots available")
            continue
        res = bot.request("getChat", data={"chat_id": ch_id})
        if res and res.get("ok"):
            chat = res["result"]
            print(f"   ✅ {name}: {chat.get('title', '?')} ({ch_id})")
            # Check each bot's permissions
            for b in bot_pool.list_bots():
                if not b.id:
                    continue
                mres = b.request("getChatMember", data={
                    "chat_id": ch_id, "user_id": b.id
                })
                if mres and mres.get("ok"):
                    status = mres["result"]["status"]
                    can_post = mres["result"].get("can_post_messages",
                                                   status == "administrator")
                    can_delete = mres["result"].get("can_delete_messages",
                                                    status == "administrator")
                    icon = "✅" if (can_post and can_delete) else "⚠️"
                    print(f"      {icon} @{b.username}: "
                          f"status={status}, post={can_post}, delete={can_delete}")
                else:
                    print(f"      ❌ @{b.username}: no access")
        else:
            err = res.get("description") if res else "No response"
            print(f"   ❌ {name}: {err}")


def cmd_ls(args, config):
    """List recent files in main channel (using forwardMessage trick)."""
    if not config.main_channel:
        print("❌ Main channel not set.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    # Send a marker message, then inspect messages backward
    # (Telegram Bot API doesn't have getHistory, so this is the workaround)
    bot = bot_pool.get_next()
    marker_res = bot.request("sendMessage", data={
        "chat_id": config.main_channel,
        "text": "_ls_marker_",
        "disable_web_page_preview": True,
        "disable_notification": True,
    })
    if not marker_res or not marker_res.get("ok"):
        print("❌ Cannot send marker message.")
        return
    marker_id = marker_res["result"]["message_id"]

    # Delete the marker immediately
    bot.request("deleteMessage", data={
        "chat_id": config.main_channel,
        "message_id": marker_id,
    })

    # Inspect previous messages by forwarding them one-by-one to temp channel
    print(f"\n📋 Recent files in main channel (scanning last {args.limit} messages):\n")
    found = 0
    for msg_id in range(marker_id - 1, max(0, marker_id - args.limit - 1), -1):
        if found >= args.limit:
            break
        bot = bot_pool.get_next()
        res = bot.request("forwardMessage", data={
            "chat_id": config.temp_channel,
            "from_chat_id": config.main_channel,
            "message_id": msg_id,
            "disable_notification": True,
        })
        if not res or not res.get("ok"):
            continue
        msg = res["result"]
        # Delete the forwarded copy
        bot.request("deleteMessage", data={
            "chat_id": config.temp_channel,
            "message_id": msg["message_id"],
        })

        caption = msg.get("caption", "") or msg.get("text", "")
        if caption.startswith(MANIFEST_PREFIX):
            try:
                # Only parse the first line (header) — the rest is JSON
                # which may contain characters that confuse a naive split.
                first_line = caption.split("\n", 1)[0]
                parts = first_line.split("|")
                fname = parts[1]
                total_parts = int(parts[2])
                hash_prefix = parts[3]
                link = build_share_link(config.main_channel, msg_id)
                print(f"  📄 {fname} ({total_parts} parts) | {hash_prefix}...")
                print(f"     🔗 {link}")
                found += 1
            except (IndexError, ValueError):
                pass
    if found == 0:
        print("  (no manifest files found in recent messages)")


def cmd_delete(args, config):
    """Delete a file's messages (description, parts, manifest) from channel."""
    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    try:
        chat_id, message_id = parse_telegram_link(args.link)
    except ValueError as e:
        print(f"❌ {e}")
        return

    print(f"🌐 Fetching manifest at {args.link}...")
    downloader = Downloader(config, bot_pool)
    manifest = downloader._fetch_manifest(chat_id, message_id)
    if not manifest:
        print("❌ Could not fetch manifest.")
        return
    downloader._cleanup()

    msg_ids = []
    if manifest.get("description_msg_id"):
        msg_ids.append(manifest["description_msg_id"])
    msg_ids.extend(manifest.get("message_ids", []))
    msg_ids.append(message_id)  # manifest itself

    print(f"🗑️ Deleting {len(msg_ids)} messages...")
    if not args.force:
        confirm = input(f"   Type 'yes' to confirm deletion of {len(msg_ids)} messages: ")
        if confirm.strip().lower() != "yes":
            print("Cancelled.")
            return

    deleted = 0
    for mid in msg_ids:
        bot = bot_pool.get_next()
        res = bot.request("deleteMessage", data={
            "chat_id": manifest["channel_id"],
            "message_id": mid,
        })
        if res and res.get("ok"):
            deleted += 1
        else:
            err = res.get("description") if res else "No response"
            print(f"   ⚠️ Failed to delete {mid}: {err}")
    print(f"✅ Deleted {deleted}/{len(msg_ids)} messages.")

    # Also mark as deleted in database if enabled
    db = config.get_db()
    if db:
        try:
            existing = db.get_file_by_sha(manifest.get("sha256", ""))
            if existing:
                db.mark_deleted(existing["id"])
                print(f"💾 Database: marked file #{existing['id']} as deleted")
        except Exception as e:
            print(f"⚠️ Could not update database: {e}")


def cmd_cleanup(args, config):
    """Clean up temp channel by deleting recent messages."""
    if not config.temp_channel:
        print("❌ Temp channel not set.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    bot = bot_pool.get_next()
    print(f"🧹 Cleaning temp channel ({config.temp_channel})...")

    # Send marker
    test_res = bot.request("sendMessage", data={
        "chat_id": config.temp_channel,
        "text": "_cleanup_marker_",
        "disable_notification": True,
    })
    if not test_res or not test_res.get("ok"):
        print("❌ Cannot send marker.")
        return
    test_msg_id = test_res["result"]["message_id"]
    print(f"   Marker at: {test_msg_id}")

    count = 0
    for msg_id in range(test_msg_id, max(0, test_msg_id - args.max_count), -1):
        res = bot.request("deleteMessage", data={
            "chat_id": config.temp_channel,
            "message_id": msg_id,
        })
        if res and res.get("ok"):
            count += 1

    print(f"✅ Deleted {count} messages.")


# ==========================================
# Database commands
# ==========================================
def cmd_db(args, config):
    """Database management commands."""
    # Special case: 'enable' doesn't require DB to be already enabled
    if args.db_action == "enable":
        config.db_enabled = True
        if not config.db_path:
            config.db_path = config.get_db_path()
        config.save()
        # Initialize the database file
        from .db import Database
        Database(config.get_db_path())
        print(f"✅ Database enabled: {config.get_db_path()}")
        return

    # All other actions require DB to be enabled
    db = config.get_db()
    if db is None:
        print("❌ Database is not enabled.")
        print("   Run: python tg.py db enable")
        return

    elif args.db_action == "disable":
        config.db_enabled = False
        config.save()
        print("✅ Database disabled. (File kept on disk.)")
        print(f"   To re-enable: python tg.py db enable")
        return

    elif args.db_action == "info":
        path = config.get_db_path()
        if not os.path.exists(path):
            print(f"❌ Database file does not exist yet: {path}")
            print("   It will be created automatically on first upload.")
            return
        size = os.path.getsize(path)
        print(f"📍 Database: {path}")
        print(f"   Size: {format_size(size)}")
        print(f"   Enabled: {'yes' if config.db_enabled else 'no'}")
        stats = db.stats()
        print(f"\n📊 Stats:")
        print(f"   Files: {stats['total_files']}")
        print(f"   Total size: {format_size(stats['total_size'])}")
        print(f"   Total downloads: {stats['total_downloads']}")
        if stats["top_files"]:
            print(f"\n   Top downloaded files:")
            for f in stats["top_files"]:
                print(f"     • {f['name']} ({format_size(f['size'])}) — {f['dl_count']} downloads")

    elif args.db_action == "list":
        limit = args.limit or 50
        rows = db.list_files(limit=limit, status="uploaded")
        if not rows:
            print("No files in database.")
            return
        print(f"📋 Files in database ({len(rows)} shown):")
        print(f"{'─' * 80}")
        print(f"{'ID':<4} {'Name':<30} {'Size':<10} {'Parts':<6} {'Date':<20} {'Link'}")
        print(f"{'─' * 80}")
        for r in rows:
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            name = r["name"][:30]
            link = r["share_link"] or ""
            print(f"{r['id']:<4} {name:<30} {format_size(r['size']):<10} {r['total_parts']:<6} {date:<20} {link}")

    elif args.db_action == "search":
        # Support both positional and --query
        query = args.query or getattr(args, "query_opt", None)
        if not query:
            print("❌ Search query required: python tg.py db search <query>")
            return
        rows = db.search_files(query)
        if not rows:
            print(f"No files matching '{query}'.")
            return
        print(f"🔍 Search results for '{query}' ({len(rows)} found):")
        print(f"{'─' * 80}")
        for r in rows:
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            print(f"  #{r['id']}  {r['name']}  ({format_size(r['size'])})  {date}")
            if r["description"]:
                print(f"         {r['description'][:80]}")
            if r["share_link"]:
                print(f"         🔗 {r['share_link']}")

    elif args.db_action == "stats":
        stats = db.stats()
        print("📊 Database statistics:")
        print(f"   Total files: {stats['total_files']}")
        print(f"   Total size:  {format_size(stats['total_size'])}")
        print(f"   Total downloads: {stats['total_downloads']}")
        if stats["top_files"]:
            print(f"\n   Top downloaded files:")
            for f in stats["top_files"]:
                print(f"     • {f['name']} ({format_size(f['size'])}) — {f['dl_count']} downloads")

    elif args.db_action == "export":
        if not args.output:
            default = "tg-vault-export.json"
            args.output = default
        n = db.export_json(args.output)
        print(f"✅ Exported {n} records to {args.output}")

    elif args.db_action == "sync":
        sync_db_to_channel(config, bot_pool=None, verbose=True)

    elif args.db_action == "restore":
        restore_db_from_channel(config, bot_pool=None, verbose=True)

    elif args.db_action == "query":
        # Build filters from args
        filters = _build_filters_from_args(args)
        rows = db.query_files(filters)
        count = db.count_files(filters)
        if not rows:
            print(f"No files matching the filter. ({count} total matches)")
            return
        print(f"🔍 Query results ({len(rows)} of {count} shown):")
        print(f"{'─' * 100}")
        print(f"{'ID':<4} {'Name':<30} {'Size':<10} {'Parts':<6} {'Enc':<4} {'Date':<20} {'Link'}")
        print(f"{'─' * 100}")
        for r in rows:
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            name = r["name"][:30]
            enc = "🔐" if r.get("encrypted") else "  "
            link = r["share_link"] or ""
            print(f"{r['id']:<4} {name:<30} {format_size(r['size']):<10} {r['total_parts']:<6} {enc:<4} {date:<20} {link}")
        print(f"\n💡 To download: python tg.py db download {rows[0]['id']}")
        print(f"   Or all matching: python tg.py db download --all-matching [same filters]")

    elif args.db_action == "count":
        filters = _build_filters_from_args(args)
        count = db.count_files(filters)
        print(f"📊 Count: {count} files match the filter")
        if filters:
            print(f"   Filters: {filters}")

    elif args.db_action == "download":
        _db_download(args, config, db)

    elif args.db_action == "vacuum":
        # Reclaim unused space in the DB file
        db_path = config.get_db_path()
        if not os.path.exists(db_path):
            print(f"❌ Database file does not exist: {db_path}")
            return
        old_size = os.path.getsize(db_path)
        print(f"📊 DB size before VACUUM: {format_size(old_size)}")
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM;")
        conn.close()
        new_size = os.path.getsize(db_path)
        saved = old_size - new_size
        print(f"✅ VACUUM complete!")
        print(f"   Before: {format_size(old_size)}")
        print(f"   After:  {format_size(new_size)}")
        if saved > 0:
            print(f"   Saved:  {format_size(saved)} ({(saved/old_size)*100:.1f}% reduction)")
        else:
            print(f"   No space reclaimed (DB was already optimized)")

    elif args.db_action == "find":
        # Scan channel for latest DB backup and update config
        print("🔍 Scanning channel for DB backup messages...")
        bot_pool = BotPool(config.bots)
        found_id = find_latest_db_backup(config, bot_pool, verbose=True)
        if found_id:
            print(f"\n✅ Found! DB backup is at message {found_id}")
            print(f"   Config updated with new db_sync_msg_id: {found_id}")
            print(f"   You can now run: python tg.py db restore")
        else:
            print("\n❌ No DB backup found in channel.")
            print("   Run: python tg.py db sync")

    elif args.db_action == "find-orphans":
        # Scan ALL storage channels for messages not in database.
        # Uses the batched orphan scanner; results are stored in the `orphans`
        # table for later review / deletion via `db orphans list|delete|clear`.
        from .orphan_scanner import scan_orphans
        max_scan = getattr(args, "max_scan", 500) or 500
        batch_size = getattr(args, "batch_size", 500) or 500
        delay = getattr(args, "delay", 0.5)
        if isinstance(delay, str):
            try:
                delay = float(delay)
            except ValueError:
                delay = 0.5
        bot_pool = BotPool(config.bots)

        # Scan all storage channels
        all_channels = config.get_all_storage_channels()
        total_stats = {"found_new_orphans": 0, "already_known_orphans": 0,
                       "scanned_messages": 0, "skipped_known": 0}
        for i, ch_id in enumerate(all_channels, 1):
            if len(all_channels) > 1:
                print(f"\n{'#' * 60}")
                print(f"📡 Scanning channel {i}/{len(all_channels)}: {ch_id}")
                print(f"{'#' * 60}")
            stats = scan_orphans(
                config, bot_pool=bot_pool,
                max_scan=max_scan, batch_size=batch_size, delay=delay,
                verbose=True, channel_id=ch_id,
            )
            if "error" in stats:
                continue
            total_stats["found_new_orphans"] += stats.get("found_new_orphans", 0)
            total_stats["already_known_orphans"] += stats.get("already_known_orphans", 0)
            total_stats["scanned_messages"] += stats.get("scanned_messages", 0)
            total_stats["skipped_known"] += stats.get("skipped_known", 0)

        if len(all_channels) > 1:
            print(f"\n{'=' * 60}")
            print(f"📊 All channels scanned:")
            print(f"   Total new orphans:         {total_stats['found_new_orphans']}")
            print(f"   Total existing refreshed:  {total_stats['already_known_orphans']}")
            print(f"   Total messages scanned:    {total_stats['scanned_messages']}")
            print(f"   Total known skipped:       {total_stats['skipped_known']}")
            print(f"{'=' * 60}")

        # Show next steps
        if total_stats["found_new_orphans"] > 0 or total_stats["already_known_orphans"] > 0:
            print(f"\n💡 Next steps:")
            print(f"   python tg.py db orphans list                    — review found orphans")
            print(f"   python tg.py db orphans delete --ids 1          — delete one orphan (from Telegram + DB)")
            print(f"   python tg.py db orphans delete --ids 1,2,3      — delete multiple orphans")
            print(f"   python tg.py db orphans delete --ids all --force — delete ALL orphans")
            print(f"   python tg.py db orphans clear                   — just clear the local orphan list")

    elif args.db_action == "orphans":
        # Orphan management subcommand
        sub = getattr(args, "query", None) or getattr(args, "query_opt", None)
        if sub == "list" or sub is None:
            orphans = db.list_orphans(include_deleted=False)
            if not orphans:
                print("✅ No orphaned messages in the local DB.")
                print("   Run `python tg.py db find-orphans` to scan the channel.")
                return
            print(f"📋 Orphaned messages in DB ({len(orphans)}):")
            print(f"{'─' * 110}")
            print(f"{'ID':<4} {'Msg ID':<8} {'Type':<10} {'Name':<35} {'Size':<10} {'Discovered':<20} {'Link'}")
            print(f"{'─' * 110}")
            import time as _time
            for o in orphans:
                when = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(o["discovered_at"]))
                name = (o.get("name") or "?")[:35]
                link = o.get("share_link") or ""
                msg_type = o.get("message_type") or "?"
                size_val = o.get("file_size")
                size_str = format_size(size_val) if size_val else "—"
                print(f"{o['id']:<4} {o['msg_id']:<8} {msg_type:<10} {name:<35} "
                      f"{size_str:<10} {when:<20} {link}")
            print(f"\n💡 To delete: python tg.py db orphans delete --ids <ID>")
            print(f"   To delete all: python tg.py db orphans delete --ids all --force")
        elif sub == "delete":
            from .orphan_scanner import delete_orphan_from_telegram
            if getattr(args, "all_matching", False) or getattr(args, "ids", None) == "all":
                # Delete ALL orphans
                orphans = db.list_orphans(include_deleted=False)
                if not orphans:
                    print("✅ No orphans to delete.")
                    return
                if not getattr(args, "force", False):
                    confirm = input(f"   Delete ALL {len(orphans)} orphans from Telegram + DB? (yes/no): ")
                    if confirm.strip().lower() != "yes":
                        print("Cancelled.")
                        return
                bot_pool = BotPool(config.bots)
                total = len(orphans)
                ok = 0
                for i, o in enumerate(orphans, 1):
                    print(f"\n[{i}/{total}] Deleting orphan #{o['id']} — {o.get('name', '?')}")
                    if delete_orphan_from_telegram(
                        config, o["id"], bot_pool=bot_pool, verbose=True, force=True
                    ):
                        ok += 1
                print(f"\n📊 Deleted {ok}/{total} orphans.")
            elif getattr(args, "ids", None):
                # Delete multiple by comma-separated IDs (also accepts single ID)
                try:
                    ids = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
                except ValueError:
                    print(f"❌ Invalid IDs: {args.ids}")
                    return
                bot_pool = BotPool(config.bots)
                ok = 0
                for oid in ids:
                    if delete_orphan_from_telegram(
                        config, oid, bot_pool=bot_pool, verbose=True,
                        force=getattr(args, "force", True),  # skip per-file confirm, already bulk
                    ):
                        ok += 1
                print(f"\n📊 Deleted {ok}/{len(ids)} orphans.")
            else:
                print("❌ Specify what to delete:")
                print("   python tg.py db orphans delete --ids 1            — single orphan")
                print("   python tg.py db orphans delete --ids 1,2,3         — multiple orphans")
                print("   python tg.py db orphans delete --ids all --force   — delete ALL orphans")
                print("   python tg.py db orphans delete --all-matching --force — same as --ids all")
        elif sub == "clear":
            # Just clear the local DB rows (doesn't touch Telegram)
            n = db.clear_orphans(include_deleted=False)
            print(f"✅ Cleared {n} orphan row(s) from the local DB.")
            print("   (Messages in Telegram were NOT deleted.)")
        elif sub == "count":
            print(f"📊 Orphans in local DB: {db.orphan_count()}")
        else:
            print(f"❌ Unknown orphan action: {sub}")
            print("   Valid actions: list, delete, clear, count")

    elif args.db_action == "edit":
        # Edit description and/or tags of an uploaded file.
        _db_edit(args, config, db)

    elif args.db_action == "verify":
        # Verify database integrity — check for share_link vs manifest_msg_id
        # mismatches (caused by the old update_share_link bug)
        _db_verify(config, db, force=getattr(args, "force", False))

    elif args.db_action == "find-missing":
        # Check each file in DB — is its manifest still in the channel?
        _db_find_missing(config, db)

    elif args.db_action == "clear-temp":
        # Delete ALL messages from temp channel except DB backup
        _db_clear_temp_keep_db(config, db)

    elif args.db_action == "delete":
        # Delete a file by ID from both Telegram and database
        if not args.query:
            print("❌ File ID required: python tg.py db delete <ID>")
            return
        try:
            file_id = int(args.query)
        except ValueError:
            print(f"❌ Invalid file ID: {args.query}")
            return

        file_record = db.get_file_by_id(file_id)
        if not file_record:
            print(f"❌ No file with ID {file_id}")
            return

        print(f"📄 File: {file_record['name']} ({format_size(file_record['size'])})")
        print(f"   Parts: {file_record['total_parts']}")
        print(f"   Link: {file_record.get('share_link', 'N/A')}")

        # Collect all message IDs to delete
        msg_ids = []
        if file_record.get("description_msg_id"):
            msg_ids.append(file_record["description_msg_id"])
        try:
            part_ids = json.loads(file_record.get("message_ids", "[]"))
            msg_ids.extend(part_ids)
        except Exception:
            pass
        if file_record.get("manifest_msg_id"):
            msg_ids.append(file_record["manifest_msg_id"])

        channel_id = file_record.get("main_channel") or config.main_channel

        if not getattr(args, "force", False):
            confirm = input(f"\n   Delete {len(msg_ids)} messages from Telegram + DB record? (yes/no): ")
            if confirm.strip().lower() != "yes":
                print("Cancelled.")
                return

        bot_pool = BotPool(config.bots)
        deleted = 0
        for mid in msg_ids:
            bot = bot_pool.get_next()
            res = bot.request("deleteMessage", data={
                "chat_id": channel_id,
                "message_id": mid,
            })
            if res and res.get("ok"):
                deleted += 1
            else:
                err = res.get("description") if res else "No response"
                print(f"   ⚠️ Failed to delete msg {mid}: {err}")

        # Mark as deleted in DB
        db.mark_deleted(file_id)
        print(f"\n✅ Deleted {deleted}/{len(msg_ids)} messages from Telegram.")
        print(f"💾 Database: marked file #{file_id} as deleted.")


def _build_filters_from_args(args):
    """Build a filters dict from argparse args for query/count/download."""
    filters = {}
    if getattr(args, "name", None):
        filters["name"] = args.name
    if getattr(args, "desc", None):
        filters["description"] = args.desc
    if getattr(args, "tag", None):
        filters["tag"] = args.tag
    if getattr(args, "min_size", None) is not None:
        filters["min_size"] = args.min_size
    if getattr(args, "max_size", None) is not None:
        filters["max_size"] = args.max_size
    if getattr(args, "min_parts", None) is not None:
        filters["min_parts"] = args.min_parts
    if getattr(args, "max_parts", None) is not None:
        filters["max_parts"] = args.max_parts
    if getattr(args, "encrypted", False):
        filters["encrypted"] = True
    elif getattr(args, "not_encrypted", False):
        filters["encrypted"] = False
    if getattr(args, "compressed", False):
        filters["compressed"] = True
    elif getattr(args, "not_compressed", False):
        filters["compressed"] = False
    # Date parsing
    for field in ["since", "until"]:
        val = getattr(args, field, None)
        if val:
            try:
                # Try YYYY-MM-DD format
                dt = datetime.datetime.strptime(val, "%Y-%m-%d")
                filters[field] = int(dt.timestamp())
            except ValueError:
                try:
                    filters[field] = int(val)  # Unix timestamp
                except ValueError:
                    print(f"⚠️ Invalid date '{val}', use YYYY-MM-DD or unix timestamp")
    filters["sort"] = getattr(args, "sort", "date")
    filters["sort_dir"] = "asc" if getattr(args, "asc", False) else "desc"
    filters["limit"] = getattr(args, "limit", 50)
    filters["offset"] = getattr(args, "offset", 0)
    return filters


def _db_download(args, config, db):
    """Download files from database by ID, IDs, or all-matching filter."""
    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    # Determine which files to download
    files_to_download = []

    if getattr(args, "all_matching", False):
        # Download all files matching the current filter
        filters = _build_filters_from_args(args)
        # Remove sort/limit/offset for download — we want ALL matches
        filters.pop("sort", None)
        filters.pop("sort_dir", None)
        filters.pop("limit", None)
        filters.pop("offset", None)
        files_to_download = db.query_files(filters)
        if not files_to_download:
            print("❌ No files match the filter.")
            return
        print(f"📥 Downloading {len(files_to_download)} files matching filter...")

    elif getattr(args, "ids", None):
        # Download by comma-separated IDs
        try:
            ids = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
        except ValueError:
            print(f"❌ Invalid IDs: {args.ids}")
            return
        files_to_download = db.get_files_by_ids(ids)
        if not files_to_download:
            print(f"❌ No files found with IDs: {ids}")
            return
        print(f"📥 Downloading {len(files_to_download)} files by ID...")

    elif args.query:
        # Download by single ID
        try:
            file_id = int(args.query)
        except ValueError:
            print(f"❌ Invalid file ID: {args.query}")
            return
        file_record = db.get_file_by_id(file_id)
        if not file_record:
            print(f"❌ No file with ID {file_id}")
            return
        files_to_download = [file_record]
        print(f"📥 Downloading 1 file by ID...")

    else:
        print("❌ Specify what to download:")
        print("   python tg.py db download <ID>              — single file by ID")
        print("   python tg.py db download --ids 1,2,3       — multiple files by IDs")
        print("   python tg.py db download --all-matching    — all files matching filter")
        print("   (combine --all-matching with --name, --tag, --min-size, etc.)")
        return

    output_dir = getattr(args, "db_output_dir", ".")
    downloader = Downloader(config, bot_pool, db=db)

    total = len(files_to_download)
    success_count = 0
    for i, f in enumerate(files_to_download, 1):
        print(f"\n{'=' * 60}")
        print(f"📥 [{i}/{total}] #{f['id']} {f['name']} ({format_size(f['size'])})")
        print(f"{'=' * 60}")
        link = f.get("share_link")
        if not link:
            print(f"❌ No share_link in DB for file #{f['id']}")
            continue
        try:
            password = getattr(args, "password", None) if f.get("encrypted") else None
            success = downloader.download(link, output_dir=output_dir, password=password)
            if success:
                success_count += 1
        except Exception as e:
            print(f"❌ Error: {e}")

    print(f"\n{'=' * 60}")
    print(f"📊 Download summary: {success_count}/{total} files downloaded successfully")
    print(f"{'=' * 60}")


# ==========================================
# Edit file metadata (description + tags)
# ==========================================
def _db_edit(args, config, db):
    """Edit description and/or tags of one or more uploaded files.

    Updates both:
      - The Telegram description message (editMessageText)
      - The Telegram manifest message (editMessageText or editMessageCaption)
      - The database record (files.description, files.hashtags, files.tags)

    Usage:
        # Single file
        python tg.py db edit <ID> --desc "New description" --tag new,tags
        python tg.py db edit <ID> --desc "New description only"
        python tg.py db edit <ID> --tag new,tags,only

        # Bulk edit multiple files
        python tg.py db edit --ids 1,2,3 --desc "Shared description" --tag batch,2026
        python tg.py db edit --ids 1,2,3 --add-tag backup          # add tag to all
        python tg.py db edit --ids 1,2,3 --remove-tag old          # remove tag from all
        python tg.py db edit --ids 1,2,3 --desc "New desc"         # set same desc for all

    Modes:
      --tag X,Y,Z      Replace all tags with X,Y,Z
      --add-tag X      Add tag X (preserves existing tags, dedupes)
      --remove-tag X   Remove tag X if present

    Note: Only works for files with a text manifest (manifest_type='text').
    File manifests (manifest_type='file') cannot be edited because the JSON
    is inside a file attachment, not editable text. Use --manifest-type text
    on upload to ensure editability.
    """
    new_desc = getattr(args, "desc", None)
    new_tag_str = getattr(args, "tag", None)
    add_tag_str = getattr(args, "add_tag", None)
    remove_tag_str = getattr(args, "remove_tag", None)
    ids_str = getattr(args, "ids", None)

    # Determine target file IDs
    file_ids = []
    if ids_str:
        # Bulk mode: --ids 1,2,3
        try:
            file_ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
        except ValueError:
            print(f"❌ Invalid IDs: {ids_str}")
            return
    elif args.query:
        # Single file mode
        try:
            file_ids = [int(args.query)]
        except ValueError:
            print(f"❌ Invalid file ID: {args.query}")
            return
    else:
        print("❌ File ID required:")
        print("   python tg.py db edit <ID> --desc \"...\" --tag ...")
        print("   python tg.py db edit --ids 1,2,3 --desc \"...\" --tag ...")
        return

    if not file_ids:
        print("❌ No file IDs specified.")
        return

    # Parse tag operations
    replace_tags = None
    add_tags = []
    remove_tags = []

    if new_tag_str:
        raw_tags = [t.strip() for t in new_tag_str.split(",") if t.strip()]
        replace_tags = sanitize_hashtags(raw_tags)
        if len(replace_tags) != len(raw_tags):
            print(f"⚠️ Some tags were sanitized:")
            print(f"   Original: {raw_tags}")
            print(f"   Sanitized: {replace_tags}")

    if add_tag_str:
        raw_add = [t.strip() for t in add_tag_str.split(",") if t.strip()]
        add_tags = sanitize_hashtags(raw_add)

    if remove_tag_str:
        raw_rem = [t.strip() for t in remove_tag_str.split(",") if t.strip()]
        remove_tags = [t.lower() for t in raw_rem]

    # Check if we have anything to do
    if not new_desc and replace_tags is None and not add_tags and not remove_tags:
        # Show current state
        if len(file_ids) == 1:
            file_id = file_ids[0]
            record = db.get_file_by_id(file_id)
            if not record:
                print(f"❌ No file with ID {file_id}")
                return
            print(f"📄 Current file: #{file_id} — {record['name']}")
            print(f"   Description: {record.get('description') or '(empty)'}")
            try:
                current_tags = json.loads(record.get("hashtags", "[]"))
            except Exception:
                current_tags = []
            print(f"   Hashtags: {', '.join(current_tags) if current_tags else '(empty)'}")
            print(f"\n💡 To edit:")
            print(f"   python tg.py db edit {file_id} --desc \"New description\"")
            print(f"   python tg.py db edit {file_id} --tag new,tags,here")
            print(f"   python tg.py db edit {file_id} --add-tag backup")
            print(f"   python tg.py db edit {file_id} --remove-tag old")
        else:
            print(f"📄 {len(file_ids)} files selected for bulk edit: {file_ids}")
            print(f"\n💡 To edit:")
            print(f"   python tg.py db edit --ids {','.join(map(str, file_ids))} --desc \"New desc\"")
            print(f"   python tg.py db edit --ids {','.join(map(str, file_ids))} --add-tag backup")
            print(f"   python tg.py db edit --ids {','.join(map(str, file_ids))} --remove-tag old")
        return

    # Bulk or single edit
    print(f"📄 Editing {len(file_ids)} file(s): {file_ids}")
    if new_desc is not None:
        print(f"   Set description: {new_desc}")
    if replace_tags is not None:
        print(f"   Replace tags: {replace_tags}")
    if add_tags:
        print(f"   Add tags: {add_tags}")
    if remove_tags:
        print(f"   Remove tags: {remove_tags}")
    print()

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    success_count = 0
    for file_id in file_ids:
        record = db.get_file_by_id(file_id)
        if not record:
            print(f"❌ No file with ID {file_id}")
            continue

        # Compute the final tags for this file
        try:
            current_tags = json.loads(record.get("hashtags", "[]")) if record.get("hashtags") else []
        except Exception:
            current_tags = []

        if replace_tags is not None:
            final_tags = list(replace_tags)
        else:
            final_tags = list(current_tags)

        # Add tags (dedupe case-insensitive)
        if add_tags:
            existing_lower = {t.lower() for t in final_tags}
            for t in add_tags:
                if t.lower() not in existing_lower:
                    final_tags.append(t)
                    existing_lower.add(t.lower())

        # Remove tags
        if remove_tags:
            final_tags = [t for t in final_tags if t.lower() not in remove_tags]

        # Compute final description
        final_desc = new_desc if new_desc is not None else (record.get("description") or "")

        # Show per-file changes
        print(f"  #{file_id} {record['name']}")
        if new_desc is not None:
            print(f"     desc: '{record.get('description') or ''}' → '{final_desc}'")
        if final_tags != current_tags:
            print(f"     tags: {current_tags} → {final_tags}")

        # Apply the edit
        if _apply_file_edit(config, bot_pool, db, record, final_desc, final_tags):
            success_count += 1
        else:
            print(f"     ⚠️ Partial failure for file #{file_id}")

    print(f"\n📊 Done: {success_count}/{len(file_ids)} files updated successfully.")


def _apply_file_edit(config, bot_pool, db, record, new_desc, new_tags):
    """Apply a single-file edit (description + tags) to Telegram + DB.

    Args:
        record: The DB file record dict.
        new_desc: The new description string.
        new_tags: The new tags list (replaces all existing tags).

    Returns True on success, False on partial failure.
    """
    bot = bot_pool.get_next()
    file_id = record["id"]
    channel_id = record.get("main_channel") or config.main_channel
    desc_msg_id = record.get("description_msg_id")
    manifest_msg_id = record.get("manifest_msg_id")

    # Get current tags (for comparison)
    try:
        current_tags = json.loads(record.get("hashtags", "[]")) if record.get("hashtags") else []
    except Exception:
        current_tags = []

    success = True

    # 1. Edit the description message (if description or tags changed)
    if desc_msg_id and (new_desc != (record.get("description") or "") or new_tags != current_tags):
        lines = [
            f"📦 File: {record['name']}",
            f"💾 Size: {format_size(record['size'])}",
            f"🔢 Parts: {record['total_parts']}",
            f"🔐 SHA256: {record['sha256']}",
        ]
        if new_desc:
            lines.append("")
            lines.append("📝 Description:")
            lines.append(new_desc)
        if new_tags:
            lines.append("")
            tag_str = " ".join(f"#{t.lstrip('#')}" for t in new_tags)
            lines.append(tag_str)

        new_text = truncate_text("\n".join(lines))
        res = bot.request("editMessageText", data={
            "chat_id": channel_id,
            "message_id": desc_msg_id,
            "text": new_text,
            "disable_web_page_preview": True,
        })
        if not (res and res.get("ok")):
            err = res.get("description") if res else "No response"
            print(f"     ⚠️ Description message edit failed: {err}")
            success = False

    # 2. Edit the manifest message
    if manifest_msg_id:
        manifest = _reconstruct_manifest_from_db(record)
        manifest["description"] = new_desc
        manifest["hashtags"] = new_tags

        from .constants import MANIFEST_PREFIX
        header = f"{MANIFEST_PREFIX}|{manifest['name']}|{manifest['total_parts']}|{manifest['sha256'][:16]}"
        json_str = json.dumps(manifest, ensure_ascii=False, separators=(',', ':'))
        new_manifest_text = header + "\n" + json_str

        if len(new_manifest_text) > 4090:
            print(f"     ⚠️ Manifest text too long ({len(new_manifest_text)} chars) — skipped Telegram edit")
        else:
            res = bot.request("editMessageText", data={
                "chat_id": channel_id,
                "message_id": manifest_msg_id,
                "text": new_manifest_text,
                "disable_web_page_preview": True,
            })
            if not (res and res.get("ok")):
                # Try caption fallback (file manifest)
                caption_res = bot.request("editMessageCaption", data={
                    "chat_id": channel_id,
                    "message_id": manifest_msg_id,
                    "caption": truncate_caption(header),
                })
                if not (caption_res and caption_res.get("ok")):
                    print(f"     ⚠️ Manifest edit failed (text + caption)")
                    success = False

    # 3. Update the database record
    try:
        with get_db_conn(config.get_db_path()) as conn:
            conn.execute("UPDATE files SET description=? WHERE id=?", (new_desc, file_id))
            import json as _json
            tags_json = _json.dumps(new_tags)
            tags_csv = ",".join(new_tags)
            conn.execute("UPDATE files SET hashtags=?, tags=? WHERE id=?",
                         (tags_json, tags_csv, file_id))
            conn.execute("DELETE FROM tags WHERE file_id=?", (file_id,))
            import time as _time
            now = int(_time.time())
            for tag in new_tags:
                conn.execute("INSERT OR IGNORE INTO tags (file_id, tag, created_at) VALUES (?,?,?)",
                             (file_id, tag, now))
    except Exception as e:
        print(f"     ⚠️ Database update failed: {e}")
        success = False

    return success


def _reconstruct_manifest_from_db(record):
    """Reconstruct a manifest dict from a database file record."""
    import json as _json
    try:
        message_ids = _json.loads(record.get("message_ids", "[]"))
    except Exception:
        message_ids = []
    try:
        hashtags = _json.loads(record.get("hashtags", "[]"))
    except Exception:
        hashtags = []

    manifest = {
        "name": record["name"],
        "size": record["size"],
        "total_parts": record["total_parts"],
        "chunk_size": record.get("chunk_size", 0),
        "message_ids": message_ids,
        "sha256": record["sha256"],
        "channel_id": record.get("main_channel", ""),
        "description_msg_id": record.get("description_msg_id"),
        "description": record.get("description", ""),
        "hashtags": hashtags,
        "session_id": record.get("session_id", ""),
        "version": 8,
        "created_at": record.get("uploaded_at", 0),
        "encrypted": bool(record.get("encrypted")),
        "compressed": bool(record.get("compressed")),
        "has_chunk_header": bool(record.get("has_chunk_header")),
        "manifest_type": "text",
        "manifest_message_id": record.get("manifest_msg_id"),
    }
    if record.get("encryption_salt"):
        manifest["encryption_salt"] = record["encryption_salt"]
        manifest["encryption_algorithm"] = record.get("encryption_algorithm", "aes-256-gcm")
        manifest["encryption_kdf"] = record.get("encryption_kdf", "pbkdf2-sha512-600k")
    return manifest


def get_db_conn(db_path):
    """Get a SQLite connection (used by _db_edit for direct updates)."""
    import sqlite3
    from .db import get_conn
    return get_conn(db_path)


# ==========================================
# Database integrity verification
# ==========================================
def _db_verify(config, db, force=False):
    """Verify database integrity.

    Checks for:
      1. share_link vs manifest_msg_id mismatches (caused by the old
         update_share_link bug where re-uploaded files only had their
         share_link updated, not message_ids/manifest_msg_id)

    For each mismatch, offers to fix by fetching the manifest from the
    share_link and updating the DB record.
    """
    print("🔍 Verifying database integrity...\n")

    all_files = db.list_files(limit=100000, status=None)
    mismatches = []

    for f in all_files:
        link = f.get("share_link") or ""
        manifest_msg_id = f.get("manifest_msg_id")

        if not link or not manifest_msg_id:
            continue

        # Extract msg_id from link
        try:
            link_msg = int(link.rsplit("/", 1)[1])
        except (ValueError, IndexError):
            continue

        if link_msg != manifest_msg_id:
            mismatches.append(f)

    if not mismatches:
        print("✅ All records are consistent. No mismatches found.")
        return

    print(f"⚠️ Found {len(mismatches)} file(s) with share_link / manifest_msg_id mismatch:")
    print(f"   (These were likely re-uploaded, but the DB wasn't fully updated.)\n")

    for f in mismatches:
        link = f["share_link"]
        link_msg = int(link.rsplit("/", 1)[1])
        print(f"  #{f['id']} {f['name']}")
        print(f"     DB manifest_msg_id: {f['manifest_msg_id']}")
        print(f"     share_link points to: {link_msg}")
        print(f"     message_ids: {f.get('message_ids', '[]')[:80]}...")
        print()

    print("💡 To fix: the tool will fetch the manifest from the share_link")
    print("   and update message_ids, manifest_msg_id, description_msg_id.")
    print()

    if not config.bots:
        print("❌ No bots configured — cannot fetch manifests to fix.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots — cannot fetch manifests to fix.")
        return

    if not force:
        confirm = input(f"Fix all {len(mismatches)} mismatches now? (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("Cancelled.")
            return

    from .downloader import Downloader
    downloader = Downloader(config, bot_pool)

    fixed = 0
    for f in mismatches:
        link = f["share_link"]
        file_id = f["id"]
        name = f["name"]
        db_manifest_msg_id = f["manifest_msg_id"]
        channel_id_str = f.get("main_channel", "") or str(config.main_channel)
        # Parse channel ID (could be string like "-100..." or "@username")
        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            channel_id = channel_id_str

        print(f"\n  Fixing #{file_id} {name}...")

        # Build a list of message IDs to try, in order of preference:
        # 1. manifest_msg_id from DB (the first upload's manifest — most likely to still exist)
        # 2. message_id from share_link (the re-uploaded manifest — may have been deleted)
        candidates = []
        if db_manifest_msg_id:
            candidates.append(("manifest_msg_id", db_manifest_msg_id))
        try:
            link_msg = int(link.rsplit("/", 1)[1])
            if link_msg != db_manifest_msg_id:
                candidates.append(("share_link", link_msg))
        except (ValueError, IndexError):
            pass

        manifest = None
        working_msg_id = None
        for source_label, msg_id in candidates:
            print(f"     Trying {source_label} (msg {msg_id})...")
            try:
                manifest = downloader._fetch_manifest(channel_id, msg_id)
                downloader._cleanup()
                if manifest:
                    working_msg_id = msg_id
                    print(f"     ✅ Found manifest at msg {msg_id}")
                    break
            except Exception as e:
                print(f"     ⚠️ Failed: {e}")

        if not manifest:
            print(f"     ❌ Cannot fetch manifest from any known message ID")
            print(f"        The file may need to be re-uploaded. Marking as 'corrupted'.")
            with get_db_conn(config.get_db_path()) as conn:
                conn.execute("UPDATE files SET status='corrupted' WHERE id=?", (file_id,))
            continue

        # Build the correct share_link from the working message
        correct_share_link = build_share_link(channel_id, working_msg_id)
        if correct_share_link is None:
            correct_share_link = link  # fallback to old link

        # Ensure manifest_message_id is set (it's not in the manifest JSON content,
        # it's added by the uploader after sending the manifest message)
        manifest["manifest_message_id"] = working_msg_id

        # Update the DB record with the correct manifest data
        db.update_share_link(file_id, correct_share_link, manifest=manifest)
        print(f"     ✅ Updated DB:")
        print(f"        share_link → {correct_share_link}")
        print(f"        message_ids → {manifest.get('message_ids', [])[:5]}...")
        print(f"        manifest_msg_id → {manifest.get('manifest_message_id')}")
        fixed += 1

    print(f"\n📊 Fixed {fixed}/{len(mismatches)} files.")
    if fixed < len(mismatches):
        print("   Files that couldn't be fixed were marked as 'corrupted'.")
        print("   You may need to re-upload them.")


def _db_find_missing(config, db):
    """Check each file in DB — is its manifest still accessible in the channel?

    For each file, tries to forwardMessage the manifest from the main channel.
    If it fails, the file is marked as 'corrupted' in the DB.

    Files marked as 'corrupted' can be cleaned up or re-uploaded.
    """
    print("🔍 Checking for missing files...\n")

    all_files = db.list_files(limit=100000, status="uploaded")
    if not all_files:
        print("No uploaded files to check.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    bot = bot_pool.get_next()
    missing = []
    ok = 0

    for i, f in enumerate(all_files, 1):
        file_id = f["id"]
        name = f["name"]
        manifest_msg_id = f.get("manifest_msg_id")
        channel_id_str = f.get("main_channel", "") or str(config.main_channel)
        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            channel_id = channel_id_str

        # Build candidates to try: manifest_msg_id first, then share_link
        candidates = []
        if manifest_msg_id:
            candidates.append(manifest_msg_id)
        link = f.get("share_link") or ""
        if link:
            try:
                link_msg = int(link.rsplit("/", 1)[1])
                if link_msg not in candidates:
                    candidates.append(link_msg)
            except (ValueError, IndexError):
                pass

        if not candidates:
            print(f"  [{i}/{len(all_files)}] #{file_id} {name} — ⚠️ no manifest_msg_id or share_link")
            continue

        # Try each candidate
        found = False
        for msg_id in candidates:
            fwd_res = bot.request("forwardMessage", data={
                "chat_id": config.temp_channel,
                "from_chat_id": channel_id,
                "message_id": msg_id,
                "disable_notification": True,
            })
            if fwd_res and fwd_res.get("ok"):
                # Manifest exists — delete the forwarded copy
                fwd_msg_id = fwd_res["result"]["message_id"]
                bot.request("deleteMessage", data={
                    "chat_id": config.temp_channel, "message_id": fwd_msg_id,
                })
                found = True
                # If we found it at a different message_id than manifest_msg_id,
                # update the DB
                if msg_id != manifest_msg_id:
                    print(f"  [{i}/{len(all_files)}] #{file_id} {name} — ⚠️ manifest at msg {msg_id} (DB says {manifest_msg_id}) — fixing DB")
                    with get_db_conn(config.get_db_path()) as conn:
                        conn.execute("UPDATE files SET manifest_msg_id=?, status='uploaded' WHERE id=?",
                                     (msg_id, file_id))
                else:
                    print(f"  [{i}/{len(all_files)}] #{file_id} {name} — ✅ OK")
                break

        if found:
            ok += 1
        else:
            err = "message to forward not found"
            print(f"  [{i}/{len(all_files)}] #{file_id} {name} — ❌ MISSING ({err})")
            missing.append(f)
            # Mark as corrupted in DB
            with get_db_conn(config.get_db_path()) as conn:
                conn.execute("UPDATE files SET status='corrupted' WHERE id=?", (file_id,))

    print(f"\n{'=' * 50}")
    print(f"📊 Results:")
    print(f"   Total checked:  {len(all_files)}")
    print(f"   OK:             {ok}")
    print(f"   Missing:        {len(missing)}")
    if missing:
        print(f"\n   Missing files:")
        for f in missing:
            print(f"     #{f['id']} {f['name']} (manifest was at msg {f['manifest_msg_id']})")
        print(f"\n💡 These files were marked as 'corrupted' in the DB.")
        print(f"   You may need to re-upload them or fix the manifest message IDs.")


def _db_clear_temp_keep_db(config, db):
    """Delete ALL messages from the temp channel EXCEPT the DB backup.

    The DB backup message ID is stored in config.db_sync_msg_id.
    """
    temp_channel = config.temp_channel
    if not temp_channel:
        print("❌ Temp channel not set.")
        return

    bot_pool = BotPool(config.bots)
    if len(bot_pool) == 0:
        print("❌ No active bots.")
        return

    bot = bot_pool.get_next()

    # Send a marker to find the latest message ID
    marker_res = bot.request("sendMessage", data={
        "chat_id": temp_channel, "text": "_clear_temp_marker_",
        "disable_notification": True,
    })
    if not marker_res or not marker_res.get("ok"):
        print("❌ Cannot send marker.")
        return
    marker_id = marker_res["result"]["message_id"]
    bot.request("deleteMessage", data={
        "chat_id": temp_channel, "message_id": marker_id,
    })

    db_backup_msg_id = config.db_sync_msg_id

    print(f"🧹 Clearing temp channel {temp_channel} (keeping DB backup at msg {db_backup_msg_id})...")
    print(f"   Scanning {marker_id} messages...")

    deleted = 0
    for msg_id in range(marker_id, 0, -1):
        if msg_id == db_backup_msg_id:
            continue  # keep DB backup
        res = bot.request("deleteMessage", data={
            "chat_id": temp_channel,
            "message_id": msg_id,
        })
        if res and res.get("ok"):
            deleted += 1
        if msg_id % 100 == 0:
            print(f"   ... deleted {deleted} so far (at msg {msg_id})")

    print(f"\n✅ Deleted {deleted} messages from temp channel.")
    if db_backup_msg_id:
        print(f"   DB backup (msg {db_backup_msg_id}) was preserved.")
