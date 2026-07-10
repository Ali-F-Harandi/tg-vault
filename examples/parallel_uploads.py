#!/usr/bin/env python3
"""
parallel_uploads.py — Upload multiple files concurrently using tg-vault.

Each file is uploaded in its own subprocess so they all run in parallel.
The BotPool inside each process rotates between the configured bots,
so with N bots you can safely run N parallel uploads without FloodWait.

Usage:
    python examples/parallel_uploads.py file1.zip file2.zip file3.zip
    python examples/parallel_uploads.py *.mp4
"""
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file1> [file2] [file3] ...")
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        path = Path(arg).expanduser().resolve()
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            # Add all files in the directory
            files.extend(p for p in path.iterdir() if p.is_file())
        else:
            print(f"Warning: {arg} not found")

    if not files:
        print("No files to upload.")
        sys.exit(1)

    print(f"📤 Uploading {len(files)} files in parallel...")
    print()

    # Find tg.py relative to this script
    tg_script = Path(__file__).parent.parent / "tg.py"
    if not tg_script.exists():
        print(f"Error: tg.py not found at {tg_script}")
        sys.exit(1)

    # Spawn one subprocess per file
    processes = []
    for f in files:
        cmd = [sys.executable, str(tg_script), "upload", str(f)]
        processes.append({
            "file": f.name,
            "proc": subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ),
        })

    # Wait for all and report
    print(f"{'File':<30} {'Status':<10} {'Link':<60}")
    print("-" * 100)
    for p in processes:
        out, _ = p["proc"].communicate()
        # Parse the output to find the share link
        link = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("https://t.me/"):
                link = line
                break
        status = "✅" if p["proc"].returncode == 0 and link else "❌"
        print(f"{p['file'][:30]:<30} {status:<10} {link[:60]}")


if __name__ == "__main__":
    main()
