"""
AES-256-GCM encryption helpers for storing sensitive values in the database
(OAuth tokens, 2FA secrets).

Keys
----
Stored secrets are encrypted with ENCRYPTION_KEY — a key used for nothing else.
Previously the single SECRET_KEY both signed login tokens and encrypted every
customer's QuickBooks/Gmail tokens, so one leaked value gave an attacker both
"log in as anyone" and "read every stored token".

Migration is automatic and needs no downtime:
  * encrypt() always uses ENCRYPTION_KEY when it is set;
  * decrypt() tries ENCRYPTION_KEY first, then any ENCRYPTION_KEY_PREVIOUS
    keys, then SECRET_KEY — so values written before the key was introduced
    (or before a rotation) still open, and are re-written with the new key the
    next time they are saved (tokens refresh at least hourly).
If ENCRYPTION_KEY is not set, behaviour is unchanged (SECRET_KEY is used).

Each encrypted value gets its own random 96-bit nonce stored with the ciphertext.
"""
import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@lru_cache(maxsize=16)
def _derive_key(secret: str) -> bytes:
    """Derive a 32-byte AES key from a secret string (cached: PBKDF2 is slow by design)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"secretaryai-v2-static-salt",  # static salt is OK for key derivation (not password hashing)
        iterations=100_000,
    )
    return kdf.derive(secret.encode())


def _candidate_secrets(secret_key: str) -> list[str]:
    """
    Secrets to use, in order. The first one encrypts; all are tried to decrypt.

    Callers pass the app's SECRET_KEY (the historical signature). When they do,
    and a dedicated ENCRYPTION_KEY is configured, that key takes over. A caller
    passing any other explicit key gets exactly that key.
    """
    try:
        from app.config import get_settings
        settings = get_settings()
    except Exception:
        return [secret_key]

    if secret_key != getattr(settings, "secret_key", None):
        return [secret_key]

    out: list[str] = []
    primary = getattr(settings, "encryption_key", "") or ""
    if primary:
        out.append(primary)
    previous = getattr(settings, "encryption_key_previous", "") or ""
    out.extend(k.strip() for k in previous.split(",") if k.strip())
    out.append(secret_key)
    # de-duplicate, keep order
    seen: set[str] = set()
    return [k for k in out if not (k in seen or seen.add(k))]


def encrypt(plaintext: str, secret_key: str) -> str:
    """
    Encrypt a string value.
    Returns a base64-encoded string: nonce (12 bytes) + ciphertext.
    """
    key = _derive_key(_candidate_secrets(secret_key)[0])
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt(encrypted: str, secret_key: str) -> str:
    """
    Decrypt a value produced by encrypt().
    Raises ValueError if decryption fails (tampered or wrong key).
    """
    try:
        raw = base64.urlsafe_b64decode(encrypted)
    except Exception:
        raise ValueError("Decryption failed: value is not valid encrypted data")
    nonce, ciphertext = raw[:12], raw[12:]
    for secret in _candidate_secrets(secret_key):
        try:
            return AESGCM(_derive_key(secret)).decrypt(nonce, ciphertext, None).decode()
        except Exception:
            continue
    # Deliberately no detail: the reason is always "wrong key or tampered".
    raise ValueError("Decryption failed")
