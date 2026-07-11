"""
Interactive menu for tg-vault.

When the user runs ``python tg.py`` (or ``python -m tg_vault``) without any
subcommand, this menu is shown. It offers a 13-option interactive prompt
covering upload, download, info, list, delete, setup, bots, channels,
test, cleanup, database, and exit.
"""

import argparse
import shlex

from .constants import MANIFEST_PREFIX
from .config import Config
from .bot_pool import BotPool
from .uploader import Uploader
from .downloader import Downloader
from .utils import sanitize_hashtags
from .commands import (
    cmd_upload,
    cmd_setup,
    cmd_bots,
    cmd_channels,
    cmd_download,
    cmd_info,
    cmd_test,
    cmd_ls,
    cmd_delete,
    cmd_cleanup,
    cmd_db,
)


def interactive_menu(config_path):
    config = Config.load(config_path)

    while True:
        print("\n" + "=" * 55)
        print("    tg-vault — Telegram Cloud Storage")
        print("=" * 55)
        print(f"   bots: {len(config.bots)} | channel: {config.main_channel or '?'}")
        print(f"   db: {'✅' if config.db_enabled else '❌'}")
        print("=" * 55)
        print("1. Upload file(s)")
        print("2. Upload file (resume)")
        print("3. Download by link(s)")
        print("4. Show file info")
        print("5. List recent files")
        print("6. Delete a file")
        print("7. Setup wizard (bot + channels + db)")
        print("8. Add bot")
        print("9. Set channel")
        print("10. Test connectivity")
        print("11. Cleanup temp channel")
        print("12. Database: list/search/stats")
        print("13. Exit")

        choice = input("\nChoice: ").strip()

        if choice == "1":
            paths_raw = input("File path(s) — space-separated for bulk: ").strip()
            if not paths_raw:
                continue
            # Split by space, strip quotes
            try:
                paths = shlex.split(paths_raw)
            except ValueError:
                paths = paths_raw.split()
            desc = input("Description (optional): ").strip()
            tags = input("Hashtags (comma-separated, optional): ").strip()
            raw_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            hashtags = sanitize_hashtags(raw_tags) if raw_tags else []
            if raw_tags and hashtags != raw_tags:
                print(f"⚠️ Hashtags sanitized: {raw_tags} → {hashtags}")
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            db = config.get_db()
            args_mock = argparse.Namespace(
                files=paths, desc=desc, tag=tags, resume=False
            )
            cmd_upload(args_mock, config)

        elif choice == "2":
            path = input("File path: ").strip().strip('"').strip("'")
            if not path:
                continue
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            db = config.get_db()
            uploader = Uploader(config, bot_pool, db=db)
            uploader.upload(path, resume=True)

        elif choice == "3":
            links_raw = input("Manifest link(s) — space-separated for bulk: ").strip()
            if not links_raw:
                continue
            try:
                links = shlex.split(links_raw)
            except ValueError:
                links = links_raw.split()
            output_dir = input("Output directory (default: .): ").strip() or "."
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            db = config.get_db()
            downloader = Downloader(config, bot_pool, db=db)
            for i, link in enumerate(links, 1):
                if len(links) > 1:
                    print(f"\n{'=' * 60}")
                    print(f"📥 Downloading file {i}/{len(links)}: {link}")
                    print(f"{'=' * 60}")
                try:
                    downloader.download(link, resume=True, output_dir=output_dir)
                except ValueError as e:
                    print(f"❌ {e}")

        elif choice == "4":
            link = input("Manifest link: ").strip()
            if not link:
                continue
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            bot_pool = BotPool(config.bots)
            downloader = Downloader(config, bot_pool)
            try:
                downloader.info(link)
            except ValueError as e:
                print(f"❌ {e}")

        elif choice == "5":
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            cmd_ls(argparse.Namespace(limit=20), config)

        elif choice == "6":
            link = input("Manifest link: ").strip()
            if not link:
                continue
            errors = config.validate()
            if errors:
                for e in errors:
                    print(f"❌ {e}")
                continue
            cmd_delete(argparse.Namespace(link=link, force=False), config)

        elif choice == "7":
            cmd_setup(None, config)

        elif choice == "8":
            token = input("Bot token: ").strip()
            if not token:
                continue
            cmd_bots(argparse.Namespace(bots_action="add", token=token), config)

        elif choice == "9":
            print("1. Main channel")
            print("2. Temp channel")
            sub = input("Choice: ").strip()
            value = input("Channel ID: ").strip()
            name = "main" if sub == "1" else "temp"
            cmd_channels(argparse.Namespace(channels_action="set", name=name, value=value), config)

        elif choice == "10":
            cmd_test(None, config)

        elif choice == "11":
            count = input("Max messages to delete (default 100): ").strip()
            try:
                count = int(count) if count else 100
            except ValueError:
                count = 100
            cmd_cleanup(argparse.Namespace(max_count=count), config)

        elif choice == "12":
            # Database submenu
            if not config.db_enabled:
                print("❌ Database not enabled.")
                enable = input("Enable now? (y/N): ").strip().lower()
                if enable == "y":
                    cmd_db(argparse.Namespace(db_action="enable", query=None, limit=50, output=None), config)
                continue
            print("\n--- Database ---")
            print("a. List files")
            print("b. Search")
            print("c. Stats")
            print("d. Info")
            print("e. Export to JSON")
            sub = input("Choice: ").strip().lower()
            if sub == "a":
                limit = input("Limit (default 50): ").strip()
                try:
                    limit = int(limit) if limit else 50
                except ValueError:
                    limit = 50
                cmd_db(argparse.Namespace(db_action="list", query=None, limit=limit, output=None), config)
            elif sub == "b":
                q = input("Search query: ").strip()
                cmd_db(argparse.Namespace(db_action="search", query=q, limit=50, output=None), config)
            elif sub == "c":
                cmd_db(argparse.Namespace(db_action="stats", query=None, limit=50, output=None), config)
            elif sub == "d":
                cmd_db(argparse.Namespace(db_action="info", query=None, limit=50, output=None), config)
            elif sub == "e":
                out = input("Output file (default: tg-vault-export.json): ").strip() or "tg-vault-export.json"
                cmd_db(argparse.Namespace(db_action="export", query=None, limit=50, output=out), config)

        elif choice == "13":
            print("Goodbye!")
            break
        else:
            print("❌ Invalid choice")


def install_signal_handlers():
    """Install global signal handlers for graceful shutdown.

    Inspired by TAS — prevents silent crashes and ensures temp messages
    are cleaned up on Ctrl+C / SIGTERM.
    """
    import signal
    import sys

    def sigint_handler(signum, frame):
        print("\n\n⚠️ Interrupted by user (Ctrl+C).")
        sys.exit(130)

    def sigterm_handler(signum, frame):
        print("\n⚠️ Received SIGTERM. Shutting down.")
        sys.exit(143)

    try:
        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)
    except (ValueError, AttributeError):
        # On Windows, SIGTERM may not be available
        pass
