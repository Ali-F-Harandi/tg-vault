"""
Uploader — uploads a file as a chain of reply-linked chunks + manifest.

v8 features (inspired by TAS):
  - Optional AES-256-GCM encryption (zero-knowledge)
  - Optional smart compression (skips already-compressed formats)
  - Self-describing chunk header (TGV1 magic)
"""

import io
import json
import math
import os
import time
import uuid

from .constants import (
    VERSION,
    MANIFEST_PREFIX,
    DESCRIPTION_PREFIX,
    TG_FILENAME_MAX,
)
from .utils import (
    compute_sha256,
    format_size,
    sanitize_filename,
    truncate_caption,
    truncate_text,
    ProgressTracker,
)
from .chunk_header import (
    create_header as chunk_create_header,
    FLAG_COMPRESSED,
    FLAG_ENCRYPTED,
)
from .crypto import Encryptor, is_encryption_available
from .compression import compress_file, compress_data, should_skip_compression


class Uploader:
    """Upload a file as a chain of reply-linked chunks + manifest."""

    def __init__(self, config, bot_pool, db=None):
        self.config = config
        self.bot_pool = bot_pool
        self.db = db  # optional Database instance
        self.session_id = uuid.uuid4().hex[:8]
        self._interrupted = False

    def upload(self, file_path, description="", hashtags=None, resume=False,
               encrypt=False, password=None, compress=True, manifest_type="auto"):
        """Upload file with optional description + hashtags.

        Args:
            encrypt: If True, encrypt chunks with AES-256-GCM using ``password``.
            password: Password for encryption (required if encrypt=True).
            compress: If True, gzip-compress chunks (skips already-compressed formats).
            manifest_type: 'text' (force text manifest, editable), 'file' (force file
                manifest, not editable), 'auto' (text if fits, file if too large).
                Default 'auto'.

        Returns a dict with keys: share_link, manifest (full manifest dict).
        """
        if not os.path.exists(file_path):
            print(f"Error: file not found: {file_path}")
            return None

        file_size = os.path.getsize(file_path)
        if file_size > 2000 * 1024 * 1024:
            print(f"Error: file too large ({format_size(file_size)}). "
                  "Telegram Bot API max is 2 GB even with Local Bot API Server.")
            return None

        file_name = os.path.basename(file_path)
        file_name = sanitize_filename(file_name)
        hashtags = hashtags or []
        resume_path = f"{file_name}.resume.json"

        # Encryption setup
        encryptor = None
        encryption_salt = None
        password_hash = None
        if encrypt:
            if not is_encryption_available():
                print("❌ Encryption requires 'cryptography' library. Install with: pip install cryptography")
                return None
            if not password:
                print("❌ Encryption requires a password. Use --password or set TG_VAULT_PASSWORD env var.")
                return None
            encryptor = Encryptor(password)
            encryption_salt = encryptor.salt
            password_hash = encryptor.get_password_hash(password)
            print(f"🔐 Encryption: ENABLED (AES-256-GCM, PBKDF2 600k iterations)")
            print(f"   Salt: {encryptor.salt_to_str(encryption_salt)[:24]}...")
            print(f"   Password hash: {password_hash[:16]}...")

        # Compression setup
        will_compress = compress and compress_file is not None and not should_skip_compression(file_name)
        any_chunk_compressed = False  # track if ANY chunk was actually compressed
        if compress and not will_compress and compress_file is not None:
            print(f"📦 Compression: SKIPPED (already-compressed format: {file_name})")
        elif will_compress:
            print(f"📦 Compression: ENABLED (gzip level 6)")

        total_parts = max(1, math.ceil(file_size / self.config.chunk_size))
        print(f"\n📦 File: {file_name}")
        print(f"   Size: {format_size(file_size)}")
        print(f"   Parts: {total_parts}")
        print(f"   Session: {self.session_id}")

        # SHA256 (computed on ORIGINAL file, before any processing)
        print("\n🔍 Computing SHA256 of original file...")
        file_hash = compute_sha256(file_path)
        print(f"✅ SHA256: {file_hash}")

        # First 16 bytes of SHA256 for chunk headers
        sha256_prefix = bytes.fromhex(file_hash)[:16]

        # Resume state
        message_ids = []
        prev_msg_id = None
        desc_msg_id = None
        start_part = 1

        if resume and os.path.exists(resume_path):
            try:
                with open(resume_path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if (old.get("sha256") == file_hash
                        and old.get("name") == file_name):
                    message_ids = old.get("message_ids", [])
                    desc_msg_id = old.get("description_msg_id")
                    if len(message_ids) >= total_parts:
                        print("\n✅ All parts already uploaded. Sending manifest only.")
                        share_link, manifest_dict = self._send_manifest(
                            file_name, file_size, total_parts,
                            message_ids, file_hash, desc_msg_id,
                            description, hashtags,
                            encrypt=bool(encryptor),
                            encryption_salt=encryption_salt,
                            password_hash=password_hash,
                            compress=any_chunk_compressed,
                            sha256_prefix=sha256_prefix,
                            manifest_type=manifest_type,
                        )
                        if os.path.exists(resume_path):
                            os.remove(resume_path)
                        if share_link:
                            self._print_summary(file_name, file_size, total_parts,
                                                file_hash, message_ids, desc_msg_id,
                                                share_link)
                            if self.db and manifest_dict:
                                self._log_to_db(manifest_dict, share_link)
                                print(f"💾 Database updated: {self.config.get_db_path()}")
                                if self.config.db_auto_sync:
                                    # Local import to avoid circular dependency
                                    from .db_sync import auto_sync_db
                                    auto_sync_db(self.config, self.bot_pool)
                                    print(f"☁️  Database synced to Telegram channel")
                            return {"share_link": share_link, "manifest": manifest_dict}
                    elif len(message_ids) > 0:
                        start_part = len(message_ids) + 1
                        prev_msg_id = message_ids[-1]
                        print(f"\n▶️ Resuming from part {start_part} "
                              f"({len(message_ids)} parts already uploaded)")
                else:
                    print("\n⚠️ Hash or name mismatch. Starting fresh.")
            except Exception as e:
                print(f"\n⚠️ Failed to read resume state ({e}). Starting fresh.")

        # Send description (if starting fresh)
        if start_part == 1:
            desc_msg_id = self._send_description(
                file_name, file_size, total_parts, file_hash,
                description, hashtags
            )
            if desc_msg_id is None:
                print("Error: failed to send description message.")
                return None
            prev_msg_id = desc_msg_id

        # Upload chunks
        progress = ProgressTracker(total_parts, prefix="Upload  ")
        try:
            with open(file_path, "rb") as f:
                f.seek((start_part - 1) * self.config.chunk_size)
                for part_num in range(start_part, total_parts + 1):
                    if self._interrupted:
                        raise KeyboardInterrupt
                    raw_chunk = f.read(self.config.chunk_size)

                    # Pipeline: raw → compress (optional) → encrypt (optional) → prepend header
                    processed = raw_chunk
                    chunk_actually_compressed = False

                    if will_compress and compress_file is not None:
                        processed, chunk_actually_compressed = compress_data(processed, file_name)
                        if chunk_actually_compressed:
                            any_chunk_compressed = True

                    iv = None
                    if encryptor:
                        iv = (part_num - 1).to_bytes(12, "big")
                        processed = encryptor.encrypt_chunk_with_iv(processed, iv)

                    # Prepend self-describing header (TGV1)
                    flags = 0
                    if chunk_actually_compressed:
                        flags |= FLAG_COMPRESSED
                    if encryptor:
                        flags |= FLAG_ENCRYPTED
                    if chunk_create_header is not None:
                        header = chunk_create_header(
                            chunk_index=part_num - 1,
                            total_chunks=total_parts,
                            original_size=file_size,
                            sha256_prefix=sha256_prefix,
                            flags=flags,
                        )
                        processed = header + processed

                    # For single-part files, use the original filename (no .part suffix)
                    # For multi-part files, use .partNNNNofNNNN suffix
                    if total_parts == 1:
                        part_name = sanitize_filename(file_name)
                    else:
                        part_name = sanitize_filename(
                            f"{file_name}.part{part_num:04d}of{total_parts:04d}"
                        )

                    bot = self.bot_pool.get_next()
                    files = {"document": (part_name, processed)}
                    caption = truncate_caption(
                        f"📦 part {part_num}/{total_parts} | {file_name} | "
                        f"#{self.session_id}"
                    )
                    data = {
                        "chat_id": self.config.main_channel,
                        "caption": caption,
                    }
                    if prev_msg_id is not None:
                        data["reply_to_message_id"] = prev_msg_id

                    res = bot.request("sendDocument", data=data, files=files)

                    if res and res.get("ok"):
                        msg_id = res["result"]["message_id"]
                        message_ids.append(msg_id)
                        prev_msg_id = msg_id
                        self._save_resume(resume_path, file_name, file_hash,
                                          message_ids, desc_msg_id)
                        progress.update(1)
                    else:
                        err = res.get("description") if res else "No response"
                        print(f"\nError uploading part {part_num}: {err}")
                        print(f"\nTo resume: python tg.py upload \"{file_path}\" --resume")
                        return None

                    time.sleep(self.config.upload_delay)

        except KeyboardInterrupt:
            self._interrupted = True
            print("\n\n⚠️ Interrupted! Resume state saved.")
            print(f"To resume: python tg.py upload \"{file_path}\" --resume")
            return None

        # Send manifest
        print("\n📋 Sending manifest...")
        share_link, manifest_dict = self._send_manifest(
            file_name, file_size, total_parts,
            message_ids, file_hash, desc_msg_id,
            description, hashtags,
            encrypt=bool(encryptor),
            encryption_salt=encryption_salt,
            password_hash=password_hash,
            compress=any_chunk_compressed,
            sha256_prefix=sha256_prefix,
            manifest_type=manifest_type,
        )

        # Clean up resume state
        if os.path.exists(resume_path):
            os.remove(resume_path)

        if share_link:
            self._print_summary(file_name, file_size, total_parts,
                                file_hash, message_ids, desc_msg_id, share_link)
            # Log to database if enabled
            if self.db and manifest_dict:
                self._log_to_db(manifest_dict, share_link)
                print(f"💾 Database updated: {self.config.get_db_path()}")
                # Auto-sync DB to Telegram channel
                if self.config.db_auto_sync:
                    from .db_sync import auto_sync_db
                    auto_sync_db(self.config, self.bot_pool)
                    print(f"☁️  Database synced to Telegram channel")
            return {"share_link": share_link, "manifest": manifest_dict}
        return None

    def _send_description(self, file_name, file_size, total_parts,
                          file_hash, description, hashtags):
        """Send the description message (before any parts)."""
        lines = [
            f"📦 File: {file_name}",
            f"💾 Size: {format_size(file_size)}",
            f"🔢 Parts: {total_parts}",
            f"🔐 SHA256: {file_hash}",
            f"🆔 Session: {self.session_id}",
        ]
        if description:
            lines.append("")
            lines.append("📝 Description:")
            lines.append(description)
        if hashtags:
            lines.append("")
            tag_str = " ".join(f"#{t.lstrip('#')}" for t in hashtags)
            lines.append(tag_str)

        text = truncate_text("\n".join(lines))
        bot = self.bot_pool.get_next()
        res = bot.request("sendMessage", data={
            "chat_id": self.config.main_channel,
            "text": text,
            "disable_web_page_preview": True,
        })
        if res and res.get("ok"):
            return res["result"]["message_id"]
        return None

    def _send_manifest(self, file_name, file_size, total_parts,
                       message_ids, file_hash, desc_msg_id,
                       description, hashtags,
                       encrypt=False, encryption_salt=None, password_hash=None,
                       compress=False, sha256_prefix=None, manifest_type="auto"):
        """Send the manifest as a TEXT message or FILE.

        Args:
            manifest_type: 'text' (force text), 'file' (force file), 'auto'
                (text if fits, file if > 4090 chars). Default 'auto'.

        Text manifests are better because:
          1. Can be edited later with editMessageText (update description/tags)
          2. Faster (no file upload needed)
          3. Smaller (no file overhead)

        Format:
          Line 1: TG_VAULT_MANIFEST|name|parts|sha256_prefix
          Line 2+: JSON content (compact, single line)

        Returns (share_link, manifest_dict) or (None, None).
        """
        manifest = {
            "name": file_name,
            "size": file_size,
            "total_parts": total_parts,
            "chunk_size": self.config.chunk_size,
            "message_ids": message_ids,
            "sha256": file_hash,
            "channel_id": self.config.main_channel,
            "description_msg_id": desc_msg_id,
            "description": description,
            "hashtags": hashtags,
            "session_id": self.session_id,
            "version": VERSION,
            "created_at": time.time(),
            "encrypted": encrypt,
            "compressed": compress,
            "has_chunk_header": chunk_create_header is not None,
            "manifest_type": "text",  # identifies this as a text manifest
        }
        if encrypt and encryption_salt is not None:
            manifest["encryption_salt"] = Encryptor.salt_to_str(encryption_salt)
            manifest["encryption_algorithm"] = "aes-256-gcm"
            manifest["encryption_kdf"] = "pbkdf2-sha512-600k"
            manifest["password_hash"] = password_hash
        if sha256_prefix is not None:
            import base64
            manifest["sha256_prefix_b64"] = base64.b64encode(sha256_prefix).decode("ascii")

        # Build text message: header line + compact JSON
        # Compact JSON: no indent, minimal separators → saves ~60% space
        # Example: {"name":"file.zip","size":12345} instead of pretty-printed
        header = f"{MANIFEST_PREFIX}|{file_name}|{total_parts}|{file_hash[:16]}"
        json_str = json.dumps(manifest, ensure_ascii=False, separators=(',', ':'))
        text = header + "\n" + json_str

        # Decide: text vs file manifest
        # - manifest_type='text': always text (will fail if > 4096 chars)
        # - manifest_type='file': always file
        # - manifest_type='auto': text if fits, file if > 4090 chars
        use_file = False
        if manifest_type == "file":
            use_file = True
        elif manifest_type == "auto" and len(text) > 4090:
            use_file = True
        # else: manifest_type == "text" or "auto" with small text → text

        if use_file:
            # Send as file manifest
            json_bytes = json_str.encode("utf-8")
            manifest_filename = sanitize_filename(f"{file_name}.manifest.json")
            bot = self.bot_pool.get_next()
            files = {"document": (manifest_filename, io.BytesIO(json_bytes))}
            caption = truncate_caption(header)
            data = {
                "chat_id": self.config.main_channel,
                "caption": caption,
            }
            if message_ids:
                data["reply_to_message_id"] = message_ids[-1]
            res = bot.request("sendDocument", data=data, files=files)
            manifest["manifest_type"] = "file"
        else:
            # Send as text message (preferred — editable)
            bot = self.bot_pool.get_next()
            data = {
                "chat_id": self.config.main_channel,
                "text": text,
                "disable_web_page_preview": True,
            }
            if message_ids:
                data["reply_to_message_id"] = message_ids[-1]
            res = bot.request("sendMessage", data=data)
            manifest["manifest_type"] = "text"

        if not res or not res.get("ok"):
            err = res.get("description") if res else "No response"
            print(f"Error sending manifest: {err}")
            return None, None

        manifest_msg_id = res["result"]["message_id"]
        manifest["manifest_message_id"] = manifest_msg_id
        share_link = build_share_link(self.config.main_channel, manifest_msg_id)
        return share_link, manifest

    def _save_resume(self, path, file_name, file_hash, message_ids, desc_msg_id):
        data = {
            "name": file_name,
            "sha256": file_hash,
            "message_ids": message_ids,
            "description_msg_id": desc_msg_id,
            "session_id": self.session_id,
            "saved_at": time.time(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _print_summary(self, file_name, file_size, total_parts,
                       file_hash, message_ids, desc_msg_id, share_link):
        print("\n" + "=" * 60)
        print("✅ Upload complete!")
        print(f"   File: {file_name}")
        print(f"   Size: {format_size(file_size)}")
        print(f"   Parts: {total_parts}")
        print(f"   SHA256: {file_hash}")
        print(f"   Description msg: {desc_msg_id}")
        print(f"   First part: {message_ids[0] if message_ids else '?'}")
        print(f"   Last part: {message_ids[-1] if message_ids else '?'}")
        print(f"\n🔗 ★ Download link:")
        print(f"   {share_link}")
        print("=" * 60)

    def _log_to_db(self, manifest, share_link):
        """Insert a record into the database. Silent on errors.

        If the file already exists (same SHA256), update ALL message-related
        fields (message_ids, manifest_msg_id, description_msg_id) — not just
        share_link. This prevents the orphan scanner from seeing the new
        upload's messages as orphans and deleting them.
        """
        if not self.db:
            return
        try:
            # Check if file already exists (by SHA256)
            existing = self.db.get_file_by_sha(manifest["sha256"])
            if existing:
                # CRITICAL: update ALL message-related fields, not just share_link
                self.db.update_share_link(existing["id"], share_link, manifest=manifest)
                return existing["id"]
            return self.db.insert_file(manifest, share_link,
                                        temp_channel=self.config.temp_channel)
        except Exception as e:
            print(f"⚠️ Database log failed: {e}")
            return None


# Local import to avoid circular dependency at module load time
from .utils import build_share_link
