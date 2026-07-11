"""
Encryption module for tg-vault — AES-256-GCM with PBKDF2 key derivation.

Inspired by TAS (https://github.com/ixchio/tas) but adapted for Python.

Features:
  - AES-256-GCM authenticated encryption
  - PBKDF2-HMAC-SHA512 with 600,000 iterations (OWASP 2025 recommendation)
  - Per-chunk random IV (96-bit, NIST-recommended for GCM)
  - 128-bit auth tag (stored at end of encrypted data, like TLS)
  - Password verification hash (separate from encryption key)
  - Timing-safe comparison to prevent side-channel attacks

The encryption key is NEVER stored. The user must provide the password
for both encryption and decryption. The manifest stores:
  - encryption flag (boolean)
  - salt (random, 32 bytes, base64-encoded in JSON)
  - password verification hash (so we can fail-fast on wrong password)
  - IV per chunk (random, 12 bytes, base64-encoded in JSON)

Security notes:
  - PBKDF2 with 600k iterations takes ~1 second on a modern CPU — this is
    intentional, to make brute-force attacks expensive.
  - GCM mode provides both confidentiality and integrity.
  - The auth tag is verified automatically during decryption; if a chunk
    has been tampered with, decryption fails with InvalidTag.
"""

import base64
import hashlib
import hmac
import os
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Constants
ALGORITHM = "aes-256-gcm"
KEY_LENGTH = 32           # 256 bits
IV_LENGTH = 12            # 96 bits (NIST-recommended for GCM)
TAG_LENGTH = 16           # 128 bits (built into AESGCM.encrypt() output)
SALT_LENGTH = 32          # 256 bits
PBKDF2_ITERATIONS = 600_000  # OWASP 2025 recommendation for SHA-512

# Domain separation for password verification hash
# (different from encryption key derivation, to prevent cross-use)
VERIFY_SALT_DOMAIN = b"tg-vault-password-verify-v1"


class Encryptor:
    """AES-256-GCM encryptor with PBKDF2 key derivation."""

    def __init__(self, password: str, salt: bytes = None):
        """
        Initialize with a password. If salt is None, a new random salt is generated.
        The same salt MUST be used for both encryption and decryption.
        """
        self.password = password.encode("utf-8") if isinstance(password, str) else password
        self.salt = salt if salt is not None else secrets.token_bytes(SALT_LENGTH)
        self._key = self._derive_key(self.password, self.salt)
        self._aesgcm = AESGCM(self._key)

    @staticmethod
    def _derive_key(password: bytes, salt: bytes) -> bytes:
        """Derive a 256-bit key from password + salt using PBKDF2-HMAC-SHA512."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA512(),
            length=KEY_LENGTH,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Get a hash of the password for verification (NOT the encryption key).

        Uses a fixed domain-separated salt so the same password always
        produces the same hash. This lets us fail-fast on wrong passwords
        before attempting decryption.
        """
        password_bytes = password.encode("utf-8") if isinstance(password, str) else password
        h = hashlib.pbkdf2_hmac(
            "sha512",
            password_bytes,
            VERIFY_SALT_DOMAIN,
            PBKDF2_ITERATIONS,
            dklen=32,
        )
        return h.hex()

    @staticmethod
    def verify_password_hash(password: str, stored_hash: str) -> bool:
        """Verify password against stored hash using timing-safe comparison."""
        computed = Encryptor.get_password_hash(password)
        # hmac.compare_digest is constant-time
        return hmac.compare_digest(computed.encode(), stored_hash.encode())

    def encrypt_chunk(self, plaintext: bytes) -> tuple:
        """Encrypt a single chunk with a random IV.

        Returns (ciphertext_with_tag, iv) — both bytes.
        The IV is needed for decryption and must be stored alongside.
        """
        iv = secrets.token_bytes(IV_LENGTH)
        # AESGCM.encrypt returns ciphertext + tag concatenated
        ciphertext = self._aesgcm.encrypt(iv, plaintext, associated_data=None)
        return ciphertext, iv

    def encrypt_chunk_with_iv(self, plaintext: bytes, iv: bytes) -> bytes:
        """Encrypt a single chunk with a caller-provided IV.

        This is used when IVs are deterministic (e.g., derived from chunk index)
        to avoid storing per-chunk IVs in the manifest.

        WARNING: The caller MUST ensure (key, IV) is never reused.
        """
        if len(iv) != IV_LENGTH:
            raise ValueError(f"IV must be {IV_LENGTH} bytes, got {len(iv)}")
        return self._aesgcm.encrypt(iv, plaintext, associated_data=None)

    def decrypt_chunk(self, ciphertext: bytes, iv: bytes) -> bytes:
        """Decrypt a single chunk.

        Raises cryptography.exceptions.InvalidTag if the chunk has been
        tampered with or the password is wrong.
        """
        return self._aesgcm.decrypt(iv, ciphertext, associated_data=None)

    def encrypt_file(self, input_path: str, output_path: str, chunk_size: int = 1024 * 1024) -> list:
        """Encrypt an entire file. Returns list of IVs (one per chunk).

        The output file is the concatenation of:
          [chunk1_ciphertext+tag][chunk2_ciphertext+tag]...

        Each chunk's IV is returned separately so it can be stored in the manifest.
        """
        ivs = []
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break
                ciphertext, iv = self.encrypt_chunk(chunk)
                fout.write(ciphertext)
                ivs.append(iv)
        return ivs

    def decrypt_file(self, input_path: str, output_path: str, ivs: list, chunk_size: int = 1024 * 1024) -> None:
        """Decrypt an entire file given the list of IVs.

        Each chunk is (chunk_size + TAG_LENGTH) bytes in the encrypted file.
        The caller must provide the IVs in the correct order.
        """
        # Each encrypted chunk = plaintext_length + TAG_LENGTH bytes
        # But the LAST chunk may be shorter. So we read chunks of (chunk_size + TAG_LENGTH)
        # and the last read will return whatever's left.
        encrypted_chunk_size = chunk_size + TAG_LENGTH
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            for iv in ivs:
                encrypted_chunk = fin.read(encrypted_chunk_size)
                if not encrypted_chunk:
                    raise ValueError(f"Encrypted file ended before all IVs were consumed. {len(ivs) - ivs.index(iv)} IVs remaining.")
                plaintext = self.decrypt_chunk(encrypted_chunk, iv)
                fout.write(plaintext)

    # ─────────────── Serialization helpers ───────────────

    @staticmethod
    def salt_to_str(salt: bytes) -> str:
        """Encode salt as base64 string for JSON storage."""
        return base64.b64encode(salt).decode("ascii")

    @staticmethod
    def salt_from_str(s: str) -> bytes:
        """Decode salt from base64 string."""
        return base64.b64decode(s.encode("ascii"))

    @staticmethod
    def iv_to_str(iv: bytes) -> str:
        return base64.b64encode(iv).decode("ascii")

    @staticmethod
    def iv_from_str(s: str) -> bytes:
        return base64.b64decode(s.encode("ascii"))

    @staticmethod
    def ivs_to_str_list(ivs: list) -> list:
        return [Encryptor.iv_to_str(iv) for iv in ivs]

    @staticmethod
    def ivs_from_str_list(strs: list) -> list:
        return [Encryptor.iv_from_str(s) for s in strs]


def is_encryption_available() -> bool:
    """Check if the cryptography library is installed."""
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False
