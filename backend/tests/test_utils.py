"""
Tests for utils module: encryption, rate limiter.
"""
import pytest
import time
from unittest.mock import MagicMock

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


# ─── Encryption Tests ──────────────────────────────────────────────────────────

class TestEncryption:
    SECRET = "my-test-secret-key-for-unit-tests"

    def test_encrypt_returns_string(self):
        from app.utils.encryption import encrypt
        result = encrypt("hello world", self.SECRET)
        assert isinstance(result, str)
        assert result != "hello world"

    def test_decrypt_roundtrip(self):
        from app.utils.encryption import encrypt, decrypt
        plaintext = "sensitive-api-key-12345"
        encrypted = encrypt(plaintext, self.SECRET)
        decrypted = decrypt(encrypted, self.SECRET)
        assert decrypted == plaintext

    def test_different_plaintexts_produce_different_ciphertext(self):
        from app.utils.encryption import encrypt
        e1 = encrypt("value1", self.SECRET)
        e2 = encrypt("value2", self.SECRET)
        assert e1 != e2

    def test_same_plaintext_produces_different_ciphertext_each_time(self):
        # Each call uses a random nonce
        from app.utils.encryption import encrypt
        e1 = encrypt("same", self.SECRET)
        e2 = encrypt("same", self.SECRET)
        assert e1 != e2

    def test_wrong_key_raises_value_error(self):
        from app.utils.encryption import encrypt, decrypt
        encrypted = encrypt("secret data", self.SECRET)
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(encrypted, "wrong-secret-key-for-testing!!")

    def test_tampered_ciphertext_raises_value_error(self):
        from app.utils.encryption import encrypt, decrypt
        encrypted = encrypt("secret data", self.SECRET)
        tampered = encrypted[:-4] + "XXXX"
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(tampered, self.SECRET)

    def test_encrypt_empty_string(self):
        from app.utils.encryption import encrypt, decrypt
        encrypted = encrypt("", self.SECRET)
        decrypted = decrypt(encrypted, self.SECRET)
        assert decrypted == ""

    def test_encrypt_unicode(self):
        from app.utils.encryption import encrypt, decrypt
        plaintext = "Héllo Wörld — 日本語テスト"
        encrypted = encrypt(plaintext, self.SECRET)
        decrypted = decrypt(encrypted, self.SECRET)
        assert decrypted == plaintext

    def test_encrypt_long_value(self):
        from app.utils.encryption import encrypt, decrypt
        plaintext = "x" * 10_000
        encrypted = encrypt(plaintext, self.SECRET)
        decrypted = decrypt(encrypted, self.SECRET)
        assert decrypted == plaintext


# ─── Rate Limiter Tests ────────────────────────────────────────────────────────

class TestRedisRateLimiter:
    def test_allows_requests_within_limit(self):
        from app.utils.rate_limiter import RedisRateLimiter
        limiter = RedisRateLimiter(max_calls=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_allowed("user:1") is True

    def test_blocks_after_limit_exceeded(self):
        from app.utils.rate_limiter import RedisRateLimiter
        limiter = RedisRateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            limiter.is_allowed("user:2")
        assert limiter.is_allowed("user:2") is False

    def test_different_keys_are_independent(self):
        from app.utils.rate_limiter import RedisRateLimiter
        limiter = RedisRateLimiter(max_calls=1, window_seconds=60)
        assert limiter.is_allowed("user:a") is True
        assert limiter.is_allowed("user:a") is False
        assert limiter.is_allowed("user:b") is True  # Independent key

    def test_remaining_decrements(self):
        from app.utils.rate_limiter import RedisRateLimiter
        limiter = RedisRateLimiter(max_calls=5, window_seconds=60)
        assert limiter.remaining("user:1") == 5
        limiter.is_allowed("user:1")
        assert limiter.remaining("user:1") == 4
        limiter.is_allowed("user:1")
        assert limiter.remaining("user:1") == 3

    def test_remaining_never_negative(self):
        from app.utils.rate_limiter import RedisRateLimiter
        limiter = RedisRateLimiter(max_calls=2, window_seconds=60)
        for _ in range(5):
            limiter.is_allowed("user:1")
        assert limiter.remaining("user:1") == 0

    def test_window_expiry_resets_allowance(self):
        from app.utils.rate_limiter import RedisRateLimiter
        limiter = RedisRateLimiter(max_calls=1, window_seconds=1)
        assert limiter.is_allowed("user:1") is True
        assert limiter.is_allowed("user:1") is False
        time.sleep(1.1)
        assert limiter.is_allowed("user:1") is True

    def test_pre_configured_limiters_exist(self):
        from app.utils.rate_limiter import chat_limiter, computer_use_limiter, api_limiter, login_limiter
        assert chat_limiter.max_calls == 30
        assert computer_use_limiter.max_calls == 5
        assert api_limiter.max_calls == 120
        assert login_limiter.max_calls == 5


class TestGetClientKey:
    def test_combines_user_id_and_ip(self):
        from app.utils.rate_limiter import get_client_key
        request = MagicMock()
        request.state.user_id = "user-123"
        request.client.host = "192.168.1.1"
        key = get_client_key(request)
        assert key == "user-123:192.168.1.1"

    def test_no_user_id_uses_empty_string(self):
        from app.utils.rate_limiter import get_client_key
        request = MagicMock()
        del request.state.user_id  # Simulate missing attribute
        request.state = MagicMock(spec=[])  # No user_id
        request.client.host = "10.0.0.1"
        key = get_client_key(request)
        assert ":10.0.0.1" in key

    def test_no_client_uses_unknown(self):
        from app.utils.rate_limiter import get_client_key
        request = MagicMock()
        request.state.user_id = "u1"
        request.client = None
        key = get_client_key(request)
        assert key == "u1:unknown"


class TestRequireRateLimit:
    def test_allows_within_limit(self):
        from app.utils.rate_limiter import RedisRateLimiter, require_rate_limit
        limiter = RedisRateLimiter(max_calls=10, window_seconds=60)
        dep_fn = require_rate_limit(limiter)
        request = MagicMock()
        request.state.user_id = "user-1"
        request.client.host = "127.0.0.1"
        # Should not raise
        dep_fn(request)

    def test_raises_429_when_limit_exceeded(self):
        from fastapi import HTTPException
        from app.utils.rate_limiter import RedisRateLimiter, require_rate_limit
        limiter = RedisRateLimiter(max_calls=1, window_seconds=60)
        dep_fn = require_rate_limit(limiter)
        request = MagicMock()
        request.state.user_id = "user-limited"
        request.client.host = "127.0.0.1"
        dep_fn(request)  # First call OK
        with pytest.raises(HTTPException) as exc_info:
            dep_fn(request)
        assert exc_info.value.status_code == 429
