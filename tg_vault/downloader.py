"""
Downloader — downloads a file from its manifest link, with parallel chunks.

Strategy: download N parts in parallel using ``ThreadPoolExecutor``, write
in order to the output file. Uses ``forwardMessage`` to a temp channel
(because ``copyMessage`` does NOT return the caption for channel messages,
a Telegram quirk).
"""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from .constants import (
    MANIFEST_PREFIX,
    TG_FILE_SIZE_LIMIT,
)
from .utils import (
    compute_sha256,
    format_size,
    sanitize_filename,
    build_share_link,
    parse_telegram_link,
    ProgressTracker,
)
from .chunk_header import (
    is_chunk_with_header,
    HEADER_SIZE as CHUNK_HEADER_SIZE,
)
from .crypto import Encryptor, is_encryption_available
from .compression import decompress_file, decompress_data


class Downloader:
    """Download a file from its manifest link, with parallel chunks."""

    def __init__(self, config, bot_pool, db=None):
        self.config = config
        self.bot_pool = bot_pool
        self.db = db  # optional Database instance
        self.session_id = uuid.uuid4().hex[:8]
        self._temp_msg_ids = []  # (chat_id, msg_id) tuples
        self._temp_lock = threading.Lock()
        self._interrupted = False

    def download(self, link, resume=False, output=None, output_dir=".", password=None):
        """Download file from manifest link.

        Args:
            password: Required if manifest indicates encryption.
        """
        print(f"\n🌐 Link: {link}")
        print(f"🆔 Download session: {self.session_id}")

        try:
            chat_id, message_id = parse_telegram_link(link)
        except ValueError as e:
            print(f"Error: {e}")
            return False
        print(f"🔗 Parsed: chat_id={chat_id}, message_id={message_id}")

        # Fetch manifest
        manifest = self._fetch_manifest(chat_id, message_id)
        if not manifest:
            return False

        return self._download_from_manifest(manifest, resume, output, output_dir, password=password)

    def info(self, link):
        """Show manifest info without downloading."""
        print(f"\n🌐 Link: {link}")
        try:
            chat_id, message_id = parse_telegram_link(link)
        except ValueError as e:
            print(f"Error: {e}")
            return False
        manifest = self._fetch_manifest(chat_id, message_id)
        if not manifest:
            return False

        print("\n" + "=" * 60)
        print("📋 File info:")
        print(f"   Name: {manifest['name']}")
        print(f"   Size: {format_size(manifest['size'])}")
        print(f"   Parts: {manifest['total_parts']}")
        print(f"   SHA256: {manifest['sha256']}")
        print(f"   Channel: {manifest.get('channel_id', '?')}")
        if manifest.get("description"):
            print(f"   Description: {manifest['description']}")
        if manifest.get("hashtags"):
            print(f"   Hashtags: {', '.join(manifest['hashtags'])}")
        print(f"   Created: {time.ctime(manifest.get('created_at', 0))}")
        print(f"   Version: v{manifest.get('version', '?')}")
        print("=" * 60)
        return True

    def _fetch_manifest(self, chat_id, message_id):
        """Fetch and parse the manifest message.

        Supports both:
        1. Text manifest (new): message text starts with TG_VAULT_MANIFEST
        2. File manifest (old): message has a document with caption starting with TG_VAULT_MANIFEST
        """
        print("📡 Fetching manifest...")
        copied = self._fetch_message(chat_id, message_id)
        if not copied:
            return None

        # Check if it's a text manifest (new style)
        text = copied.get("text", "")
        if text.startswith(MANIFEST_PREFIX):
            # Text manifest — parse directly from message text
            lines = text.split("\n", 1)
            header = lines[0]
            json_str = lines[1] if len(lines) > 1 else ""

            try:
                parts = header.split("|")
                file_name = parts[1]
                total_parts = int(parts[2])
                print(f"✅ Manifest found: '{file_name}' with {total_parts} parts (text)")
            except (IndexError, ValueError):
                print("Error: malformed manifest header.")
                self._cleanup()
                return None

            self._cleanup()  # Delete the forwarded copy

            try:
                return json.loads(json_str)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"Error parsing manifest JSON: {e}")
                return None

        # Check if it's a file manifest (old style)
        caption = copied.get("caption", "")
        if not caption.startswith(MANIFEST_PREFIX):
            print(f"Error: not a manifest message.")
            self._cleanup()
            return None

        try:
            parts = caption.split("|")
            file_name = parts[1]
            total_parts = int(parts[2])
            print(f"✅ Manifest found: '{file_name}' with {total_parts} parts (file)")
        except (IndexError, ValueError):
            print("Error: malformed manifest caption.")
            self._cleanup()
            return None

        content = self._download_document(copied)
        self._cleanup()

        if not content:
            return None

        try:
            return json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Error parsing manifest JSON: {e}")
            return None

    def _fetch_message(self, source_chat_id, message_id):
        """
        Forward a message from source channel to temp channel.
        Note: we use forwardMessage (not copyMessage) because copyMessage
        does not return caption for channel messages (Telegram quirk).
        """
        bot = self.bot_pool.get_next()
        res = bot.request("forwardMessage", data={
            "chat_id": self.config.temp_channel,
            "from_chat_id": source_chat_id,
            "message_id": message_id,
            "disable_notification": True,
        })
        if not res or not res.get("ok"):
            err = res.get("description") if res else "No response"
            print(f"\n  Error in forwardMessage: {err}")
            if "not enough rights" in str(err).lower():
                print("  ⚠️ Bot lacks permissions (admin required).")
            return None
        temp_msg_id = res["result"]["message_id"]
        with self._temp_lock:
            self._temp_msg_ids.append((self.config.temp_channel, temp_msg_id))
        return res["result"]

    def _download_document(self, message_dict):
        """Download document content from a forwarded message (in temp channel)."""
        doc = message_dict.get("document")
        if not doc:
            return None
        file_id = doc["file_id"]
        file_size = doc.get("file_size", 0)

        if file_size > TG_FILE_SIZE_LIMIT:
            print(f"\n  Error: file {format_size(file_size)} > 20MB. "
                  "Bot cannot download via getFile.")
            return None

        bot = self.bot_pool.get_next()
        file_res = bot.request("getFile", data={"file_id": file_id})
        if not file_res or not file_res.get("ok"):
            return None

        file_path = file_res["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"

        try:
            r = bot.session.get(url, timeout=300)
            if r.status_code != 200:
                print(f"\n  HTTP {r.status_code}")
                return None
            return r.content
        except requests.exceptions.RequestException as e:
            print(f"\n  Download error: {e}")
            return None

    def _cleanup(self):
        """Delete all forwarded temp messages. Robust — retries failed deletes."""
        with self._temp_lock:
            msgs = list(self._temp_msg_ids)
            self._temp_msg_ids.clear()
        if not msgs:
            return
        failed = []
        for chat_id, msg_id in msgs:
            bot = self.bot_pool.get_next()
            res = bot.request("deleteMessage", data={
                "chat_id": chat_id,
                "message_id": msg_id,
            })
            if not res or not res.get("ok"):
                failed.append((chat_id, msg_id))
        # Re-add failed deletions for next cleanup attempt
        if failed:
            with self._temp_lock:
                self._temp_msg_ids.extend(failed)

    def _download_part(self, source_chat_id, msg_id, part_num):
        """Download a single part (worker function for parallel download)."""
        try:
            copied = self._fetch_message(source_chat_id, msg_id)
            if not copied:
                return part_num, None
            content = self._download_document(copied)
            # Cleanup this specific forward
            # (already appended to _temp_msg_ids; cleaned up periodically)
            return part_num, content
        except Exception as e:
            print(f"\n  Error in part {part_num}: {e}")
            return part_num, None

    def _download_from_manifest(self, manifest, resume=False, output=None, output_dir=".",
                                  password=None):
        """Download all parts in parallel and assemble.

        Args:
            password: Required if manifest indicates encryption.
        """
        import json  # local import to avoid module-level import

        file_name = manifest["name"]
        expected_size = manifest["size"]
        total_parts = manifest["total_parts"]
        message_ids = manifest["message_ids"]
        expected_hash = manifest["sha256"]
        source_chat_id = manifest["channel_id"]
        is_encrypted = manifest.get("encrypted", False)
        is_compressed = manifest.get("compressed", False)
        has_chunk_header = manifest.get("has_chunk_header", False)

        if len(message_ids) != total_parts:
            print(f"Error: manifest inconsistent: {len(message_ids)} ids for {total_parts} parts")
            return False

        # Encryption setup
        encryptor = None
        if is_encrypted:
            if not is_encryption_available():
                print("❌ This file is encrypted. Install cryptography: pip install cryptography")
                return False
            if not password:
                # Try env var
                password = os.environ.get("TG_VAULT_PASSWORD")
                if not password:
                    import getpass
                    print("🔐 This file is encrypted with AES-256-GCM.")
                    password = getpass.getpass("Enter password: ")
            # Verify password
            stored_hash = manifest.get("password_hash")
            if stored_hash and not Encryptor.verify_password_hash(password, stored_hash):
                print("❌ Wrong password (verification hash mismatch).")
                return False
            salt = Encryptor.salt_from_str(manifest["encryption_salt"])
            encryptor = Encryptor(password, salt=salt)
            print(f"🔐 Decryption: ENABLED (AES-256-GCM)")

        if is_compressed:
            print(f"📦 Decompression: ENABLED (gzip)")
        if has_chunk_header:
            print(f"🏷️  Self-describing chunks: ENABLED (TGV1 header)")

        # Determine output path
        if output:
            out_path = os.path.join(output_dir, output) if not os.path.isabs(output) else output
        else:
            out_path = os.path.join(output_dir, sanitize_filename(file_name))

        # Auto-create output directory if it doesn't exist
        out_dir_abs = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(out_dir_abs, exist_ok=True)

        temp_file = out_path + ".downloading"

        print(f"\n📥 Downloading: {file_name}")
        print(f"   Size: {format_size(expected_size)}")
        print(f"   Parts: {total_parts}")
        print(f"   SHA256: {expected_hash}")
        print(f"   Output: {out_path}")
        print(f"   Parallel workers: {min(self.config.parallel_workers, len(self.bot_pool))}\n")

        start_part = 1
        if resume and os.path.exists(temp_file):
            current_size = os.path.getsize(temp_file)
            completed = current_size // self.config.chunk_size
            if 0 < completed < total_parts:
                start_part = completed + 1
                with open(temp_file, "r+b") as f:
                    f.truncate(completed * self.config.chunk_size)
                print(f"▶️ Resuming from part {start_part} ({completed} parts done)\n")
            elif completed >= total_parts:
                print("🔍 File looks complete. Verifying...\n")
                start_part = total_parts + 1

        try:
            if start_part <= total_parts:
                # Strategy: download N parts in parallel, write in order
                workers = min(self.config.parallel_workers, max(1, len(self.bot_pool)))
                progress = ProgressTracker(total_parts - start_part + 1, prefix="Download")

                # Use a queue: download parts in batches of `workers`
                # to keep memory bounded
                next_to_download = start_part
                next_to_write = start_part
                pending = {}  # part_num -> content (out of order)
                write_lock = threading.Lock()

                mode = "ab" if start_part > 1 else "wb"
                with open(temp_file, mode) as out_file:
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        # Submit initial batch
                        in_flight = 0
                        max_in_flight = workers * 2  # buffer ahead

                        futures = {}
                        while next_to_write <= total_parts or futures:
                            # Submit new tasks up to max_in_flight
                            while (next_to_download <= total_parts
                                   and in_flight < max_in_flight):
                                msg_id = message_ids[next_to_download - 1]
                                fut = executor.submit(
                                    self._download_part,
                                    source_chat_id,
                                    msg_id,
                                    next_to_download
                                )
                                futures[fut] = next_to_download
                                next_to_download += 1
                                in_flight += 1

                            if not futures:
                                break

                            # Use as_completed properly
                            for fut in as_completed(list(futures.keys())):
                                part_num = futures.pop(fut)
                                in_flight -= 1
                                _, content = fut.result()
                                if content is None:
                                    print(f"\nError downloading part {part_num}")
                                    self._cleanup()
                                    return False
                                pending[part_num] = content

                                # Cleanup temp messages periodically
                                if len(pending) >= workers:
                                    self._cleanup()

                                # Write any parts that are ready in order
                                with write_lock:
                                    while next_to_write in pending:
                                        raw = pending.pop(next_to_write)
                                        # Strip header if present
                                        if has_chunk_header and is_chunk_with_header(raw):
                                            raw = raw[CHUNK_HEADER_SIZE:]
                                        # Decrypt if needed
                                        if encryptor:
                                            # AESGCM ciphertext includes tag at end
                                            # We need to know the IV. For simplicity in v8,
                                            # we derive IV deterministically from chunk index
                                            # (counter mode). For true random IV, would need
                                            # to store IVs in manifest.
                                            iv = (next_to_write - 1).to_bytes(12, "big")
                                            try:
                                                raw = encryptor.decrypt_chunk(raw, iv)
                                            except Exception as e:
                                                print(f"\n❌ Decryption failed for part {next_to_write}: {e}")
                                                return False
                                        # Decompress if needed
                                        if is_compressed and decompress_file is not None:
                                            try:
                                                raw = decompress_data(raw, True)
                                            except Exception:
                                                # Fallback: data wasn't actually compressed
                                                # (bug in older versions set compressed=True
                                                # even when gzip didn't help)
                                                pass
                                        out_file.write(raw)
                                        next_to_write += 1
                                        progress.update(1)

                                # Check if we should submit more
                                if next_to_download <= total_parts and in_flight < max_in_flight:
                                    break

                        # Final cleanup
                        self._cleanup()

        except KeyboardInterrupt:
            self._interrupted = True
            self._cleanup()
            print("\n\n⚠️ Interrupted! Partial download saved.")
            print(f"To resume: python tg.py download \"{manifest.get('share_link', '')}\" --resume")
            return False

        # Verify size
        actual_size = os.path.getsize(temp_file)
        if actual_size != expected_size:
            print(f"\n⚠️ Size mismatch: {actual_size} bytes (expected {expected_size})")

        # Verify SHA256
        print("\n🔍 Verifying SHA256...")
        actual_hash = compute_sha256(temp_file)

        if actual_hash == expected_hash:
            print("✅ SHA256 verified!")
            if os.path.exists(out_path):
                os.remove(out_path)
            os.rename(temp_file, out_path)
            print(f"\n✅ File saved: {out_path}")
            # Final cleanup with retries — make sure ALL temp messages are deleted
            for attempt in range(3):
                self._cleanup()
                if not self._temp_msg_ids:
                    break
                time.sleep(1)
            # Log download to database
            if self.db:
                try:
                    existing = self.db.get_file_by_sha(expected_hash)
                    if existing:
                        self.db.log_download(existing["id"], out_path, True)
                    else:
                        # File not in DB — insert with what we know from manifest
                        file_id = self.db.insert_file(manifest, "", temp_channel=manifest.get("channel_id"))
                        self.db.log_download(file_id, out_path, True)
                except Exception as e:
                    print(f"⚠️ Database log failed: {e}")
            return True
        else:
            print(f"❌ SHA256 mismatch!")
            print(f"   Expected: {expected_hash}")
            print(f"   Got:      {actual_hash}")
            print(f"   Partial file kept: {temp_file}")
            return False


# Local imports to avoid circular dependency at module load time
import json
import requests
