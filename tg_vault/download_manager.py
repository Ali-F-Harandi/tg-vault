"""
Download manager for tg-vault GUI.

Manages multiple concurrent downloads with pause/resume/cancel support.
Downloads run in background threads (not subprocesses) so they can be
controlled directly from the GUI.

Concurrency control:
  - A global semaphore limits the total number of concurrent API calls
    across ALL downloads. With N bots, the limit is N (each bot can handle
    ~20 req/sec, but FloodWait is triggered by too many concurrent calls).
  - This prevents 18 simultaneous downloads from all trying to forward
    messages at the same time, which would cause FloodWait.

Persistence:
  - Download state is saved to a JSON file (downloads.json) so that
    paused/incomplete downloads can be resumed after a GUI restart.

Each download has a state:
  - pending:   queued, not started yet
  - downloading: actively downloading chunks
  - paused:    temporarily stopped (can be resumed)
  - completed:  finished successfully
  - failed:    failed (error occurred)
  - cancelled:  permanently stopped by user

Files are downloaded to a `.temp` subfolder inside the output directory.
On completion, the file is moved to the final destination.
"""

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .constants import TG_FILE_SIZE_LIMIT, MANIFEST_PREFIX
from .utils import (
    compute_sha256, format_size, sanitize_filename,
    build_share_link, parse_telegram_link, ProgressTracker,
)
from .chunk_header import is_chunk_with_header, HEADER_SIZE as CHUNK_HEADER_SIZE
from .crypto import Encryptor, is_encryption_available
from .compression import decompress_data


