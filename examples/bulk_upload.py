#!/usr/bin/env python3
"""
bulk_upload.py — Upload multiple files in one go using tg-vault.

This is just a thin wrapper around `tg.py upload <file1> <file2> ...`
to show how to script bulk uploads from Python.

Usage:
    python examples/bulk_upload.py file1.zip file2.zip file3.zip
    python examples/bulk_upload.py *.mp4 --desc "Backup batch" --tag movies,2026
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Bulk upload multiple files")
    parser.add_argument("files", nargs="+", help="Files to upload")
    parser.add_argument("--desc", "-d", default="", help="Description (applied to all)")
    parser.add_argument("--tag", "-t", default="", help="Hashtags (comma-separated)")
    parser.add_argument("--resume", "-r", action="store_true", help="Resume interrupted uploads")
    parser.add_argument("--config", default=None, help="Path to config file (default: ~/.tg-vault.json)")
    args = parser.parse_args()

    # Find tg.py
    tg_script = Path(__file__).parent.parent / "tg.py"
    if not tg_script.exists():
        print(f"Error: tg.py not found at {tg_script}")
        sys.exit(1)

    # Verify files exist
    missing = [f for f in args.files if not Path(f).is_file()]
    if missing:
        print(f"Error: files not found: {missing}")
        sys.exit(1)

    # Build command
    cmd = [sys.executable, str(tg_script), "upload"] + args.files
    if args.desc:
        cmd.extend(["--desc", args.desc])
    if args.tag:
        cmd.extend(["--tag", args.tag])
    if args.resume:
        cmd.append("--resume")
    if args.config:
        cmd.extend(["--config", args.config])

    print(f"📤 Uploading {len(args.files)} files...")
    print(f"   Command: {' '.join(cmd[:6])} ... ({len(args.files)} files)")
    print()

    # Run tg.py
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
