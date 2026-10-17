"""Release Phase 4 security regressions. Never quarantine these tests."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


class SharedRedis:
    """Separate client instances share a single backing store like two workers."""
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def hset(self, key, mapping):
        self.data.setdefault(key, {}).update(mapping)

    async def expire(self, key, ttl):
        assert ttl > 0

    async def hgetall(self, key):
        return dict(self.data.get(key, {}))

    async def hget(self, key, field):
        return self.data.get(key, {}).get(field)

    async def eval(self, script, numkeys, key, company_id, approval_id, decision):
        assert numkeys == 1
        assert "approval_decision" in script  # do not silently bypass atomic guard
        row = self.data.get(key)
        if row is None:
            return "missing"
        if row["company_id"] != company_id:
            return "forbidden"
        if row["status"] != "waiting_approval":
            return "not_pending"
        if row["approval_id"] != approval_id:
            return "mismatch"
        if row["approval_decision"] != "":
            return "already"
        row["approval_decision"] = decision
        return "ok"


@pytest.fixture
def cu_store(monkeypatch):
    from app.api import computer_use as cu
    data = {}
    monkeypatch.setattr(cu, "_redis", lambda: SharedRedis(data))
    return cu, data


@pytest.mark.asyncio
async def test_cu_session_survives_independent_requests(cu_store):
    cu, data = cu_store
    state = cu._SessionState("s1", "Excel", "read inventory", "co1")
    await state.save()
    # No process dictionary: an independent request reads Redis.
    result = await cu.get_session("s1", user={"company_id": "co1"})
    assert result.status == "running"
    assert result.task == "read inventory"
    assert data[cu._key("s1")]["company_id"] == "co1"
    with pytest.raises(HTTPException) as err:
        await cu.get_session("s1", user={"company_id": "other"})
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_cu_approval_across_workers_and_replay_block(cu_store):
    cu, data = cu_store
    state = cu._SessionState("s2", "Browser", "task", "co1")
    await state.save()
    waiter = asyncio.create_task(state.request_approval("click", "Approve write"))
    for _ in range(50):
        if data[cu._key("s2")]["status"] == "waiting_approval":
            break
        await asyncio.sleep(0)
    pending = data[cu._key("s2")]
    assert pending["status"] == "waiting_approval"
    request = cu.ApprovalRequest(session_id="s2", approval_id=pending["approval_id"])
    with pytest.raises(HTTPException) as err:
        await cu.approve_action(request, user={"company_id": "other", "role": "owner"})
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        await cu.approve_action(
            cu.ApprovalRequest(session_id="s2", approval_id="wrong"),
            user={"company_id": "co1", "role": "owner"},
        )
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        await cu.approve_action(request, user={"company_id": "co1", "role": "viewer"})
    assert err.value.status_code == 403
    assert (await cu.approve_action(
        request, user={"company_id": "co1", "role": "manager"},
    ))["status"] == "approved"
    with pytest.raises(HTTPException) as err:
        await cu.approve_action(request, user={"company_id": "co1", "role": "owner"})
    assert err.value.status_code == 409
    assert await asyncio.wait_for(waiter, timeout=2) == "approve"


@pytest.mark.asyncio
async def test_cu_redis_outage_fails_closed(monkeypatch):
    from app.api import computer_use as cu
    class Unavailable:
        async def __aenter__(self):
            raise ConnectionError("Redis down")
        async def __aexit__(self, *_):
            return False
    monkeypatch.setattr(cu, "_redis", lambda: Unavailable())
    with pytest.raises(HTTPException) as err:
        await cu._SessionState("s3", "App", "task", "co1").save()
    assert err.value.status_code == 503
    with pytest.raises(HTTPException) as err:
        await cu.get_session("s3", user={"company_id": "co1"})
    assert err.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("write_type", ["create_invoice", "create_po", "update_item"])
async def test_unapproved_write_never_enters_connector_queue(write_type):
    from app.api.connectors import DispatchTaskRequest, dispatch_task
    db = MagicMock()
    with pytest.raises(HTTPException) as err:
        await dispatch_task(
            DispatchTaskRequest(task_type=write_type, parameters={"amount": 123}),
            user={"company_id": "co1", "role": "owner"}, db=db,
        )
    assert err.value.status_code == 403
    assert "approval" in err.value.detail.lower()
    db.table.assert_not_called()  # no task inserted, even for owner


def test_connector_secret_digest_only_at_rest():
    from app.api.connectors import generate_install_secret
    # Async endpoint is driven below; assert at-rest value differs from one-time token.
    async def check():
        db = MagicMock()
        response = await generate_install_secret(
            user={"company_id": "co1", "role": "owner"}, db=db,
        )
        raw = response["install_secret"]
        call = db.table.return_value.update.call_args[0][0]
        assert call["connector_install_secret"] == "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in repr(call)
    asyncio.run(check())


def test_redis_revocation_outage_always_blocks_and_emits_alarm():
    from app.auth import jwt as token_lib
    from app.auth.rbac import get_current_user
    token = token_lib.create_access_token({"sub": "u", "company_id": "co1", "role": "owner"})
    with patch("redis.from_url", side_effect=ConnectionError("unavailable")), \
         patch.object(token_lib, "_revocation_alarm") as alarm:
        with pytest.raises(token_lib.RevocationStoreUnavailable):
            token_lib.is_token_revoked("some-jti")
        with pytest.raises(token_lib.RevocationStoreUnavailable):
            token_lib.revoke_token(token)
        with pytest.raises(token_lib.RevocationStoreUnavailable):
            token_lib.blacklist_jti("some-jti", 120)
        with pytest.raises(HTTPException) as err:
            get_current_user(token=token)
        assert err.value.status_code == 503
        assert alarm.call_count >= 4


@pytest.mark.asyncio
async def test_redis_failure_cannot_issue_refreshed_access_token():
    from app.auth import jwt as token_lib
    from app.api.auth import refresh_token, RefreshRequest
    token = token_lib.create_access_token({"sub": "u", "company_id": "co1", "role": "owner"})
    with patch("redis.from_url", side_effect=ConnectionError("unavailable")), \
         patch.object(token_lib, "_revocation_alarm"):
        with pytest.raises(HTTPException) as err:
            await refresh_token(RefreshRequest(access_token=token))
        assert err.value.status_code == 503


def test_production_login_limiter_fails_closed_without_redis():
    from app.utils import rate_limiter as mod
    limiter = mod.RedisRateLimiter(5, 60, "test:failclosed")
    with patch.object(mod.settings, "debug", False), \
         patch.object(limiter, "_redis", side_effect=ConnectionError("offline")), \
         patch("app.auth.jwt._revocation_alarm") as alarm:
        with pytest.raises(HTTPException) as err:
            limiter.is_allowed("login-account")
        assert err.value.status_code == 503
        assert alarm.called


def test_production_middleware_rejects_requests_without_redis():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.middleware import rate_limit as mod
    app = FastAPI()

    @app.get("/secure")
    async def secure():
        return {"ok": True}

    app.add_middleware(mod.RateLimitMiddleware)
    with patch.object(mod, "get_settings", return_value=SimpleNamespace(debug=False)), \
         patch.object(mod.RateLimitMiddleware, "_get_redis", return_value=None), \
         patch("app.auth.jwt._revocation_alarm") as alarm:
        response = TestClient(app).get("/secure")
    assert response.status_code == 503
    assert alarm.called
