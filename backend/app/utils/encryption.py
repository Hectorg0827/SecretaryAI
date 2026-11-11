"""
AES-256-GCM encryption helpers for storing sensitive values in the database
(OAuth tokens, API keys, QB credentials).

The encryption key is separate from SECRET_KEY (which signs login tokens).
For a staged key rotation, decrypt() also accepts previously configured keys
and the original SECRET_KEY, so existing encrypted credentials still work.
Each encrypted value gets its own random 96-bit nonce stored alongside the ciphertext.
"""
import base64
import os
from functools import lru_cache
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@lru_cache(maxsize=16)
def _derive_key(secret: str) -> bytes:
    """Derive a 32-byte AES key from a configured secret."""
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
    """Use the dedicated key for new ciphertext; read legacy ciphertext safely.

    The second argument is historically the application's SECRET_KEY. Preserve
    the meaning of an explicitly supplied different key in callers and tests.
    """
    try:
        from app.config import get_settings

        settings = get_settings()
    except Exception:
        return [secret_key]

    if secret_key != getattr(settings, "secret_key", None):
        return [secret_key]

    candidates = [getattr(settings, "encryption_key", "") or ""]
    previous = getattr(settings, "encryption_key_previous", "") or ""
    candidates.extend(item.strip() for item in previous.split(",") if item.strip())
    candidates.append(secret_key)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def encrypt(plaintext: str, secret_key: str) -> str:
    """
    Encrypt a string value.
    Returns a base64-encoded string: nonce (12 bytes) + ciphertext.
    """
    key = _derive_key(_candidate_secrets(secret_key)[0])
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt(encrypted: str, secret_key: str) -> str:
    """
    Decrypt a value produced by encrypt().
    Raises ValueError if decryption fails (tampered or wrong key).
    """
    try:
        raw = base64.urlsafe_b64decode(encrypted)
    except Exception:
        raise ValueError("Decryption failed") from None
    nonce, ciphertext = raw[:12], raw[12:]
    for candidate in _candidate_secrets(secret_key):
        try:
            plaintext = AESGCM(_derive_key(candidate)).decrypt(nonce, ciphertext, None)
            return plaintext.decode()
        except Exception:
            continue
    # Never leak ciphertext, key material or the cryptography failure details.
    raise ValueError("Decryption failed")
