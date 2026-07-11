"""
Pure helper functions for tg-vault.

All functions in this module are pure (no I/O, no global state) unless noted.
"""

import hashlib
import re
import threading
import time


def compute_sha256(file_path, chunk_size=8192 * 1024):
    """Stream-compute SHA256 of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def format_size(size_bytes):
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_speed(bytes_per_sec):
    """Human-readable speed."""
    if bytes_per_sec <= 0:
        return "—"
    return f"{format_size(bytes_per_sec)}/s"


def format_eta(seconds):
    """Human-readable ETA."""
    if seconds <= 0 or seconds > 86400:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m{int(seconds % 60)}s"
    return f"{int(seconds / 3600)}h{int((seconds % 3600) / 60)}m"


def sanitize_filename(name, max_len=60):
    """Remove illegal chars and truncate filename for Telegram."""
    # Remove illegal chars (Windows + Unix + Telegram)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Truncate, preserving extension
    if len(name) <= max_len:
        return name
    if "." in name:
        base, ext = name.rsplit(".", 1)
        ext = "." + ext
        if len(ext) >= max_len:
            return name[:max_len]
        return base[:max_len - len(ext)] + ext
    return name[:max_len]


def sanitize_hashtag(tag):
    """Sanitize a single hashtag to Telegram rules.

    Telegram hashtag rules (similar to Python variable naming):
      - Must start with a letter (a-z, A-Z) or underscore
      - Can contain letters, digits, and underscores
      - Other characters are not allowed

    Examples:
      "movies,2026" → "movies_2026"
      "123abc"      → "_123abc"
      "sci-fi"      → "sci_fi"
      "hello world" → "hello_world"
    """
    tag = tag.strip().lstrip("#").strip()
    if not tag:
        return None
    # Replace any invalid char with underscore
    tag = re.sub(r"[^a-zA-Z0-9_]", "_", tag)
    # If starts with a digit, prepend underscore
    if tag[0].isdigit():
        tag = "_" + tag
    # Collapse multiple underscores
    tag = re.sub(r"_+", "_", tag)
    # Strip trailing underscores
    tag = tag.rstrip("_")
    if not tag:
        return None
    return tag


def sanitize_hashtags(tags):
    """Sanitize a list of hashtags, dedupe (case-insensitive), filter empty."""
    seen = set()
    result = []
    for t in tags:
        s = sanitize_hashtag(t)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
    return result


def truncate_caption(text, max_len=1024):
    """Truncate caption to fit Telegram limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def truncate_text(text, max_len=4096):
    """Truncate message text to fit Telegram limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def parse_telegram_link(url):
    """Parse a Telegram message link → (chat_id, message_id).

    Supports:
      - https://t.me/c/2417735052/9072   (private channel)
      - https://t.me/mychannel/123        (public channel)
      - tg://resolve?domain=x&start=123
    """
    url = url.strip()
    m = re.match(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", url)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))
    m = re.match(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{4,})/(\d+)", url)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    m = re.match(r"tg://resolve\?domain=([a-zA-Z][a-zA-Z0-9_]+)&start=(\d+)", url)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    raise ValueError(f"Invalid Telegram link: {url}")


def build_share_link(chat_id, message_id):
    """Build a shareable link from chat_id and message_id."""
    if isinstance(chat_id, str):
        if chat_id.startswith("@"):
            return f"https://t.me/{chat_id[1:]}/{message_id}"
        try:
            chat_id = int(chat_id)
        except ValueError:
            return None
    if isinstance(chat_id, int) and chat_id < 0:
        s = str(chat_id)
        if s.startswith("-100"):
            return f"https://t.me/c/{s[4:]}/{message_id}"
    return None


class ProgressTracker:
    """Thread-safe progress bar with speed/ETA calculation.

    Inspired by TAS: calculates speed every 200ms based on bytes-delta,
    giving a more responsive "current speed" rather than cumulative average.
    """

    def __init__(self, total, prefix=""):
        self.total = total
        self.prefix = prefix
        self.current = 0
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._last_print = 0
        # TAS-style speed sampling
        self._speed_sample_time = self.start_time
        self._speed_sample_value = 0
        self._current_speed = 0  # bytes per second (instantaneous)

    def update(self, n=1):
        with self._lock:
            self.current += n
            now = time.time()
            # Sample speed every 200ms (like TAS)
            elapsed_since_sample = now - self._speed_sample_time
            if elapsed_since_sample >= 0.2:
                delta = self.current - self._speed_sample_value
                self._current_speed = (
                    (delta / elapsed_since_sample) if elapsed_since_sample > 0 else 0
                )
                self._speed_sample_time = now
                self._speed_sample_value = self.current
            # Throttle print to 10 Hz
            if now - self._last_print < 0.1 and self.current < self.total:
                return
            self._last_print = now
            self._print()

    def _print(self):
        if self.total == 0:
            return
        percent = (self.current / self.total) * 100
        bar_len = 30
        filled = int(bar_len * self.current // self.total)
        bar = "█" * filled + "░" * (bar_len - filled)

        # Use instantaneous speed (sampled) instead of cumulative average
        speed_str = format_speed(self._current_speed)

        # ETA based on instantaneous speed, fallback to average
        if self._current_speed > 0:
            eta = (self.total - self.current) / self._current_speed
            eta_str = format_eta(eta)
        else:
            # Fallback to cumulative average
            elapsed = time.time() - self.start_time
            if self.current > 0 and elapsed > 0:
                avg_speed = self.current / elapsed
                eta = (self.total - self.current) / avg_speed if avg_speed > 0 else 0
                eta_str = format_eta(eta)
            else:
                eta_str = "—"

        print(
            f"\r{self.prefix} |{bar}| {self.current}/{self.total} "
            f"({percent:.1f}%) {speed_str} ETA:{eta_str}    ",
            end="",
            flush=True,
        )
        if self.current >= self.total:
            print()
