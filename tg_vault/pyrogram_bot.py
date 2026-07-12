"""
HybridBot — Pyrogram + Bot API hybrid transport for tg-vault.

When ``api_id`` and ``api_hash`` are configured, this bot uses Pyrogram
(MTProto) for large file operations, bypassing Bot API limits:

    | Operation  | Bot API limit | Pyrogram limit (bot) |
    |------------|---------------|----------------------|
    | Upload     | 50 MB         | 2 GB                 |
    | Download   | 20 MB         | 2 GB                 |

For all other operations (sendMessage, deleteMessage, forwardMessage,
getMe, etc.) the standard Bot API (``requests``) is used because it is
faster and simpler for small payloads.

If Pyrogram is not installed or fails to start, the bot falls back to
Bot API only — the 20 MB / 50 MB limits apply.
"""

import asyncio
import io
import os
import sys
import tempfile
import threading
import time

import requests

from .constants import BOT_MIN_INTERVAL, MAX_RETRIES, BASE_RETRY_DELAY

# Try to import Pyrogram
try:
    from pyrogram import Client as PyrogramClient
    PYROGRAM_AVAILABLE = True
except ImportError:
    PYROGRAM_AVAILABLE = False


class HybridBot:
    """A bot that uses Pyrogram for large files and Bot API for everything else.

    Public interface mirrors :class:`tg_vault.bot_pool.Bot` so it can be
    used as a drop-in replacement inside :class:`BotPool`.
    """

    def __init__(self, token, api_id=None, api_hash=None, username=""):
        self.token = token
        self.api_id = api_id
        self.api_hash = api_hash
        self.username = username
        self.id = None
        self.first_name = ""

        # Bot API session (always available)
        self.session = requests.Session()
        self.api_url = f"https://api.telegram.org/bot{token}/"

        # Pyrogram client (optional)
        self._pyro = None
        self._pyro_started = False
        self._loop = None
        self._loop_thread = None
        self._pyro_lock = threading.Lock()  # serialize Pyrogram calls

        # Rate limiting (same as Bot)
        self._last_request_time = 0.0
        self._rate_lock = threading.Lock()
        self.request_count = 0
        self.error_count = 0

        # Threshold: files larger than this use Pyrogram
        self._large_file_threshold = 45 * 1024 * 1024  # 45 MB

    # ─────────────────────── Initialization ───────────────────────

    def init_info(self):
        """Fetch bot info via Bot API and start Pyrogram if configured."""
        # Step 1: Bot API getMe
        try:
            r = self.session.get(self.api_url + "getMe", timeout=30).json()
            if r.get("ok"):
                self.id = r["result"]["id"]
                self.username = r["result"].get("username", self.username)
                self.first_name = r["result"].get("first_name", "")
            else:
                print(f"Warning: bot {self.token[:15]}... rejected: {r.get('description', '?')}")
                return False
        except requests.exceptions.ConnectionError:
            print(f"Warning: cannot connect to api.telegram.org for {self.token[:15]}...")
            return False
        except Exception as e:
            print(f"Warning: failed to fetch bot info for {self.token[:15]}...: {e}")
            return False

        # Step 2: Start Pyrogram if api_id/api_hash provided
        if self.api_id and self.api_hash and PYROGRAM_AVAILABLE:
            self._start_pyrogram()
        elif self.api_id and self.api_hash and not PYROGRAM_AVAILABLE:
            print(f"Warning: api_id/api_hash configured but Pyrogram not installed.")
            print(f"         Install with: pip install pyrogram tgcrypto")
            print(f"         Falling back to Bot API only (20 MB download / 50 MB upload limits)")

        return True

    def _start_pyrogram(self):
        """Initialize and start the Pyrogram client with a persistent event loop."""
        try:
            session_dir = os.path.expanduser("~/.tg-vault-sessions")
            os.makedirs(session_dir, exist_ok=True)

            self._pyro = PyrogramClient(
                name=f"bot_{self.id}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                bot_token=self.token,
                workdir=session_dir,
                in_memory=True,
            )

            # Create a persistent event loop running in a dedicated thread.
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._run_event_loop,
                daemon=True,
            )
            self._loop_thread.start()

            # Start the Pyrogram client using the raw async method
            # (bypass Pyrogram's sync wrapper which uses the wrong event loop)
            self._pyro_started = True  # Set before calling _call_pyro
            self._call_pyro("start", timeout=60)
        except Exception as e:
            print(f"Warning: Pyrogram failed to start for @{self.username}: {e}")
            print(f"         Falling back to Bot API only (20 MB / 50 MB limits apply)")
            self._pyro = None
            self._pyro_started = False

    def _run_event_loop(self):
        """Run the event loop in a dedicated thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro, timeout=300):
        """Run a coroutine in the persistent event loop (thread-safe)."""
        if not self._pyro_started or not self._loop:
            raise RuntimeError("Pyrogram not started")
        with self._pyro_lock:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=timeout)

    def _call_pyro(self, method_name, *args, timeout=300, **kwargs):
        """Call a Pyrogram method (by name) in the persistent event loop.

        Uses ``__wrapped__`` to bypass Pyrogram's sync wrapper and access
        the underlying async coroutine directly.
        """
        if not self._pyro_started or not self._pyro:
            raise RuntimeError("Pyrogram not started")
        method = getattr(self._pyro, method_name)
        coro = method.__wrapped__(self._pyro, *args, **kwargs)
        return self._run_async(coro, timeout=timeout)

    # ─────────────────────── Rate Limiting ───────────────────────

    def throttle(self):
        """Enforce min interval between requests (same as Bot)."""
        with self._rate_lock:
            now = time.time()
            wait = BOT_MIN_INTERVAL - (now - self._last_request_time)
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.time()

    # ─────────────────────── Bot API Request ───────────────────────

    def _request_bot_api(self, method, data=None, files=None, retries=MAX_RETRIES):
        """Standard Bot API request (identical to Bot.request)."""
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

                if error_code == 429:
                    params = result.get("parameters", {}) or {}
                    retry_after = params.get("retry_after", 5)
                    print(f"\n  ⏳ FloodWait @{self.username}: {retry_after}s...")
                    time.sleep(retry_after + 1)
                    continue

                if 500 <= error_code < 600:
                    delay = BASE_RETRY_DELAY * attempt
                    time.sleep(delay)
                    continue

                return result

            except requests.exceptions.RequestException as e:
                delay = BASE_RETRY_DELAY * attempt
                print(f"\n  ⚠️ Network error @{self.username}: {e}. Retry in {delay}s...")
                time.sleep(delay)

        self.error_count += 1
        return None

    # ─────────────────────── Main Request Dispatcher ───────────────────────

    def request(self, method, data=None, files=None, retries=MAX_RETRIES):
        """Handle a Bot API request.

        For ``sendDocument`` with large files, uses Pyrogram if available.
        Everything else goes through Bot API.
        """
        self.throttle()

        # Route sendDocument to Pyrogram for large files
        if method == "sendDocument" and files and self._pyro_started:
            file_size = self._get_file_size(files)
            if file_size and file_size > self._large_file_threshold:
                return self._send_document_pyrogram(data, files, retries)

        # All other methods → Bot API
        return self._request_bot_api(method, data, files, retries)

    def _get_file_size(self, files):
        """Extract file size from files dict."""
        doc = files.get("document")
        if not doc:
            return 0
        if isinstance(doc, tuple) and len(doc) >= 2:
            data = doc[1]
            if isinstance(data, (bytes, bytearray)):
                return len(data)
            if isinstance(data, io.BytesIO):
                return data.getbuffer().nbytes
        return 0

    # ─────────────────────── Pyrogram Operations ───────────────────────

    def _send_document_pyrogram(self, data, files, retries):
        """Send a document using Pyrogram (up to 2 GB)."""
        chat_id = data.get("chat_id")
        caption = data.get("caption", "")
        reply_to = data.get("reply_to_message_id")

        doc = files.get("document")
        filename = "document"
        file_data = None

        if isinstance(doc, tuple) and len(doc) >= 2:
            filename = doc[0] or "document"
            file_data = doc[1]
        elif isinstance(doc, (bytes, bytearray)):
            file_data = doc
        elif isinstance(doc, io.BytesIO):
            file_data = doc.getvalue()

        if file_data is None:
            return self._request_bot_api("sendDocument", data, files, retries)

        # Write to temp file (Pyrogram needs a file path for large uploads)
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as f:
            f.write(file_data)
            temp_path = f.name

        try:
            msg = self._call_pyro(
                "send_document",
                chat_id=chat_id,
                document=temp_path,
                file_name=filename,
                caption=caption if caption else None,
                reply_to_message_id=reply_to,
                disable_notification=True,
            )
            result = {"ok": True, "result": self._convert_msg(msg)}
            self.request_count += 1
            return result

        except Exception as e:
            print(f"\n  ⚠️ Pyrogram sendDocument error @{self.username}: {e}")
            print(f"     Retrying with Bot API...")
            self.error_count += 1
            return self._request_bot_api("sendDocument", data, files, retries)

        finally:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass

    def download_media(self, chat_id, message_id):
        """Download a document from a channel message using Pyrogram.

        Bypasses the 20 MB ``getFile`` limit. Does NOT require forwarding
        to a temp channel.

        Returns ``bytes`` of the file content, or ``None`` on failure.
        """
        if not self._pyro_started:
            return None

        with tempfile.NamedTemporaryFile(delete=False, suffix=".tgdl") as f:
            temp_path = f.name

        try:
            msg = self._call_pyro("get_messages", chat_id, message_id)
            if not msg or not msg.document:
                return None

            dl_path = self._call_pyro("download_media", message=msg, file_name=temp_path)

            if dl_path and os.path.exists(dl_path):
                with open(dl_path, "rb") as f:
                    return f.read()

            return None

        except Exception as e:
            print(f"\n  ⚠️ Pyrogram download error @{self.username}: {e}")
            self.error_count += 1
            return None

        finally:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass

    def get_messages(self, chat_id, message_ids):
        """Get message info via Pyrogram. Returns Bot API-format dict."""
        if not self._pyro_started:
            return None

        try:
            msg = self._call_pyro("get_messages", chat_id, message_ids)
            return self._convert_msg(msg)
        except Exception as e:
            print(f"\n  ⚠️ Pyrogram get_messages error: {e}")
            return None

    # ─────────────────────── Helpers ───────────────────────

    @staticmethod
    def _convert_msg(pyro_msg):
        """Convert a Pyrogram message to Bot API format."""
        if not pyro_msg:
            return None

        result = {
            "message_id": pyro_msg.id,
            "date": int(pyro_msg.date.timestamp()) if pyro_msg.date else None,
            "chat": {"id": pyro_msg.chat.id} if pyro_msg.chat else None,
        }

        if pyro_msg.text:
            result["text"] = pyro_msg.text
        if pyro_msg.caption:
            result["caption"] = pyro_msg.caption
        if pyro_msg.document:
            doc = pyro_msg.document
            result["document"] = {
                "file_id": doc.file_id,
                "file_unique_id": doc.file_unique_id,
                "file_name": doc.file_name,
                "file_size": doc.file_size,
                "mime_type": doc.mime_type,
            }

        return result

    def stop(self):
        """Stop the Pyrogram client."""
        if self._pyro_started and self._pyro:
            try:
                self._call_pyro("stop", timeout=10)
            except:
                pass
            self._pyro_started = False
            if self._loop:
                self._loop.call_soon_threadsafe(self._loop.stop)

    def __del__(self):
        try:
            self.stop()
        except:
            pass
