"""
Compression module for tg-vault — smart gzip with format-aware bypass.

Inspired by TAS (https://github.com/ixchio/tas).

Features:
  - Skips compression for already-compressed formats (jpg, mp4, zip, etc.)
  - Only uses compression if it actually reduces size
  - Returns a flag so callers know whether to decompress on download
"""

import gzip
import io
import os

# File extensions that are already compressed — skip compression for these
# to save CPU time (compressing them again usually INCREASES size).
SKIP_COMPRESSION_EXTENSIONS = {
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".heif",
    # Video
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v",
    # Audio
    ".mp3", ".aac", ".m4a", ".ogg", ".opus", ".flac", ".wma",
    # Archives
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".tgz", ".tbz", ".txz",
    # Documents (already compressed internally)
    ".pdf", ".docx", ".xlsx", ".pptx", ".epub", ".odt", ".ods", ".odp",
    # Other
    ".dmg", ".iso", ".apk", ".ipa", ".jar", ".war", ".deb", ".rpm",
    # Encryption-related (compressing encrypted data is useless)
    ".enc", ".gpg", ".pgp", ".age",
}


def should_skip_compression(filename: str) -> bool:
    """Check if a file should skip compression based on its extension."""
    ext = os.path.splitext(filename)[1].lower()
    # Handle compound extensions like .tar.gz
    lower_name = filename.lower()
    if lower_name.endswith(".tar.gz") or lower_name.endswith(".tar.bz2") or lower_name.endswith(".tar.xz"):
        return True
    return ext in SKIP_COMPRESSION_EXTENSIONS


def compress_data(data: bytes, filename: str = "", level: int = 6) -> tuple:
    """Compress data using gzip.

    Returns (compressed_or_original_data, was_compressed: bool).

    Compression is skipped if:
      - The filename has a known compressed extension
      - Compression didn't actually reduce the size
    """
    if should_skip_compression(filename):
        return data, False

    try:
        compressed = gzip.compress(data, compresslevel=level)
        # Only use compressed version if it's actually smaller
        if len(compressed) < len(data):
            return compressed, True
    except Exception:
        # If compression fails (rare), return original
        pass

    return data, False


def decompress_data(data: bytes, was_compressed: bool) -> bytes:
    """Decompress data that was previously compressed by compress_data().

    If was_compressed is False, returns data unchanged.
    """
    if not was_compressed:
        return data
    return gzip.decompress(data)


def compress_file(input_path: str, output_path: str, level: int = 6, chunk_size: int = 1024 * 1024) -> bool:
    """Compress a file using gzip. Returns True if compression was used.

    If the file has a known compressed extension, this is a no-op (just copies).
    Otherwise, compresses with gzip and only keeps the result if it's smaller.
    """
    filename = os.path.basename(input_path)
    if should_skip_compression(filename):
        # Just copy
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break
                fout.write(chunk)
        return False

    # Compress with streaming
    original_size = os.path.getsize(input_path)
    with open(input_path, "rb") as fin, gzip.open(output_path, "wb", compresslevel=level) as fout:
        while True:
            chunk = fin.read(chunk_size)
            if not chunk:
                break
            fout.write(chunk)

    compressed_size = os.path.getsize(output_path)
    if compressed_size >= original_size:
        # Compression didn't help — use original
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break
                fout.write(chunk)
        return False

    return True


def decompress_file(input_path: str, output_path: str, was_compressed: bool, chunk_size: int = 1024 * 1024) -> None:
    """Decompress a file. If was_compressed is False, just copies."""
    if not was_compressed:
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break
                fout.write(chunk)
        return

    with gzip.open(input_path, "rb") as fin, open(output_path, "wb") as fout:
        while True:
            chunk = fin.read(chunk_size)
            if not chunk:
                break
            fout.write(chunk)
