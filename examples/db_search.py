#!/usr/bin/env python3
"""
db_search.py — Search the tg-vault database from a script.

Useful for:
  - Finding files by name, description, or hashtag
  - Listing all files matching a pattern
  - Filtering results for further processing

Usage:
    python examples/db_search.py "movie"
    python examples/db_search.py "backup" --limit 10
    python examples/db_search.py "2026" --json    # output as JSON
"""
import argparse
import json
import sys
from pathlib import Path

# Allow importing the tg_vault package from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from tg_vault.db import Database  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Search tg-vault database")
    parser.add_argument("query", help="Search query (matches name, description, hashtags)")
    parser.add_argument("--db", default=None, help="Path to database (default: from config)")
    parser.add_argument("--limit", type=int, default=50, help="Max results")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Determine DB path
    db_path = args.db
    if not db_path:
        # Load from config
        import os
        config_path = os.path.expanduser("~/.tg-vault.json")
        if not os.path.exists(config_path):
            print(f"Error: config file not found: {config_path}")
            sys.exit(1)
        with open(config_path) as f:
            cfg = json.load(f)
        db_path = cfg.get("db_path") or os.path.join(os.path.dirname(config_path), "tg-vault.db")

    if not Path(db_path).exists():
        print(f"Error: database file not found: {db_path}")
        sys.exit(1)

    # Search
    db = Database(db_path)
    results = db.search_files(args.query)

    if not results:
        print(f"No files matching '{args.query}'.")
        sys.exit(0)

    if args.json:
        # Convert timestamps to ISO format for JSON output
        import time
        for r in results:
            r["uploaded_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["uploaded_at"]))
            if r.get("last_accessed_at"):
                r["last_accessed_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["last_accessed_at"]))
            r["message_ids"] = json.loads(r["message_ids"]) if r["message_ids"] else []
            r["hashtags"] = json.loads(r["hashtags"]) if r["hashtags"] else []
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"🔍 Search results for '{args.query}' ({len(results)} found):")
        print("-" * 80)
        for r in results:
            import time
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            print(f"  #{r['id']}  {r['name']}  ({r['size']:,} bytes)  {date}")
            if r["description"]:
                print(f"         {r['description'][:80]}")
            if r["share_link"]:
                print(f"         🔗 {r['share_link']}")


if __name__ == "__main__":
    main()
