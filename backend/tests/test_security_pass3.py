"""
Regression tests for the end-to-end audit pass (security + reliability).

Each class maps to a finding that was fixed in this pass:
  TestTokenScope            — any HS256 token signed with SECRET_KEY was a full
                              user session (2FA pre-auth / connector tokens)
  TestRefreshHardening      — non-access tokens refreshable; stale role re-signed
  TestMiddlewareOrder       — rate limiter ran before tenant context existed
  TestDispatchTaskGate      — any role could enqueue QB write tasks; free-form task_type
  TestTaskResultTenantScope — connector could overwrite another tenant's task rows
  TestChatHistoryScope      — conversation history loaded by client-supplied id only
  TestQboWebhookSignature   — verifier read from a missing Settings field; hex vs base64
  TestSettingsRobustness    — unknown .env key crashed startup; verifier field exists
  TestDocsGating            — /api/docs/* served schema + ReDoc with no key
  TestDocsKeyCompare        — constant-time key comparison
  TestLockedTask            — idempotency lock released before the work ran
  TestLoginEmailThrottle    — brute force only limited per (proxy) IP
  TestTenantMiddleware      — decoded via undeclared PyJWT dependency
"""
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import base64
import hashlib
import hmac
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app.config import get_settings
from app.main import app

settings = get_settings()
SECRET = settings.secret_key


def _raw_token(**claims) -> str:
    """Sign an arbitrary payload with the app secret (bypasses create_access_token)."""
    payload = {"exp": datetime.now(timezone.utc) + timedelta(hours=1), "jti": str(uuid.uuid4())}
    payload.update(claims)
    return jose_jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Token scope
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenScope:
    def test_create_access_token_defaults_to_access_scope(self):
        from app.auth.jwt import create_access_token, decode_access_token
        tok = create_access_token({"sub": "u1", "company_id": "c1", "role": "owner"})
        assert decode_access_token(tok)["scope"] == "access"

    def test_explicit_scope_is_preserved(self):
        from app.auth.jwt import create_access_token, decode_access_token
        tok = create_access_token({"sub": "u1", "scope": "2fa_pending"})
        assert decode_access_token(tok)["scope"] == "2fa_pending"

    def test_2fa_pending_token_is_not_a_user_session(self):
        from app.auth.rbac import get_current_user
        tok = _raw_token(sub="victim", scope="2fa_pending")
        with pytest.raises(HTTPException) as exc:
            get_current_user(token=tok)
        assert exc.value.status_code == 401

    def test_connector_token_is_not_a_user_session(self):
        from app.auth.rbac import get_current_user
        tok = _raw_token(scope="connector", registration_id="reg-1", company_id="c1")
        with pytest.raises(HTTPException) as exc:
            get_current_user(token=tok)
        assert exc.value.status_code == 401

    def test_access_token_accepted(self):
        from app.auth.rbac import get_current_user
        tok = _raw_token(sub="u1", company_id="c1", role="owner", scope="access")
        assert get_current_user(token=tok)["sub"] == "u1"

    def test_legacy_token_without_scope_still_accepted(self):
        """Sessions issued before the scope claim existed must keep working."""
        from app.auth.rbac import get_current_user
        tok = _raw_token(sub="u1", company_id="c1", role="owner")
        assert get_current_user(token=tok)["sub"] == "u1"

    def test_2fa_setup_rejects_pre_auth_token_end_to_end(self):
        """The concrete bypass: pre-auth token → /auth/2fa/setup must be 401."""
        client = TestClient(app)
        tok = _raw_token(sub="victim", scope="2fa_pending")
        r = client.post("/auth/2fa/setup", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Refresh hardening
# ─────────────────────────────────────────────────────────────────────────────

class TestRefreshHardening:
    def test_non_access_scope_not_refreshable(self):
        client = TestClient(app)
        tok = _raw_token(sub="u1", company_id="c1", role="owner", scope="2fa_pending")
        r = client.post("/auth/refresh", json={"access_token": tok})
        assert r.status_code == 401
        assert "refreshable" in r.json()["detail"].lower()

    def test_refresh_reloads_role_and_company_from_db(self):
        """A demoted user must not keep the old role for the refresh window."""
        client = TestClient(app)
        tok = _raw_token(sub="u1", company_id="c-old", role="owner", scope="access")

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"is_active": True, "role": "viewer", "company_id": "c-new"}]
        )
        with patch("supabase.create_client", return_value=db):
            r = client.post("/auth/refresh", json={"access_token": tok})
        assert r.status_code == 200
        new = jose_jwt.decode(r.json()["access_token"], SECRET, algorithms=["HS256"])
        assert new["role"] == "viewer"
        assert new["company_id"] == "c-new"
        assert new["scope"] == "access"


