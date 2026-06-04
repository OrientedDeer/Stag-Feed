#!/usr/bin/env python3
"""Python reimplementation of Stag's client-side backup crypto envelope.

Stag encrypts every backup blob in the browser (Web Crypto API) before it ever
leaves the device — zero-knowledge: the server only stores ciphertext. To let
stag-feed merge SimpleFIN data directly into the database, we must reproduce
that envelope in Python: decrypt the blob, edit the plaintext, re-encrypt.

This module is the *proof* that the round-trip is reproducible. It mirrors
Stag's src/services/encryption/CryptoService.ts exactly:

    PBKDF2-HMAC-SHA256, 600_000 iterations, 16-byte salt  -> 256-bit key
    AES-256-GCM, 12-byte IV, ciphertext = ciphertext||16-byte-tag (WebCrypto)
    checksum = SHA-256 hex of the UTF-8 plaintext

PBKDF2 and SHA-256 are stdlib (hashlib). AES-GCM is NOT in the Python standard
library, so this module depends on `cryptography` (pip install cryptography).
That dependency is deliberate and ends stag-feed's old "stdlib-only" property —
there is no AES anywhere in the stdlib to fall back on.

Run as a script to prove the round-trip against a real Stag blob WITHOUT ever
printing the decrypted plaintext (it's your full finances):

    python3 stag_crypto.py out/backup.enc 123
"""

import base64
import hashlib
import json
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Must match CryptoService.ts constants exactly, or Stag can't read what we write.
ITERATIONS = 600_000  # OWASP recommendation for PBKDF2-SHA256
SALT_LENGTH = 16      # 128 bits
IV_LENGTH = 12        # 96 bits (recommended for AES-GCM)


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256 -> 32-byte AES-256 key. Mirrors deriveKey()."""
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decrypt(envelope: dict, passphrase: str) -> str:
    """Decrypt a Stag EncryptedBackup envelope. Mirrors CryptoService.decrypt().

    Verifies the checksum after decrypting, like Stag does. Raises ValueError on
    a wrong passphrase / tampered data (GCM auth failure) or checksum mismatch.
    """
    salt = base64.b64decode(envelope["salt"])
    iv = base64.b64decode(envelope["iv"])
    ciphertext = base64.b64decode(envelope["ciphertext"])  # includes GCM tag
    iterations = envelope.get("iterations") or ITERATIONS

    key = _derive_key(passphrase, salt, iterations)
    try:
        # AESGCM expects ciphertext||tag, which is exactly what WebCrypto emits.
        plaintext = AESGCM(key).decrypt(iv, ciphertext, None).decode("utf-8")
    except Exception:
        raise ValueError("Decryption failed. Wrong passphrase or corrupted data.")

    if envelope.get("checksum") and _sha256_hex(plaintext) != envelope["checksum"]:
        raise ValueError("Checksum mismatch. Data may be corrupted.")
    return plaintext


def _js_iso_now() -> str:
    """JS Date.toISOString() format: 2026-06-04T12:34:56.789Z (millisecond, Z)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def encrypt(plaintext: str, passphrase: str) -> dict:
    """Produce a Stag-compatible EncryptedBackup envelope. Mirrors encrypt().

    Fresh random salt + IV each call (so re-encrypting yields a different blob
    that still decrypts to the same plaintext — matching Stag's behavior).
    """
    import os
    salt = os.urandom(SALT_LENGTH)
    iv = os.urandom(IV_LENGTH)
    key = _derive_key(passphrase, salt, ITERATIONS)
    ciphertext = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)  # ct||tag
    return {
        "version": 1,
        "algorithm": "AES-256-GCM",
        "kdf": "PBKDF2",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "timestamp": _js_iso_now(),
        "checksum": _sha256_hex(plaintext),
    }


def _roundtrip_proof(blob_path: str, passphrase: str) -> None:
    """Prove the envelope is reproducible without leaking the plaintext."""
    with open(blob_path) as f:
        envelope = json.load(f)

    print(f"Read envelope: {blob_path}")
    print(f"  algorithm={envelope.get('algorithm')} kdf={envelope.get('kdf')} "
          f"iterations={envelope.get('iterations')}")

    # 1. Python decrypts Stag's own output.
    plaintext = decrypt(envelope, passphrase)
    print(f"\n[1] Decrypt Stag blob ......... OK  ({len(plaintext):,} chars, checksum verified)")
    try:
        top = list(json.loads(plaintext).keys())
        print(f"      backup top-level keys: {top}")
    except json.JSONDecodeError:
        print("      (plaintext is not JSON?)")

    # 2. Re-encrypt that plaintext, then decrypt again -> must be byte-identical.
    re_enc = encrypt(plaintext, passphrase)
    if decrypt(re_enc, passphrase) != plaintext:
        sys.exit("[2] FAILED: re-encrypted blob did not decrypt back to the original.")
    print("[2] Re-encrypt -> decrypt ..... OK  (Python envelope self-consistent)")

    # 3. Envelope shape matches the TS interface (so Stag will accept ours).
    expected = {"version", "algorithm", "kdf", "iterations", "salt", "iv",
                "ciphertext", "timestamp", "checksum"}
    assert set(re_enc) == expected, f"envelope keys differ: {set(re_enc) ^ expected}"
    assert len(base64.b64decode(re_enc["salt"])) == SALT_LENGTH
    assert len(base64.b64decode(re_enc["iv"])) == IV_LENGTH
    assert len(re_enc["checksum"]) == 64
    print("[3] Envelope shape ............ OK  (matches EncryptedBackup interface)")

    print("\nRound-trip proven. Final confirmation: re-import a Python-written\n"
          "blob into Stag itself to verify cross-implementation compatibility.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python3 stag_crypto.py <backup.enc> <passphrase>")
    _roundtrip_proof(sys.argv[1], sys.argv[2])
