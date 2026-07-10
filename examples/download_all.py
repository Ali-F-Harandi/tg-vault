#!/usr/bin/env python3
"""
download_all.py — Download all manifest files from a channel.

This script uses the `ls` command of tg-vault to enumerate all manifest
files in your main channel, then downloads each one in parallel.

Usage:
    python examples/download_all.py
    python examples/download_all.py --output-dir ~/Downloads
    python examples/download_all.py --limit 50
"""
import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def find_manifest_links(tg_script, limit=20):
    """Run `tg.py ls` and parse out the share links."""
    cmd = [sys.executable, str(tg_script), "ls", "--limit", str(limit)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running ls: {result.stderr}")
        return []

    # Parse links from output
    links = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("https://t.me/"):
            links.append(line)
    return links


def download_one(tg_script, link, output_dir):
    """Download a single manifest link."""
    cmd = [sys.executable, str(tg_script), "download", link,
           "--output-dir", output_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    return link, result.returncode == 0, result.stdout, result.stderr


def main():
    parser = argparse.ArgumentParser(description="Download all files from a channel")
    parser.add_argument("--output-dir", "-o", default=".",
                        help="Output directory (default: .)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max number of files to scan (default: 20)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel downloads (default: 4)")
    args = parser.parse_args()

    tg_script = Path(__file__).parent.parent / "tg.py"
    if not tg_script.exists():
        print(f"Error: tg.py not found at {tg_script}")
        sys.exit(1)

    output_dir = os.path.expanduser(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print(f"📋 Scanning for manifest files (limit {args.limit})...")
    links = find_manifest_links(tg_script, args.limit)

    if not links:
        print("No manifest files found.")
        return

    print(f"📥 Found {len(links)} files. Downloading to {output_dir}\n")

    # Download in parallel
    success = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_one, tg_script, link, output_dir): link
                   for link in links}
        for i, fut in enumerate(as_completed(futures), 1):
            link, ok, out, err = fut.result()
            status = "✅" if ok else "❌"
            print(f"  [{i}/{len(links)}] {status} {link}")
            if not ok and err:
                print(f"     {err[:200]}")
            if ok:
                success += 1
            else:
                failed += 1

    print(f"\n📊 Done: {success} downloaded, {failed} failed")


if __name__ == "__main__":
    main()