class DownloadTask:
    """Represents a single download task.

    A DownloadTask is created when the user requests a download. It runs
    in a background thread and can be paused, resumed, or cancelled.
    """

    def __init__(self, task_id, link, output_dir, config, bot_pool,
                 password=None, db=None, api_semaphore=None):
        self.id = task_id
        self.link = link
        self.output_dir = output_dir
        self.config = config
        self.bot_pool = bot_pool
        self.password = password
        self.db = db
        self._api_semaphore = api_semaphore  # limits concurrent API calls

        # State
        self.state = "pending"  # pending, downloading, paused, completed, failed, cancelled
        self.error = None
        self.output_path = None
        self.file_name = None
        self.file_size = 0
        self.downloaded_bytes = 0
        self.total_parts = 0
        self.completed_parts = 0
        self.started_at = None
        self.completed_at = None

        # Control flags
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused
        self._cancel_flag = False
        self._lock = threading.Lock()

        # Manifest data (populated after fetch)
        self._manifest = None
        self._source_chat_id = None
        self._message_ids = []
        self._expected_hash = None
        self._is_encrypted = False
        self._is_compressed = False
        self._has_chunk_header = False
        self._encryptor = None

        # Thread
        self._thread = None

    def start(self):
        """Start the download in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        """Pause the download (can be resumed)."""
        self._pause_event.clear()
        with self._lock:
            if self.state == "downloading":
                self.state = "paused"

    def resume(self):
        """Resume a paused download."""
        self._pause_event.set()
        with self._lock:
            if self.state == "paused":
                self.state = "downloading"

    def cancel(self):
        """Permanently cancel the download."""
        self._cancel_flag = True
        self._pause_event.set()  # unblock if paused
        with self._lock:
            if self.state in ("downloading", "paused", "pending"):
                self.state = "cancelled"

    def _run(self):
        """Main download loop (runs in background thread)."""
        try:
            with self._lock:
                self.state = "downloading"
                self.started_at = time.time()

            # Step 1: Fetch manifest
            if self._cancel_flag:
                return
            self._pause_event.wait()

            if not self._fetch_manifest():
                return

            # Step 2: Download parts
            if self._cancel_flag:
                return
            self._download_parts()

            # Step 3: Verify + finalize
            if self._cancel_flag:
                return
            self._finalize()

        except Exception as e:
            with self._lock:
                self.state = "failed"
                self.error = str(e)
        finally:
            if self.state not in ("completed", "cancelled", "failed"):
                with self._lock:
                    self.state = "failed"
                    self.error = "Download ended unexpectedly"

    def _fetch_manifest(self):
        """Fetch and parse the manifest message."""
        try:
            chat_id, message_id = parse_telegram_link(self.link)
        except ValueError as e:
            with self._lock:
                self.state = "failed"
                self.error = str(e)
            return False

        # Forward manifest to temp channel
        # Use semaphore to limit concurrent API calls across all downloads
        if self._api_semaphore:
            self._api_semaphore.acquire()
        try:
            bot = self.bot_pool.get_next()
            if bot is None:
                with self._lock:
                    self.state = "failed"
                    self.error = "No active bots"
                return False

            fwd_res = bot.request("forwardMessage", data={
                "chat_id": self.config.temp_channel,
                "from_chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": True,
            })
        finally:
            if self._api_semaphore:
                self._api_semaphore.release()

        if not fwd_res or not fwd_res.get("ok"):
            with self._lock:
                self.state = "failed"
                self.error = "Cannot fetch manifest"
            return False

        msg_data = fwd_res["result"]
        fwd_msg_id = msg_data["message_id"]
        # Delete the forwarded copy
        bot.request("deleteMessage", data={
            "chat_id": self.config.temp_channel, "message_id": fwd_msg_id,
        })

        # Parse manifest
        text = msg_data.get("text", "")
        caption = msg_data.get("caption", "")

        import json
        if text.startswith(MANIFEST_PREFIX):
            lines = text.split("\n", 1)
            json_str = lines[1] if len(lines) > 1 else ""
            self._manifest = json.loads(json_str)
        elif caption.startswith(MANIFEST_PREFIX):
            # File manifest — download and parse
            doc = msg_data.get("document")
            if not doc:
                with self._lock:
                    self.state = "failed"
                    self.error = "Manifest file not found"
                return False
            # Re-forward to download the file
            if self._api_semaphore:
                self._api_semaphore.acquire()
            try:
                fwd_res2 = bot.request("forwardMessage", data={
                    "chat_id": self.config.temp_channel,
                    "from_chat_id": chat_id,
                    "message_id": message_id,
                    "disable_notification": True,
                })
            finally:
                if self._api_semaphore:
                    self._api_semaphore.release()
            if fwd_res2 and fwd_res2.get("ok"):
                fwd_msg_id2 = fwd_res2["result"]["message_id"]
                content = self._download_document_content(bot, fwd_res2["result"])
                bot.request("deleteMessage", data={
                    "chat_id": self.config.temp_channel, "message_id": fwd_msg_id2,
                })
                if content:
                    self._manifest = json.loads(content.decode("utf-8"))
                else:
                    with self._lock:
                        self.state = "failed"
                        self.error = "Cannot download manifest file"
                    return False
        else:
            with self._lock:
                self.state = "failed"
                self.error = "Not a manifest message"
            return False

        # Extract manifest fields
        self.file_name = sanitize_filename(self._manifest["name"])
        self.file_size = self._manifest["size"]
        self.total_parts = self._manifest["total_parts"]
        self._message_ids = self._manifest["message_ids"]
        self._expected_hash = self._manifest["sha256"]
        self._source_chat_id = self._manifest["channel_id"]
        self._is_encrypted = self._manifest.get("encrypted", False)
        self._is_compressed = self._manifest.get("compressed", False)
        self._has_chunk_header = self._manifest.get("has_chunk_header", False)

        # Setup encryption
        if self._is_encrypted:
            if not is_encryption_available():
                with self._lock:
                    self.state = "failed"
                    self.error = "Encrypted file — cryptography library not installed"
                return False
            if not self.password:
                import os as _os
                self.password = _os.environ.get("TG_VAULT_PASSWORD")
            if not self.password:
                with self._lock:
                    self.state = "failed"
                    self.error = "Encrypted file — no password provided"
                return False
            stored_hash = self._manifest.get("password_hash")
            if stored_hash and not Encryptor.verify_password_hash(self.password, stored_hash):
                with self._lock:
                    self.state = "failed"
                    self.error = "Wrong password"
                return False
            salt = Encryptor.salt_from_str(self._manifest["encryption_salt"])
            self._encryptor = Encryptor(self.password, salt=salt)

        return True

    def _download_document_content(self, bot, message_dict):
        """Download document content from a forwarded message."""
        doc = message_dict.get("document")
        if not doc:
            return None
        file_id = doc["file_id"]
        file_res = bot.request("getFile", data={"file_id": file_id})
        if not file_res or not file_res.get("ok"):
            return None
        file_path = file_res["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"
        try:
            r = bot.session.get(url, timeout=300)
            if r.status_code != 200:
                return None
            return r.content
        except Exception:
            return None

    def _download_parts(self):
        """Download all parts in parallel."""
        # Create .temp folder inside output_dir
        temp_dir = os.path.join(self.output_dir, ".temp")
        os.makedirs(temp_dir, exist_ok=True)

        out_path = os.path.join(self.output_dir, self.file_name)
        temp_file = os.path.join(temp_dir, self.file_name + ".downloading")

        # Determine start part (resume)
        start_part = 1
        if os.path.exists(temp_file):
            current_size = os.path.getsize(temp_file)
            completed = current_size // self.config.chunk_size
            if 0 < completed < self.total_parts:
                start_part = completed + 1
                with open(temp_file, "r+b") as f:
                    f.truncate(completed * self.config.chunk_size)

        workers = min(self.config.parallel_workers, max(1, len(self.bot_pool)))
        next_to_download = start_part
        next_to_write = start_part
        pending = {}
        write_lock = threading.Lock()
        self.completed_parts = start_part - 1

        mode = "ab" if start_part > 1 else "wb"
        with open(temp_file, mode) as out_file:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                max_in_flight = workers * 2
                futures = {}

                while next_to_write <= self.total_parts or futures:
                    # Check for cancellation
                    if self._cancel_flag:
                        return

                    # Wait if paused
                    self._pause_event.wait()
                    if self._cancel_flag:
                        return

                    # Submit new tasks
                    while (next_to_download <= self.total_parts
                           and len(futures) < max_in_flight):
                        msg_id = self._message_ids[next_to_download - 1]
                        fut = executor.submit(
                            self._download_one_part,
                            self._source_chat_id, msg_id, next_to_download
                        )
                        futures[fut] = next_to_download
                        next_to_download += 1

                    if not futures:
                        break

                    # Process completed futures
                    for fut in as_completed(list(futures.keys())):
                        part_num = futures.pop(fut)
                        _, content = fut.result()
                        if content is None:
                            with self._lock:
                                self.state = "failed"
                                self.error = f"Failed to download part {part_num}"
                            return

                        pending[part_num] = content

                        # Write in order
                        with write_lock:
                            while next_to_write in pending:
                                raw = pending.pop(next_to_write)
                                # Strip header
                                if self._has_chunk_header and is_chunk_with_header(raw):
                                    raw = raw[CHUNK_HEADER_SIZE:]
                                # Decrypt
                                if self._encryptor:
                                    iv = (next_to_write - 1).to_bytes(12, "big")
                                    try:
                                        raw = self._encryptor.decrypt_chunk(raw, iv)
                                    except Exception as e:
                                        with self._lock:
                                            self.state = "failed"
                                            self.error = f"Decryption failed: {e}"
                                        return
                                # Decompress
                                if self._is_compressed:
                                    try:
                                        raw = decompress_data(raw, True)
                                    except Exception:
                                        pass
                                out_file.write(raw)
                                self.downloaded_bytes += len(raw)
                                self.completed_parts += 1
                                next_to_write += 1

                        # Check if we should submit more
                        if next_to_download <= self.total_parts and len(futures) < max_in_flight:
                            break

    def _download_one_part(self, source_chat_id, msg_id, part_num):
        """Download a single part. Returns (part_num, content) or (part_num, None).

        Uses the API semaphore to limit concurrent API calls.
        """
        try:
            # Use semaphore to limit concurrent API calls
            if self._api_semaphore:
                self._api_semaphore.acquire()
            try:
                bot = self.bot_pool.get_next()
                if bot is None:
                    return part_num, None

                # Forward to temp channel
                fwd_res = bot.request("forwardMessage", data={
                    "chat_id": self.config.temp_channel,
                    "from_chat_id": source_chat_id,
                    "message_id": msg_id,
                    "disable_notification": True,
                })
            finally:
                if self._api_semaphore:
                    self._api_semaphore.release()

            if not fwd_res or not fwd_res.get("ok"):
                return part_num, None

            msg_data = fwd_res["result"]
            fwd_msg_id = msg_data["message_id"]
            content = self._download_document_content(bot, msg_data)

            # Delete forwarded copy
            bot.request("deleteMessage", data={
                "chat_id": self.config.temp_channel, "message_id": fwd_msg_id,
            })

            return part_num, content
        except Exception:
            return part_num, None

    def _finalize(self):
        """Verify SHA256 and move file to final destination."""
        temp_dir = os.path.join(self.output_dir, ".temp")
        temp_file = os.path.join(temp_dir, self.file_name + ".downloading")
        out_path = os.path.join(self.output_dir, self.file_name)

        if not os.path.exists(temp_file):
            with self._lock:
                self.state = "failed"
                self.error = "Temp file not found"
            return

        # Verify size
        actual_size = os.path.getsize(temp_file)
        if actual_size != self.file_size:
            with self._lock:
                self.state = "failed"
                self.error = f"Size mismatch: {actual_size} != {self.file_size}"
            return

        # Verify SHA256
        actual_hash = compute_sha256(temp_file)
        if actual_hash != self._expected_hash:
            with self._lock:
                self.state = "failed"
                self.error = "SHA256 mismatch"
            return

        # Move to final destination
        if os.path.exists(out_path):
            os.remove(out_path)
        os.rename(temp_file, out_path)

        self.output_path = out_path
        with self._lock:
            self.state = "completed"
            self.completed_at = time.time()


class DownloadManager:
    """Manages multiple DownloadTask objects.

    Features:
      - Concurrency control: limits total concurrent API calls to avoid
        FloodWait when many downloads run simultaneously.
      - Persistence: saves download state to a JSON file so paused/incomplete
        downloads can be resumed after a GUI restart.
    """

    def __init__(self, max_concurrent=None, state_file=None):
        """Initialize the download manager.

        Args:
            max_concurrent: Maximum number of concurrent API calls across all
                            downloads. If None, defaults to 1 (safe for single bot).
            state_file: Path to a JSON file for persisting download state.
                        If None, persistence is disabled.
        """
        self._tasks = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._max_concurrent = max_concurrent or 1
        self._api_semaphore = threading.Semaphore(self._max_concurrent)
        self._state_file = state_file

    def set_max_concurrent(self, n):
        """Update the concurrency limit. Only affects new API calls."""
        # Can't easily resize a semaphore, so we create a new one.
        # Existing calls in flight will complete; new calls use the new limit.
        self._max_concurrent = max(1, n)
        self._api_semaphore = threading.Semaphore(self._max_concurrent)

    def add_download(self, link, output_dir, config, bot_pool, password=None, db=None):
        """Add a new download task and start it.

        Returns the task ID.
        """
        with self._lock:
            task_id = self._next_id
            self._next_id += 1

        task = DownloadTask(task_id, link, output_dir, config, bot_pool,
                            password, db, self._api_semaphore)
        with self._lock:
            self._tasks[task_id] = task
        task.start()
        self._save_state()
        return task_id

    def get_task(self, task_id):
        """Get a task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def get_all_tasks(self):
        """Get all tasks."""
        with self._lock:
            return list(self._tasks.values())

    def pause(self, task_id):
        """Pause a download."""
        task = self.get_task(task_id)
        if task:
            task.pause()
            self._save_state()

    def resume(self, task_id):
        """Resume a paused download."""
        task = self.get_task(task_id)
        if task:
            task.resume()
            self._save_state()

    def cancel(self, task_id):
        """Cancel a download."""
        task = self.get_task(task_id)
        if task:
            task.cancel()
            self._save_state()

    def remove(self, task_id):
        """Remove a task from the manager (only if completed/cancelled/failed)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.state in ("completed", "cancelled", "failed"):
                del self._tasks[task_id]
                self._save_state()
                return True
            return False

    def clear_completed(self):
        """Remove all completed/cancelled/failed tasks."""
        with self._lock:
            to_remove = [tid for tid, t in self._tasks.items()
                         if t.state in ("completed", "cancelled", "failed")]
            for tid in to_remove:
                del self._tasks[tid]
            if to_remove:
                self._save_state()
            return len(to_remove)

    # ─────────────── Persistence ───────────────

    def _save_state(self):
        """Save download state to the state file (if configured)."""
        if not self._state_file:
            return
        try:
            tasks_data = []
            with self._lock:
                for task in self._tasks.values():
                    # Only persist tasks that are not completed/cancelled
                    if task.state in ("completed", "cancelled"):
                        continue
                    tasks_data.append({
                        "id": task.id,
                        "link": task.link,
                        "output_dir": task.output_dir,
                        "password": task.password,
                        "state": task.state,
                        "file_name": task.file_name,
                        "file_size": task.file_size,
                        "total_parts": task.total_parts,
                        "completed_parts": task.completed_parts,
                        "downloaded_bytes": task.downloaded_bytes,
                    })
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump({"tasks": tasks_data, "next_id": self._next_id}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass  # silent — persistence is best-effort

    def load_state(self, config, bot_pool, db=None):
        """Load download state from the state file.

        Restored downloads start in 'paused' state so the user can resume
        them manually. File name, size, and parts are restored from the
        state file so the UI shows correct info immediately.
        """
        if not self._state_file or not os.path.exists(self._state_file):
            return

        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        tasks_data = data.get("tasks", [])
        self._next_id = data.get("next_id", 1)

        for td in tasks_data:
            task_id = td["id"]
            link = td["link"]
            output_dir = td["output_dir"]
            password = td.get("password")

            # Recreate the task
            task = DownloadTask(task_id, link, output_dir, config, bot_pool,
                                password, db, self._api_semaphore)
            task.state = "paused"  # start paused so user can resume manually
            # Restore saved metadata so UI shows correct info
            task.file_name = td.get("file_name")
            task.file_size = td.get("file_size", 0)
            task.total_parts = td.get("total_parts", 0)
            task.completed_parts = td.get("completed_parts", 0)
            task.downloaded_bytes = td.get("downloaded_bytes", 0)
            with self._lock:
                self._tasks[task_id] = task
