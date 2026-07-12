"""
CLI entry point for tg-vault.

Run with ``python -m tg_vault`` or via the ``tg-vault`` script after install.
The legacy ``python tg.py`` invocation still works via a shim at the repo root.
"""

import argparse
import sys

from .constants import VERSION, DEFAULT_CONFIG_PATH
from .config import Config
from .commands import (
    cmd_init, cmd_setup, cmd_bots, cmd_channels,
    cmd_upload, cmd_download, cmd_info, cmd_test, cmd_ls, cmd_delete, cmd_cleanup,
    cmd_db,
)
from .interactive import interactive_menu, install_signal_handlers


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg-vault",
        description="tg-vault — Telegram Bot API cloud storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tg-vault init
  tg-vault setup                                      # interactive wizard (recommended)
  tg-vault bots add 123456:ABC-DEF...
  tg-vault channels set main -1001234567890
  tg-vault channels set temp -1009876543210
  tg-vault test

  # Single file
  tg-vault upload movie.mp4 --desc "Backup" --tag movies,2026
  tg-vault download https://t.me/c/1234567890/42

  # Bulk upload (multiple files)
  tg-vault upload file1.zip file2.zip file3.zip --desc "Backup batch"
  tg-vault upload *.mp4 --tag movies

  # Bulk download (multiple links)
  tg-vault download https://t.me/c/.../42 https://t.me/c/.../43 https://t.me/c/.../44
  tg-vault download --links-file my_links.txt --output-dir ~/Downloads

  # Database
  tg-vault db enable                                  # enable DB
  tg-vault db info                                    # show DB info + stats
  tg-vault db list --limit 20                         # list recent files
  tg-vault db search "movie"                          # search by name/desc/tags
  tg-vault db stats                                   # show statistics only
  tg-vault db export -o backup.json                   # export all records

  # Other
  tg-vault info    https://t.me/c/1234567890/42
  tg-vault ls      --limit 10
  tg-vault delete  https://t.me/c/1234567890/42 --force
  tg-vault cleanup --max-count 100

  # Also works as a module
  python -m tg_vault upload movie.mp4
        """
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--version", action="version",
                        version=f"tg-vault v{VERSION}")

    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="Create a sample config file")

    # setup
    subparsers.add_parser("setup", help="Interactive setup wizard (bot + channels)")

    # bots
    sp = subparsers.add_parser("bots", help="Manage bots")
    sp.add_argument("bots_action", choices=["add", "list", "remove"])
    sp.add_argument("token", nargs="?", help="Bot token (for add)")
    sp.add_argument("index", nargs="?", type=int, help="Bot index (for remove)")

    # channels
    sp = subparsers.add_parser("channels", help="Manage channels")
    sp.add_argument("channels_action", choices=["set", "show", "add", "remove"])
    sp.add_argument("name", nargs="?", help="Channel name for 'set': main or temp")
    sp.add_argument("value", nargs="?", help="Channel ID for 'set', 'add', or 'remove'")

    # upload — supports multiple files for bulk upload
    sp = subparsers.add_parser("upload", help="Upload one or more files (bulk upload supported)")
    sp.add_argument("files", nargs="+", help="One or more file paths (supports wildcards)")
    sp.add_argument("--desc", "-d", help="Description text (applied to all files)")
    sp.add_argument("--tag", "-t", help="Hashtags (comma-separated, applied to all files)")
    sp.add_argument("--resume", "-r", action="store_true", help="Resume interrupted upload")
    sp.add_argument("--encrypt", "-e", action="store_true",
                    help="Encrypt chunks with AES-256-GCM (requires --password or TG_VAULT_PASSWORD env var)")
    sp.add_argument("--password", help="Password for encryption (or set TG_VAULT_PASSWORD env var)")
    sp.add_argument("--no-compress", action="store_true",
                    help="Disable gzip compression (compression is on by default)")
    sp.add_argument("--channel", help="Upload to a specific channel ID (default: main channel)")
    sp.add_argument("--all-channels", action="store_true",
                    help="Upload to ALL storage channels (file sent to each)")
    sp.add_argument("--manifest-type", choices=["text", "file", "auto"], default=None,
                    help="Manifest format: 'text' (editable), 'file' (not editable), "
                         "'auto' (text if fits, file if > 4090 chars). "
                         "Default: uses config.default_manifest_type (which defaults to 'text').")

    # download — supports multiple links for bulk download
    sp = subparsers.add_parser("download", help="Download one or more files (bulk download supported)")
    sp.add_argument("links", nargs="+", help="One or more manifest links")
    sp.add_argument("--links-file", "-f", help="Text file containing one link per line (in addition to CLI args)")
    sp.add_argument("--resume", "-r", action="store_true", help="Resume interrupted download")
    sp.add_argument("--output", "-o", help="Output filename (only valid for single-file download)")
    sp.add_argument("--output-dir", default=".", help="Output directory (default: .)")
    sp.add_argument("--password", help="Password for decryption (or set TG_VAULT_PASSWORD env var)")

    # info
    sp = subparsers.add_parser("info", help="Show manifest info without downloading")
    sp.add_argument("link", help="Manifest message link")

    # test
    subparsers.add_parser("test", help="Test connectivity")

    # ls
    sp = subparsers.add_parser("ls", help="List recent manifest files in main channel")
    sp.add_argument("--limit", type=int, default=10, help="Max results (default 10)")

    # delete
    sp = subparsers.add_parser("delete", help="Delete a file from channel")
    sp.add_argument("link", help="Manifest message link")
    sp.add_argument("--force", action="store_true", help="Skip confirmation")

    # cleanup
    sp = subparsers.add_parser("cleanup", help="Clean up temp channel")
    sp.add_argument("--max-count", type=int, default=100)

    # db — database management
    sp = subparsers.add_parser("db", help="Database management")
    sp.add_argument("db_action", choices=["enable", "disable", "info", "list", "search", "stats",
                                          "export", "sync", "restore", "query", "download", "count",
                                          "vacuum", "find", "find-orphans", "orphans", "delete",
                                          "edit", "verify", "find-missing", "clear-temp"],
                    help="Action to perform")
    sp.add_argument("query", nargs="?", help="Search query (for 'search'), file ID (for 'download'), or orphan action (list/delete/clear/count for 'orphans')")
    sp.add_argument("--query", "-q", dest="query_opt", help="Search query (alternative, for 'search')")
    sp.add_argument("--limit", type=int, default=50, help="Max results (for 'list', 'query')")
    sp.add_argument("--output", "-o", help="Output file (for 'export')")
    # Query filters
    sp.add_argument("--name", help="Filter by filename (LIKE pattern)")
    sp.add_argument("--desc", help="Filter by description (LIKE pattern)")
    sp.add_argument("--tag", help="Filter by exact tag match")
    sp.add_argument("--min-size", type=int, help="Minimum file size in bytes")
    sp.add_argument("--max-size", type=int, help="Maximum file size in bytes")
    sp.add_argument("--min-parts", type=int, help="Minimum number of parts")
    sp.add_argument("--max-parts", type=int, help="Maximum number of parts")
    sp.add_argument("--encrypted", action="store_true", help="Only encrypted files")
    sp.add_argument("--not-encrypted", action="store_true", help="Only non-encrypted files")
    sp.add_argument("--compressed", action="store_true", help="Only compressed files")
    sp.add_argument("--not-compressed", action="store_true", help="Only non-compressed files")
    sp.add_argument("--since", help="Uploaded since (YYYY-MM-DD or unix timestamp)")
    sp.add_argument("--until", help="Uploaded until (YYYY-MM-DD or unix timestamp)")
    sp.add_argument("--sort", choices=["name", "size", "parts", "date", "downloads"], default="date",
                    help="Sort field (default: date)")
    sp.add_argument("--asc", action="store_true", help="Sort ascending (default: descending)")
    sp.add_argument("--offset", type=int, default=0, help="Pagination offset")
    # Download options
    sp.add_argument("--ids", help="Comma-separated file IDs to download (for 'download'), or 'all' (for 'orphans delete'), or bulk edit (for 'edit')")
    sp.add_argument("--all-matching", action="store_true", help="Download all files matching current filter (or delete ALL orphans)")
    sp.add_argument("--output-dir", dest="db_output_dir", default=".", help="Output directory for downloads")
    sp.add_argument("--force", action="store_true", help="Skip confirmation (for 'delete' / 'orphans delete')")
    sp.add_argument("--add-tag", dest="add_tag", help="Add tag(s) to file(s) — comma-separated, for 'edit' (preserves existing tags)")
    sp.add_argument("--remove-tag", dest="remove_tag", help="Remove tag(s) from file(s) — comma-separated, for 'edit'")
    # Orphan scanner options (for 'find-orphans')
    sp.add_argument("--max-scan", type=int, default=500,
                    help="Max messages to scan for orphans (default 500, set higher for deep scans)")
    sp.add_argument("--batch-size", type=int, default=500,
                    help="Messages per batch for orphan scan (default 500)")
    sp.add_argument("--delay", type=float, default=0.5,
                    help="Seconds to pause between scan batches (default 0.5)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # No command → interactive menu
    if not args.command:
        interactive_menu(args.config)
        return

    if args.command == "init":
        cmd_init(args, args.config)
        return

    config = Config.load(args.config)

    if args.command == "setup":
        cmd_setup(args, config)
    elif args.command == "bots":
        cmd_bots(args, config)
    elif args.command == "channels":
        cmd_channels(args, config)
    elif args.command == "upload":
        cmd_upload(args, config)
    elif args.command == "download":
        cmd_download(args, config)
    elif args.command == "info":
        cmd_info(args, config)
    elif args.command == "test":
        cmd_test(args, config)
    elif args.command == "ls":
        cmd_ls(args, config)
    elif args.command == "delete":
        cmd_delete(args, config)
    elif args.command == "cleanup":
        cmd_cleanup(args, config)
    elif args.command == "db":
        cmd_db(args, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    install_signal_handlers()
    main()
