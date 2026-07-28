"""
Regression tests for the production-readiness security hardening pass.

Covers:
  - Sentry event scrubbing (#22): headers/cookies/body/local-vars/user PII.
  - /health leak (#20): no raw exception text in the public response.
  - /openapi.json exposure (#21): schema is gated in production mode.
  - Refresh-token hardening (#19): revocation enforced, renewal bounded,
    single-use rotation.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.config import get_settings

settings = get_settings()
SECRET = settings.secret_key


def _make_token(sub="user-1", company_id="co-1", role="owner", jti="jti-1", exp_delta_seconds=3600):
    exp = datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)
    return jose_jwt.encode(
        {"sub": sub, "company_id": company_id, "role": role, "jti": jti, "exp": exp},
        SECRET, algorithm="HS256",
    )


# ─────────────────────────────────────────────────────────────────────────────
# #22 — Sentry scrubbing
# ─────────────────────────────────────────────────────────────────────────────

class TestSentryScrub:
    def _event(self):
        return {
            "request": {
                "headers": {"Authorization": "Bearer secret", "Cookie": "session=abc", "Accept": "application/json"},
                "cookies": {"session_token": "xyz"},
                "data": {"password": "hunter2", "email": "a@b.com"},
            },
            "user": {"id": "u1", "email": "a@b.com", "ip_address": "1.2.3.4", "username": "alice"},
            "exception": {"values": [
                {"stacktrace": {"frames": [
                    {"vars": {"password": "hunter2", "api_key": "k", "safe": "ok"}},
                    {"vars": {"authorization": "Bearer z", "count": 3}},
                ]}},
            ]},
        }

    def test_headers_cookies_body_scrubbed(self):
        from app.main import _scrub_sentry_event
        out = _scrub_sentry_event(self._event())
        h = out["request"]["headers"]
        assert h["Authorization"] == "[redacted]"
        assert h["Cookie"] == "[redacted]"
        assert h["Accept"] == "application/json"          # non-sensitive kept
        assert out["request"]["cookies"]["session_token"] == "[redacted]"
        assert "data" not in out["request"]               # body dropped entirely

    def test_frame_vars_all_frames_scrubbed(self):
        from app.main import _scrub_sentry_event
        out = _scrub_sentry_event(self._event())
        frames = out["exception"]["values"][0]["stacktrace"]["frames"]
        assert frames[0]["vars"]["password"] == "[redacted]"
        assert frames[0]["vars"]["api_key"] == "[redacted]"
        assert frames[0]["vars"]["safe"] == "ok"
        assert frames[1]["vars"]["authorization"] == "[redacted]"   # 2nd frame too
        assert frames[1]["vars"]["count"] == 3

    def test_user_pii_removed(self):
        from app.main import _scrub_sentry_event
        out = _scrub_sentry_event(self._event())
        assert out["user"]["id"] == "u1"
        assert "email" not in out["user"]
        assert "ip_address" not in out["user"]
        assert "username" not in out["user"]


# ─────────────────────────────────────────────────────────────────────────────
# #20 / #21 — public surface
# ─────────────────────────────────────────────────────────────────────────────

class TestPublicSurface:
    def test_health_never_leaks_exception_text(self):
        from app.main import app
        client = TestClient(app)
        r = client.get("/health")
        body = r.json()
        # Redis/DB are unreachable in test → values must be exactly "ok"/"error",
        # never "error: <exception detail>".
        for key in ("redis", "db"):
            assert body["checks"][key] in ("ok", "error")
            assert ":" not in body["checks"][key]

    def test_openapi_gated_in_production(self):
        from app.main import app
        if settings.debug:
            pytest.skip("openapi is intentionally public in debug mode")
        client = TestClient(app)
        r = client.get("/openapi.json")
        assert r.status_code == 401  # not the public schema


# ─────────────────────────────────────────────────────────────────────────────
# #19 — refresh-token hardening
# ─────────────────────────────────────────────────────────────────────────────

class TestRefreshHardening:
    def test_revoked_token_cannot_refresh(self):
        from app.main import app
        client = TestClient(app)
        token = _make_token(jti="revoked-jti")
        with patch("app.auth.jwt.is_token_revoked", return_value=True):
            r = client.post("/auth/refresh", json={"access_token": token})
        assert r.status_code == 401
        assert "revoked" in r.json()["detail"].lower()

    def test_token_expired_beyond_window_cannot_refresh(self):
        from app.main import app
        client = TestClient(app)
        # Expired far beyond refresh_token_expire_days.
        old = (settings.refresh_token_expire_days * 86400) + 3600
        token = _make_token(exp_delta_seconds=-old)
        r = client.post("/auth/refresh", json={"access_token": token})
        assert r.status_code == 401
        assert "log in" in r.json()["detail"].lower()

    def test_successful_refresh_rotates_old_token(self):
        from app.main import app
        client = TestClient(app)
        token = _make_token(jti="rotate-me", exp_delta_seconds=60)

        fake_db = MagicMock()
        fake_db.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=[{"is_active": True}])

        with patch("app.auth.jwt.is_token_revoked", return_value=False), \
             patch("supabase.create_client", return_value=fake_db), \
             patch("app.auth.jwt.blacklist_jti") as spy_blacklist:
            r = client.post("/auth/refresh", json={"access_token": token})

        assert r.status_code == 200, r.text
        assert "access_token" in r.json()
        # Single-use rotation: the presented token's JTI must be blacklisted.
        spy_blacklist.assert_called_once()
        assert spy_blacklist.call_args.args[0] == "rotate-me"
