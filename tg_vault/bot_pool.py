"""
Bot and BotPool — thread-safe multi-bot rotation with rate limiting.

Each bot has its own ``requests.Session`` (connection pooling), per-bot rate
limiter (50 ms minimum interval → FloodWait-safe ~20 req/sec), and request/error
counters. The pool rotates bots in round-robin order, thread-safe.
"""

import sys
import threading
import time

try:
    import requests
except ImportError:
    print("Error: 'requests' not installed. Install with: pip install requests")
    sys.exit(1)

from .constants import BOT_MIN_INTERVAL, MAX_RETRIES, BASE_RETRY_DELAY


class Bot:
    """A single bot with its own session, rate limiter, and stats."""

    def __init__(self, token, username=""):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()
        self.username = username
        self.id = None
        self.first_name = ""
        self._last_request_time = 0.0
        self._lock = threading.Lock()
        self.request_count = 0
        self.error_count = 0

    def init_info(self):
        """Fetch bot's id and username from Telegram."""
        try:
            r = self.session.get(self.api_url + "getMe", timeout=30).json()
            if r.get("ok"):
                self.id = r["result"]["id"]
                self.username = r["result"].get("username", self.username)
                self.first_name = r["result"].get("first_name", "")
                return True
            else:
                print(f"Warning: bot {self.token[:15]}... rejected: {r.get('description', '?')}")
        except requests.exceptions.ConnectionError:
            print(f"Warning: cannot connect to api.telegram.org for {self.token[:15]}...")
            print("         Check your internet connection or configure a proxy in the GUI Settings tab.")
        except Exception as e:
            print(f"Warning: failed to fetch bot info for {self.token[:15]}...: {e}")
        return False

    def throttle(self):
        """Enforce min interval between requests to avoid FloodWait."""
        with self._lock:
            now = time.time()
            wait = BOT_MIN_INTERVAL - (now - self._last_request_time)
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.time()

    def request(self, method, data=None, files=None, retries=MAX_RETRIES):
        """Send a request to Telegram with FloodWait handling and retry."""
        self.throttle()
        url = self.api_url + method
        for attempt in range(1, retries + 1):
            try:
                if files:
                    res = self.session.post(url, data=data, files=files, timeout=300)
                else:
                    res = self.session.post(url, data=data, timeout=60)
                self.request_count += 1
                result = res.json()

                if result.get("ok"):
                    return result

                error_code = result.get("error_code", 0)

                # FloodWait — wait and retry
                if error_code == 429:
                    params = result.get("parameters", {}) or {}
                    retry_after = params.get("retry_after", 5)
                    print(f"\n  ⏳ FloodWait @{self.username}: {retry_after}s...")
                    time.sleep(retry_after + 1)
                    continue

                # 5xx — retry with backoff
                if 500 <= error_code < 600:
                    delay = BASE_RETRY_DELAY * attempt
                    time.sleep(delay)
                    continue

                # 4xx — return error (caller decides)
                return result

            except requests.exceptions.RequestException as e:
                delay = BASE_RETRY_DELAY * attempt
                print(f"\n  ⚠️ Network error @{self.username}: {e}. Retry in {delay}s...")
                time.sleep(delay)

        self.error_count += 1
        return None


class BotPool:
    """Thread-safe round-robin pool of bots."""

    def __init__(self, bots_config):
        self.bots = []
        self._counter = 0
        self._lock = threading.Lock()

        for b in bots_config:
            bot = Bot(b["token"], b.get("username", ""))
            if bot.init_info():
                self.bots.append(bot)
            else:
                print(f"Warning: bot {b['token'][:15]}... could not be initialized.")

    def get_next(self):
        """Get the next bot in round-robin order (thread-safe)."""
        with self._lock:
            if not self.bots:
                return None
            bot = self.bots[self._counter % len(self.bots)]
            self._counter += 1
            return bot

    def __len__(self):
        return len(self.bots)

    def list_bots(self):
        return list(self.bots)

    def stats(self):
        return [
            {
                "username": b.username,
                "id": b.id,
                "requests": b.request_count,
                "errors": b.error_count,
            }
            for b in self.bots
        ]
