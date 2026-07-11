"""
Self-describing chunk header for tg-vault v8+.

Inspired by TAS's WAS1 header. Each chunk starts with a small binary header
that lets us identify the chunk without consulting the database.

Header format (40 bytes total):
  Offset  Length  Field
  0       4       Magic: "TGV1" (0x54 0x47 0x56 0x31)
  4       2       Version (uint16 LE) — currently 1
  6       2       Flags (uint16 LE) — bit 0: compressed, bit 1: encrypted
  8       4       Chunk index (uint32 LE)
  12      4       Total chunks (uint32 LE)
  16      8       Original file size (uint64 LE)
  24      16      First 16 bytes of file SHA256 (for quick identification)

The header is prepended to each chunk's payload before upload. On download,
we strip the header and use it to verify ordering and integrity.

This is OPTIONAL — v8 chunks without the header still work via manifest lookup.
"""

import struct

MAGIC = b"TGV1"
HEADER_VERSION = 1
HEADER_SIZE = 40

# Flags
FLAG_COMPRESSED = 0x01
FLAG_ENCRYPTED = 0x02


def create_header(chunk_index: int, total_chunks: int, original_size: int,
                  sha256_prefix: bytes, flags: int = 0) -> bytes:
    """Create a 40-byte chunk header.

    Args:
        chunk_index: 0-based index of this chunk
        total_chunks: Total number of chunks
        original_size: Original file size in bytes (before any processing)
        sha256_prefix: First 16 bytes of the file's SHA256 hash
        flags: Bitfield of FLAG_* values

    Returns:
        40-byte bytes object
    """
    if len(sha256_prefix) != 16:
        raise ValueError(f"sha256_prefix must be 16 bytes, got {len(sha256_prefix)}")

    return struct.pack(
        "<4sHHIIQ16s",
        MAGIC,
        HEADER_VERSION,
        flags,
        chunk_index,
        total_chunks,
        original_size,
        sha256_prefix,
    )


def parse_header(data: bytes) -> dict:
    """Parse a chunk header from the start of a data buffer.

    Args:
        data: At least HEADER_SIZE bytes

    Returns:
        dict with keys: magic, version, flags, chunk_index, total_chunks,
        original_size, sha256_prefix, compressed (bool), encrypted (bool)

    Raises:
        ValueError if magic doesn't match
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Data too short for header: {len(data)} < {HEADER_SIZE}")

    magic, version, flags, chunk_index, total_chunks, original_size, sha256_prefix = struct.unpack(
        "<4sHHIIQ16s", data[:HEADER_SIZE]
    )

    if magic != MAGIC:
        raise ValueError(f"Invalid magic: expected {MAGIC!r}, got {magic!r}")

    return {
        "magic": magic,
        "version": version,
        "flags": flags,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "original_size": original_size,
        "sha256_prefix": sha256_prefix,
        "compressed": bool(flags & FLAG_COMPRESSED),
        "encrypted": bool(flags & FLAG_ENCRYPTED),
    }


def is_chunk_with_header(data: bytes) -> bool:
    """Check if a data buffer starts with the TGV1 magic."""
    return len(data) >= 4 and data[:4] == MAGIC
