#!/usr/bin/env python3
"""
bulk_download.py — Download multiple files in one go using tg-vault.

This is a wrapper around `tg.py download <link1> <link2> ...` plus support
for reading links from a text file (one link per line).

Usage:
    python examples/bulk_download.py https://t.me/c/.../42 https://t.me/c/.../43
    python examples/bulk_download.py --links-file my_links.txt --output-dir ~/Downloads
    python examples/bulk_download.py --links-file links.txt --resume
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Bulk download multiple files")
    parser.add_argument("links", nargs="*", help="One or more manifest links")
    parser.add_argument("--links-file", "-f", help="Text file with one link per line")
    parser.add_argument("--output-dir", "-o", default=".", help="Output directory")
    parser.add_argument("--resume", "-r", action="store_true", help="Resume interrupted downloads")
    parser.add_argument("--config", default=None, help="Path to config file")
    args = parser.parse_args()

    tg_script = Path(__file__).parent.parent / "tg.py"
    if not tg_script.exists():
        print(f"Error: tg.py not found at {tg_script}")
        sys.exit(1)

    # Collect all links
    links = list(args.links)
    if args.links_file:
        try:
            with open(args.links_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        links.append(line)
        except OSError as e:
            print(f"Error reading links file: {e}")
            sys.exit(1)

    if not links:
        print("Error: no links provided. Use positional args or --links-file.")
        sys.exit(1)

    # Build command
    # NOTE: --config is a global flag, must come before the subcommand.
    cmd = [sys.executable, str(tg_script)]
    if args.config:
        cmd.extend(["--config", args.config])
    cmd.append("download")
    cmd.extend(links)
    cmd.extend(["--output-dir", args.output_dir])
    if args.resume:
        cmd.append("--resume")

    print(f"📥 Downloading {len(links)} files to {args.output_dir}/")
    print()

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
