#!/usr/bin/env python3
"""
encrypted_upload.py — Upload a file with AES-256-GCM encryption.

Demonstrates the v8 encryption feature. The file is encrypted client-side
before upload, so even if someone gains access to your Telegram channel,
they cannot read the file without the password.

Usage:
    # Will prompt for password interactively
    python examples/encrypted_upload.py secret.txt

    # Or provide password via env var (recommended for scripts)
    export TG_VAULT_PASSWORD="my-secret-password"
    python examples/encrypted_upload.py secret.txt

    # Or via command line (NOT recommended — password visible in shell history)
    python examples/encrypted_upload.py secret.txt --password "my-secret-password"
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Upload a file with AES-256-GCM encryption")
    parser.add_argument("file", help="File to encrypt and upload")
    parser.add_argument("--password", "-p", help="Encryption password (or set TG_VAULT_PASSWORD env var)")
    parser.add_argument("--desc", "-d", default="", help="Description text")
    parser.add_argument("--tag", "-t", default="", help="Hashtags (comma-separated)")
    parser.add_argument("--config", default=None, help="Path to config file")
    args = parser.parse_args()

    # Find tg.py
    tg_script = Path(__file__).parent.parent / "tg.py"
    if not tg_script.exists():
        print(f"Error: tg.py not found at {tg_script}")
        sys.exit(1)

    # Check file exists
    if not Path(args.file).is_file():
        print(f"Error: file not found: {args.file}")
        sys.exit(1)

    # Check that cryptography is installed
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("Error: 'cryptography' library not installed.")
        print("Install with: pip install cryptography")
        sys.exit(1)

    # Get password
    password = args.password or os.environ.get("TG_VAULT_PASSWORD")
    if not password:
        import getpass
        print("🔐 Enter a password to encrypt the file.")
        print("   (This password will be required to download the file)")
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("❌ Passwords don't match.")
            sys.exit(1)

    # Build command
    # NOTE: --config is a global flag, must come before the subcommand.
    cmd = [sys.executable, str(tg_script)]
    if args.config:
        cmd.extend(["--config", args.config])
    cmd.extend(["upload", args.file, "--encrypt"])
    if args.password:
        cmd.extend(["--password", args.password])
    elif password:
        # Set env var instead of passing on CLI (more secure)
        os.environ["TG_VAULT_PASSWORD"] = password
    if args.desc:
        cmd.extend(["--desc", args.desc])
    if args.tag:
        cmd.extend(["--tag", args.tag])

    print(f"📤 Uploading encrypted file: {args.file}")
    print(f"   Algorithm: AES-256-GCM")
    print(f"   Key derivation: PBKDF2-HMAC-SHA512 (600,000 iterations)")
    print()

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