# ─────────────────────────────────────────────────────────────────────────────
# Middleware order
# ─────────────────────────────────────────────────────────────────────────────

class TestMiddlewareOrder:
    def _index(self, cls):
        names = [m.cls.__name__ for m in app.user_middleware]  # index 0 = outermost
        assert cls in names, f"{cls} not registered; stack={names}"
        return names.index(cls)

    def test_tenant_context_is_set_before_rate_limiting(self):
        # Outer (smaller index) runs first on the way in.
        assert self._index("TenantMiddleware") < self._index("RateLimitMiddleware")

    def test_timeout_middleware_registered(self):
        self._index("TimeoutMiddleware")

    def test_security_headers_and_request_id_still_present(self):
        self._index("SecurityHeadersMiddleware")
        self._index("RequestIDMiddleware")


# ─────────────────────────────────────────────────────────────────────────────
# dispatch_task role + task_type gate
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatchTaskGate:
    def _db_with_connector(self):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value \
            .in_.return_value.limit.return_value.execute.return_value.data = [
                {"id": "reg-1", "connector_type": "qb_desktop", "status": "connected"}
            ]
        db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return db

    @pytest.mark.parametrize("role", ["viewer", "sales_rep", "back_office"])
    @pytest.mark.asyncio
    async def test_non_admin_roles_cannot_dispatch(self, role):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        with pytest.raises(HTTPException) as exc:
            await dispatch_task(
                body=DispatchTaskRequest(task_type="create_invoice"),
                user={"sub": "u1", "role": role, "company_id": "c1"},
                db=self._db_with_connector(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_task_type_rejected(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        with pytest.raises(HTTPException) as exc:
            await dispatch_task(
                body=DispatchTaskRequest(task_type="rm -rf"),
                user={"sub": "u1", "role": "owner", "company_id": "c1"},
                db=self._db_with_connector(),
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_every_protocol_task_type_is_accepted_for_owner(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        from app.domain.connector_protocol import TaskType
        for t in TaskType:
            out = await dispatch_task(
                body=DispatchTaskRequest(task_type=t.value),
                user={"sub": "u1", "role": "owner", "company_id": "c1"},
                db=self._db_with_connector(),
            )
            assert out["status"] == "pending"

    @pytest.mark.asyncio
    async def test_manager_can_dispatch(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        out = await dispatch_task(
            body=DispatchTaskRequest(task_type="fetch_customers"),
            user={"sub": "u1", "role": "manager", "company_id": "c1"},
            db=self._db_with_connector(),
        )
        assert out["task_id"]


# ─────────────────────────────────────────────────────────────────────────────
# task-result tenant scope
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskResultTenantScope:
    def _result(self, company_id="co-A"):
        from app.domain.connector_protocol import TaskResult, TaskStatus
        return TaskResult(
            task_id="task-1", registration_id="reg-1", company_id=company_id,
            correlation_id="corr-1", status=TaskStatus.COMPLETED, data={"rows": []},
        )

    def _db(self, updated_rows):
        db = MagicMock()
        upd = db.table.return_value.update.return_value
        upd.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=updated_rows)
        return db

    @pytest.mark.asyncio
    async def test_update_is_scoped_to_connectors_company(self):
        from app.api.connectors import submit_task_result
        db = self._db([{"id": "task-1"}])
        await submit_task_result(
            result=self._result(), connector={"id": "reg-1", "company_id": "co-A"}, db=db,
        )
        # The same mock chain also receives the follow-up connector_registrations
        # update (last_sync_at), so check membership rather than the last call.
        upd = db.table.return_value.update.return_value
        upd.eq.assert_any_call("id", "task-1")
        upd.eq.return_value.eq.assert_any_call("company_id", "co-A")

    @pytest.mark.asyncio
    async def test_other_tenants_task_is_404(self):
        from app.api.connectors import submit_task_result
        db = self._db([])  # scoped update matched nothing → row belongs to someone else
        with pytest.raises(HTTPException) as exc:
            await submit_task_result(
                result=self._result(), connector={"id": "reg-1", "company_id": "co-A"}, db=db,
            )
        assert exc.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# chat history tenant scope
# ─────────────────────────────────────────────────────────────────────────────

class TestChatHistoryScope:
    def test_load_history_filters_by_company(self):
        from app.api.chat import _load_history
        db = MagicMock()
        _load_history(db, "conv-123", "co-1")
        sel = db.table.return_value.select.return_value
        sel.eq.assert_called_with("conversation_id", "conv-123")
        sel.eq.return_value.eq.assert_called_with("company_id", "co-1")


# ─────────────────────────────────────────────────────────────────────────────
# QBO webhook signature
# ─────────────────────────────────────────────────────────────────────────────

class TestQboWebhookSignature:
    def _sig(self, body: bytes, verifier: str) -> str:
        return base64.b64encode(hmac.new(verifier.encode(), body, hashlib.sha256).digest()).decode()

    def test_valid_base64_signature_accepted(self):
        from app.api import webhooks
        body = b'{"eventNotifications":[]}'
        with patch.object(webhooks.settings, "intuit_webhook_verifier_token", "verifier-1"):
            assert webhooks._verify_qbo_signature(body, self._sig(body, "verifier-1")) is True

    def test_hex_signature_rejected(self):
        from app.api import webhooks
        body = b"{}"
        hexsig = hmac.new(b"verifier-1", body, hashlib.sha256).hexdigest()
        with patch.object(webhooks.settings, "intuit_webhook_verifier_token", "verifier-1"):
            assert webhooks._verify_qbo_signature(body, hexsig) is False

    def test_wrong_verifier_rejected(self):
        from app.api import webhooks
        body = b"{}"
        with patch.object(webhooks.settings, "intuit_webhook_verifier_token", "verifier-1"):
            assert webhooks._verify_qbo_signature(body, self._sig(body, "other")) is False

    def test_missing_verifier_fails_closed(self):
        from app.api import webhooks
        body = b"{}"
        with patch.object(webhooks.settings, "intuit_webhook_verifier_token", ""):
            assert webhooks._verify_qbo_signature(body, self._sig(body, "")) is False


# ─────────────────────────────────────────────────────────────────────────────
# Settings robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestSettingsRobustness:
    def test_unknown_env_key_does_not_crash_startup(self):
        from app.config import Settings
        s = Settings(_env_file=None, some_operator_typo="x")  # must not raise
        assert s.secret_key

    def test_webhook_verifier_field_exists(self):
        from app.config import Settings
        s = Settings(_env_file=None, intuit_webhook_verifier_token="tok")
        assert s.intuit_webhook_verifier_token == "tok"


# ─────────────────────────────────────────────────────────────────────────────
# Docs gating
# ─────────────────────────────────────────────────────────────────────────────

class TestDocsGating:
    def test_api_docs_openapi_requires_key(self):
        client = TestClient(app)
        assert client.get("/api/docs/openapi.json").status_code == 401

    def test_api_docs_redoc_requires_key(self):
        client = TestClient(app)
        assert client.get("/api/docs/").status_code == 401

    def test_api_docs_openapi_with_key(self):
        from app import main
        client = TestClient(app)
        with patch.object(main.settings, "docs_api_key", "the-key"):
            r = client.get("/api/docs/openapi.json", headers={"X-Docs-Key": "the-key"})
        assert r.status_code == 200
        assert "paths" in r.json()

    def test_wrong_key_rejected(self):
        from app import main
        client = TestClient(app)
        with patch.object(main.settings, "docs_api_key", "the-key"):
            r = client.get("/api/docs/openapi.json", headers={"X-Docs-Key": "nope"})
        assert r.status_code == 401


class TestDocsKeyCompare:
    def test_uses_constant_time_compare(self):
        from app import main
        req = MagicMock()
        req.headers = {"x-docs-key": "abc"}
        req.query_params = {}
        with patch.object(main.settings, "docs_api_key", "abc"), \
             patch("hmac.compare_digest", wraps=hmac.compare_digest) as cd:
            assert main._docs_key_ok(req) is True
            assert cd.called

    def test_empty_configured_key_never_matches(self):
        from app import main
        req = MagicMock()
        req.headers = {"x-docs-key": ""}
        req.query_params = {}
        with patch.object(main.settings, "docs_api_key", ""):
            assert main._docs_key_ok(req) is False


# ─────────────────────────────────────────────────────────────────────────────
# locked_task — the lock must be held for the WHOLE run
# ─────────────────────────────────────────────────────────────────────────────

_LOCK_STATE = {"held": False, "acquire": True, "names": []}


@contextmanager
def task_lock(name, ttl_seconds=3600):
    """Module-level fake; locked_task resolves `task_lock` from the decorated
    function's module, so this shadows tasks.base.task_lock for tests here."""
    _LOCK_STATE["names"].append((name, ttl_seconds))
    if not _LOCK_STATE["acquire"]:
        yield False
        return
    _LOCK_STATE["held"] = True
    try:
        yield True
    finally:
        _LOCK_STATE["held"] = False


class TestLockedTask:
    def setup_method(self):
        _LOCK_STATE.update({"held": False, "acquire": True, "names": []})

    def test_lock_held_while_body_runs(self):
        from tasks.base import locked_task

        @locked_task("unit_lock", ttl_seconds=42)
        def body(self_):
            return {"held_during_body": _LOCK_STATE["held"]}

        out = body(object())
        assert out == {"held_during_body": True}
        assert _LOCK_STATE["held"] is False                  # released after
        assert _LOCK_STATE["names"] == [("unit_lock", 42)]

    def test_skips_when_not_acquired(self):
        from tasks.base import locked_task
        _LOCK_STATE["acquire"] = False
        calls = []

        @locked_task("unit_lock")
        def body(self_):
            calls.append(1)
            return {"ran": True}

        assert body(object()) == {"skipped": True}
        assert calls == []

    def test_callable_lock_name_sees_task_args(self):
        from tasks.base import locked_task

        @locked_task(lambda self_, company_id=None: f"per_{company_id}" if company_id else "all")
        def body(self_, company_id=None):
            return "ok"

        assert body(object(), company_id="c9") == "ok"
        assert body(object()) == "ok"
        assert [n for n, _ in _LOCK_STATE["names"]] == ["per_c9", "all"]

    def test_all_scheduled_tasks_use_the_decorator(self):
        """Guard against the with-block pattern creeping back in."""
        import pathlib, re
        root = pathlib.Path(__file__).resolve().parents[1] / "tasks"
        offenders = []
        for f in sorted(root.glob("*.py")):
            src = f.read_text()
            if "@app.task(" not in src:
                continue  # helpers/docs, not scheduled tasks
            # pattern: `with task_lock(...) as acquired:` immediately followed by
            # an `if not acquired:` early-return — the lock is dropped right after.
            if re.search(r"with task_lock\([^\n]*\) as acquired:\n\s+if not acquired:", src):
                offenders.append(f.name)
        assert offenders == [], f"lock released before work in: {offenders}"


# ─────────────────────────────────────────────────────────────────────────────
# per-account login throttle
# ─────────────────────────────────────────────────────────────────────────────

class TestLoginEmailThrottle:
    def test_limits_per_email_independent_of_ip(self):
        from app.utils.rate_limiter import login_email_limiter
        key = f"throttle-{uuid.uuid4()}@example.com"
        # Redis is not running in tests → in-process fallback (deterministic).
        allowed = [login_email_limiter.is_allowed(key) for _ in range(login_email_limiter.max_calls + 1)]
        assert allowed[:-1] == [True] * login_email_limiter.max_calls
        assert allowed[-1] is False
        assert login_email_limiter.is_allowed(f"other-{uuid.uuid4()}@example.com") is True

    def test_login_endpoint_returns_429_when_account_throttled(self):
        from app.utils import rate_limiter
        client = TestClient(app)
        with patch.object(rate_limiter.login_email_limiter, "is_allowed", return_value=False), \
             patch("supabase.create_client", return_value=MagicMock()):
            r = client.post("/auth/login", json={"email": "a@b.com", "password": "x"})
        assert r.status_code == 429
        assert "Retry-After" in r.headers


# ─────────────────────────────────────────────────────────────────────────────
# tenant middleware decode
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantMiddleware:
    def test_attaches_tenant_header_using_app_decoder(self):
        client = TestClient(app)
        tok = _raw_token(sub="u1", company_id="company-xyz-123", role="owner", scope="access")
        # any non-public path exercises the middleware; a 404 is fine
        r = client.get("/definitely-not-a-route", headers={"Authorization": f"Bearer {tok}"})
        assert r.headers.get("X-Tenant-ID", "").startswith("company-")

    def test_garbage_token_does_not_crash(self):
        client = TestClient(app)
        r = client.get("/definitely-not-a-route", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 404
        assert "X-Tenant-ID" not in r.headers
