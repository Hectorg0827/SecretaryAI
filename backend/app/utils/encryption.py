"""
AES-256-GCM encryption helpers for storing sensitive values in the database
(OAuth tokens, API keys, QB credentials).

The master key comes from the SECRET_KEY env var.
Each encrypted value gets its own random 96-bit nonce stored alongside the ciphertext.
"""
import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _derive_key(secret: str) -> bytes:
    """Derive a 32-byte AES key from the SECRET_KEY string."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"secretaryai-v2-static-salt",  # static salt is OK for key derivation (not password hashing)
        iterations=100_000,
    )
    return kdf.derive(secret.encode())


def encrypt(plaintext: str, secret_key: str) -> str:
    """
    Encrypt a string value.
    Returns a base64-encoded string: nonce (12 bytes) + ciphertext.
    """
    key = _derive_key(secret_key)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt(encrypted: str, secret_key: str) -> str:
    """
    Decrypt a value produced by encrypt().
    Raises ValueError if decryption fails (tampered or wrong key).
    """
    key = _derive_key(secret_key)
    raw = base64.urlsafe_b64decode(encrypted)
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode()
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")
