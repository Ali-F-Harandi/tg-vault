#!/usr/bin/env python3
"""
backup_directory.py — Recursively back up a directory to Telegram using tg-vault.

For each file in the directory tree, this script:
  1. Computes a relative path (e.g. "photos/2026/january/beach.jpg")
  2. Uploads it via tg-vault with that path as the description
  3. Saves the returned manifest link to a local backup index file

The backup index file (default: backup_index.json) maps original paths to
manifest links, so you can later restore individual files.

Usage:
    python examples/backup_directory.py /path/to/dir
    python examples/backup_directory.py /path/to/dir --tag family-photos,2026
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Back up a directory to Telegram")
    parser.add_argument("directory", help="Directory to back up")
    parser.add_argument("--tag", "-t", default="",
                        help="Comma-separated hashtags to add to all files")
    parser.add_argument("--index", default="backup_index.json",
                        help="Path to backup index file (default: backup_index.json)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip files already in the index")
    args = parser.parse_args()

    src_dir = Path(args.directory).expanduser().resolve()
    if not src_dir.is_dir():
        print(f"Error: {src_dir} is not a directory")
        sys.exit(1)

    tg_script = Path(__file__).parent.parent / "tg.py"
    if not tg_script.exists():
        print(f"Error: tg.py not found at {tg_script}")
        sys.exit(1)

    # Load existing index (for resume)
    index = {}
    if args.resume and Path(args.index).exists():
        try:
            with open(args.index, "r", encoding="utf-8") as f:
                index = json.load(f)
            print(f"📖 Loaded {len(index)} files from {args.index}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read index: {e}")

    # Find all files
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    print(f"📁 Found {len(files)} files in {src_dir}")

    # Stats
    total_size = sum(f.stat().st_size for f in files)
    print(f"💾 Total size: {total_size / (1024*1024*1024):.2f} GB\n")

    backup_time = datetime.now().isoformat()
    new_entries = []
    skipped = 0
    failed = 0

    for i, file_path in enumerate(files, 1):
        rel_path = str(file_path.relative_to(src_dir))
        print(f"\n[{i}/{len(files)}] {rel_path} ({file_path.stat().st_size / 1024:.1f} KB)")

        if args.resume and rel_path in index:
            print(f"  ⏭️ Already in index, skipping")
            skipped += 1
            continue

        # Build description
        desc = f"Backup of: {rel_path}\nBackup time: {backup_time}"

        # Build command
        cmd = [sys.executable, str(tg_script), "upload", str(file_path),
               "--desc", desc]
        if args.tag:
            cmd.extend(["--tag", args.tag])

        # Run upload
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            print(f"  ❌ Timeout")
            failed += 1
            continue

        # Parse output for share link
        link = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("https://t.me/"):
                link = line
                break

        if link:
            print(f"  ✅ {link}")
            entry = {
                "rel_path": rel_path,
                "size": file_path.stat().st_size,
                "link": link,
                "backup_time": backup_time,
            }
            new_entries.append(entry)
            index[rel_path] = entry

            # Save index after each file (so resume works)
            with open(args.index, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
        else:
            print(f"  ❌ Failed (exit {result.returncode})")
            if result.stderr:
                print(f"     stderr: {result.stderr[:200]}")
            failed += 1

    # Summary
    print("\n" + "=" * 60)
    print("📊 Backup summary:")
    print(f"   Total files: {len(files)}")
    print(f"   ✅ Uploaded: {len(new_entries)}")
    print(f"   ⏭️ Skipped:  {skipped}")
    print(f"   ❌ Failed:   {failed}")
    print(f"   📝 Index:    {args.index}")
    print("=" * 60)


if __name__ == "__main__":
    main()
