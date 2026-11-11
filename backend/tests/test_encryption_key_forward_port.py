"""Staging-forward-port regression tests for split encryption/signing keys.

No Supabase, OAuth provider, customer key, or external service is contacted.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils.encryption import encrypt, decrypt


SIGNING_KEY = "test-signing-key-legacy-32-characters-0001"
ENC_KEY = "test-encryption-key-separate-32-chars-0002"
OLD_KEY = "test-encryption-key-previous-32-chars-0003"


def _settings(encryption_key="", encryption_key_previous=""):
    return SimpleNamespace(
        secret_key=SIGNING_KEY,
        encryption_key=encryption_key,
        encryption_key_previous=encryption_key_previous,
    )


def test_existing_ciphertext_decrypts_after_new_key_is_configured():
    with patch("app.config.get_settings", return_value=_settings()):
        legacy_ciphertext = encrypt("old-refresh-token", SIGNING_KEY)

    with patch("app.config.get_settings", return_value=_settings(ENC_KEY)):
        assert decrypt(legacy_ciphertext, SIGNING_KEY) == "old-refresh-token"
        new_ciphertext = encrypt("new-refresh-token", SIGNING_KEY)

    # New data must not be readable with the stolen signing key alone.
    with patch("app.config.get_settings", return_value=_settings()):
        with pytest.raises(ValueError, match="^Decryption failed$"):
            decrypt(new_ciphertext, SIGNING_KEY)


def test_previous_encryption_keys_are_read_only_after_rotation():
    with patch("app.config.get_settings", return_value=_settings(OLD_KEY)):
        previous_ciphertext = encrypt("persisted-credential", SIGNING_KEY)

    with patch("app.config.get_settings", return_value=_settings(ENC_KEY, OLD_KEY)):
        assert decrypt(previous_ciphertext, SIGNING_KEY) == "persisted-credential"
        new_ciphertext = encrypt("fresh-credential", SIGNING_KEY)

    with patch("app.config.get_settings", return_value=_settings(OLD_KEY)):
        with pytest.raises(ValueError, match="^Decryption failed$"):
            decrypt(new_ciphertext, SIGNING_KEY)


def test_explicit_non_application_secret_remains_isolated():
    other_key = "explicit-other-provider-key-32-characters"
    with patch("app.config.get_settings", return_value=_settings(ENC_KEY)):
        ciphertext = encrypt("private", other_key)
        assert decrypt(ciphertext, other_key) == "private"
        with pytest.raises(ValueError, match="^Decryption failed$"):
            decrypt(ciphertext, SIGNING_KEY)


def test_tampered_ciphertext_does_not_leak_crypto_or_key_details():
    with patch("app.config.get_settings", return_value=_settings(ENC_KEY)):
        ciphertext = encrypt("do-not-echo-this-secret", SIGNING_KEY)
        for malformed in ("not-valid!", ciphertext[:-3] + "ABC"):
            with pytest.raises(ValueError) as exc:
                decrypt(malformed, SIGNING_KEY)
            assert str(exc.value) == "Decryption failed"


def test_config_rejects_weak_or_reused_encryption_keys(monkeypatch):
    from app.config import Settings

    common = dict(
        secret_key=SIGNING_KEY,
        supabase_url="https://test.supabase.co",
        supabase_anon_key="anon",
        supabase_service_role_key="service",
        database_url="postgresql://test:test@localhost/test",
        anthropic_api_key="test",
        debug=True,
    )
    with pytest.raises(ValueError, match="ENCRYPTION_KEY must be at least 32"):
        Settings(_env_file=None, **common, encryption_key="short")
    with pytest.raises(ValueError, match="ENCRYPTION_KEY must be different"):
        Settings(_env_file=None, **common, encryption_key=SIGNING_KEY)
    with pytest.raises(ValueError, match="ENCRYPTION_KEY_PREVIOUS requires"):
        Settings(_env_file=None, **common, encryption_key_previous=OLD_KEY)
    assert Settings(_env_file=None, **common, encryption_key=ENC_KEY).encryption_key == ENC_KEY
