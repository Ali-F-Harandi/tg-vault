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


def cmd_channels(args, config):
    if args.channels_action == "set":
        if args.name == "main":
            config.main_channel = args.value
            if not config.temp_channel:
                config.temp_channel = args.value
            config.save()
            print(f"✅ Main channel set: {args.value}")
            print(f"   Temp channel: {config.temp_channel}")
        elif args.name == "temp":
            config.temp_channel = args.value
            config.save()
            print(f"✅ Temp channel set: {args.value}")
        else:
            print("❌ name must be 'main' or 'temp'")

    elif args.channels_action == "show":
        print("📋 Channels:")
        print(f"   main: {config.main_channel or '(not set)'}")
        print(f"   temp: {config.temp_channel or '(not set)'}")


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

    # Bulk upload
    results = []
    total = len(files)
    for i, file_path in enumerate(files, 1):
        print(f"\n{'=' * 60}")
        print(f"📤 Uploading file {i}/{total}: {file_path}")
        print(f"{'=' * 60}")
        result = uploader.upload(
            file_path,
            description=args.desc or "",
            hashtags=hashtags,
            resume=args.resume,
            encrypt=encrypt,
            password=password,
            compress=compress,
        )
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
        # Scan main channel for manifest messages not in database
        print("🔍 Scanning main channel for orphaned files...")
        bot_pool = BotPool(config.bots)
        if len(bot_pool) == 0:
            print("❌ No active bots.")
            return
        bot = bot_pool.get_next()
        main_channel = config.main_channel

        # Get all known message_ids from DB
        known_msg_ids = set()
        all_files = db.list_files(limit=10000)
        for f in all_files:
            try:
                ids = json.loads(f.get("message_ids", "[]"))
                known_msg_ids.update(ids)
            except Exception:
                pass
            if f.get("manifest_msg_id"):
                known_msg_ids.add(f["manifest_msg_id"])
            if f.get("description_msg_id"):
                known_msg_ids.add(f["description_msg_id"])

        # Send marker to get current position
        marker_res = bot.request("sendMessage", data={
            "chat_id": main_channel, "text": "_orphan_scan_",
            "disable_notification": True,
        })
        if not marker_res or not marker_res.get("ok"):
            print("❌ Cannot send marker.")
            return
        marker_id = marker_res["result"]["message_id"]
        bot.request("deleteMessage", data={"chat_id": main_channel, "message_id": marker_id})

        # Scan backwards
        scan_count = min(500, marker_id)
        orphans = []
        print(f"   Scanning last {scan_count} messages in main channel...")
        for check_id in range(marker_id, max(0, marker_id - scan_count), -1):
            if check_id == marker_id:
                continue
            if check_id in known_msg_ids:
                continue  # Known, skip

            fwd_res = bot.request("forwardMessage", data={
                "chat_id": main_channel, "from_chat_id": main_channel,
                "message_id": check_id, "disable_notification": True,
            })
            if not fwd_res or not fwd_res.get("ok"):
                continue

            fwd_msg_id = fwd_res["result"]["message_id"]
            text = fwd_res["result"].get("text", "")
            caption = fwd_res["result"].get("caption", "")
            bot.request("deleteMessage", data={"chat_id": main_channel, "message_id": fwd_msg_id})

            # Check if it's a manifest (text or file)
            manifest_text = text if text.startswith(MANIFEST_PREFIX) else caption
            if manifest_text.startswith(MANIFEST_PREFIX):
                link = build_share_link(main_channel, check_id)
                orphans.append((check_id, manifest_text[:80], link))

        if orphans:
            print(f"\n📋 Found {len(orphans)} orphaned manifest(s):")
            for msg_id, cap, link in orphans:
                print(f"   msg {msg_id}: {cap}")
                if link:
                    print(f"      🔗 {link}")
            print(f"\n💡 To delete: python tg.py delete <link> --force")
            print(f"   Or add to DB: python tg.py download <link>")
        else:
            print(f"\n✅ No orphans found! All manifests are in the database.")

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
