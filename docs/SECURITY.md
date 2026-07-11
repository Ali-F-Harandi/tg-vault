# Security

This document describes tg-vault's encryption design, threat model, and security best practices.

## Encryption overview

tg-vault uses **AES-256-GCM** authenticated encryption with **PBKDF2-HMAC-SHA512** key derivation (600,000 iterations, per OWASP 2025 recommendations).

### Key derivation

```
password + 32-byte random salt → PBKDF2-HMAC-SHA512(600,000 iters) → 256-bit AES key
```

- The salt is **unique per file** (random 32 bytes generated at encryption time)
- The same password produces **different keys** for different files
- 600,000 iterations takes ~1 second on a modern CPU — intentionally slow to make brute-force expensive

### Per-chunk encryption

Each chunk is encrypted with AES-256-GCM using:
- The file-derived key (same for all chunks in a file)
- A **deterministic IV** derived from the chunk index: `iv = (chunk_index).to_bytes(12, "big")`

This is safe because:
- The key is unique per file (random salt)
- The IV is unique within a file (chunk index)
- The (key, IV) pair is never reused

Per NIST SP 800-38D, AES-GCM requires that (key, IV) be unique for each encryption operation. tg-vault satisfies this because each file has a unique key (random salt) and each chunk within a file has a unique IV (chunk index).

### Authentication

AES-GCM provides both **confidentiality** and **integrity**:
- A 128-bit authentication tag is appended to each chunk's ciphertext
- On decryption, the tag is verified automatically
- If a chunk has been tampered with, decryption fails with `InvalidTag`

### What is stored where

**On Telegram (encrypted):**
- Chunk ciphertexts (in the `document` of each part message)
- Manifest with: `encrypted=true`, `encryption_salt`, `encryption_algorithm`, `encryption_kdf`, `password_hash`, `sha256_prefix_b64`

**On Telegram (plaintext):**
- File name (in manifest and description message)
- File size, total parts, chunk size
- Description text and hashtags
- SHA256 of the **original** file (before encryption)
- Channel IDs and message IDs
- Session ID

**Never stored anywhere:**
- The encryption key itself
- The password (only a verification hash)

### Password verification hash

To fail-fast on wrong passwords (instead of attempting decryption and getting a generic `InvalidTag`), tg-vault stores a separate password verification hash:

```
password + domain-separated salt → PBKDF2-HMAC-SHA512(600,000 iters) → 32-byte hash
```

The domain separation salt (`b"tg-vault-password-verify-v1"`) is fixed and different from the encryption salt, so the verification hash cannot be used to derive the encryption key.

Verification uses `hmac.compare_digest()` (constant-time comparison) to prevent timing side-channel attacks.

## Threat model

### What encryption protects against

✅ **Telegram sees your file contents** — chunks are encrypted, Telegram only sees ciphertext
✅ **Channel members with read access** — they can't decrypt without the password
✅ **Telegram staff or government requests** — they have ciphertext only
✅ **Chunk tampering** — AES-GCM auth tag detects any modification
✅ **Wrong-password attempts** — fail-fast via verification hash

### What encryption does NOT protect against

❌ **File metadata exposure** — file name, size, description, hashtags, SHA256 of original file are all in plaintext in the manifest
❌ **Existence of files** — Telegram can see when and how many encrypted files you upload
❌ **Traffic analysis** — Telegram can see upload/download patterns
❌ **Password brute-force** — if you use a weak password, an attacker who gets the manifest can try to brute-force it (600k PBKDF2 iterations make this slow, but not impossible)
❌ **Key compromise** — if your password is leaked, all files encrypted with that password are compromised
❌ **Loss of password** — if you forget the password, the files are unrecoverable (zero-knowledge design)

### Security best practices

1. **Use a strong password** — at least 16 characters, mixed case + digits + symbols. Use a password manager.
2. **Use a unique password** — don't reuse passwords from other services
3. **Don't lose the password** — there is no recovery mechanism
4. **Use `TG_VAULT_PASSWORD` env var** — avoids the password being in shell history
5. **Be aware of metadata leakage** — the file name is in plaintext. If the name is sensitive, rename the file before uploading (or use a generic name).
6. **Keep your config file secure** — `~/.tg-vault.json` contains bot tokens. Set permissions to `600` (`chmod 600 ~/.tg-vault.json`).
7. **Use a private channel** — public channels are visible to everyone

### File permissions

```bash
chmod 600 ~/.tg-vault.json
chmod 600 ~/.tg-vault.db
```

## Encryption command examples

### Encrypt on upload

```bash
# Will prompt for password
python tg.py upload secret.txt --encrypt

# Or via env var (recommended for scripts)
export TG_VAULT_PASSWORD="my-strong-password"
python tg.py upload secret.txt --encrypt

# Or via CLI flag (NOT recommended — visible in shell history)
python tg.py upload secret.txt --encrypt --password "my-strong-password"
```

### Decrypt on download

```bash
# Will prompt for password
python tg.py download https://t.me/c/.../42

# Or via env var
export TG_VAULT_PASSWORD="my-strong-password"
python tg.py download https://t.me/c/.../42

# Or via CLI flag
python tg.py download https://t.me/c/.../42 --password "my-strong-password"
```

### Verify encryption is working

After an encrypted upload, the manifest will contain:

```json
{
  "encrypted": true,
  "encryption_salt": "QGGU8SGov4vvWpxv6L38fY4rQsjUDlfCHwqHA/PjtJs=",
  "encryption_algorithm": "aes-256-gcm",
  "encryption_kdf": "pbkdf2-sha512-600k",
  "password_hash": "eb694dc860d4052b0d06f28394d1358a14b139f3ec46fba5123dd35af4313e4c",
  "sha256_prefix_b64": "C7P2JgWGZ21nLaekPdmPmg=="
}
```

Check with `python tg.py info <link>` — you should see these fields in the manifest.

## Backward compatibility

- v7 manifests (without `encrypted` field) still download correctly
- v7 config files work as-is (`db_enabled` defaults to `false` if not set)
- v7 database schemas auto-upgrade (new columns added with defaults)
- Old downloads without TGV1 header still work (header detection is opt-in)

## Cryptographic libraries

tg-vault uses the [`cryptography`](https://cryptography.io/) library (Python's standard crypto library):

- `cryptography.hazmat.primitives.ciphers.aead.AESGCM` — AES-256-GCM
- `cryptography.hazmat.primitives.kdf.pbkdf2.PBKDF2HMAC` — PBKDF2 key derivation
- `cryptography.hazmat.primitives.hashes.SHA512` — SHA-512 for PBKDF2

Install with:

```bash
pip install cryptography
```

## References

- [NIST SP 800-38D](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38d.pdf) — GCM mode recommendation
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) — PBKDF2 iteration count recommendations
- [RFC 5116](https://tools.ietf.org/html/rfc5116) — Authenticated Encryption with GCM
- [cryptography.io documentation](https://cryptography.io/en/latest/hazmat/primitives/aead/)
