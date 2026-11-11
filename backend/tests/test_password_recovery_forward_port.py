"""Password recovery security with synthetic users and no outbound provider calls."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import account_security as security
from app.auth.jwt import hash_password, password_session_version, verify_password
from app.middleware import active_session_boundary as gate6c


OWNER = "54bde80b-3d42-4077-a0af-ed3b7384110d"
COMPANY = "89399925-ea7d-43f3-91cd-95f9a5636b73"
MAIL = "pilot@example.com"
ACTOR = {"sub": OWNER, "company_id": COMPANY, "role": "owner"}


class MemoryRedis:
    def __init__(self):
        self.values = {}

    def setex(self, key, _ttl, value):
        self.values[key] = value
        return True

    def getdel(self, key):
        return self.values.pop(key, None)

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def exists(self, key):
        return int(key in self.values)


class Query:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self.filters = {}
        self.inserted = None
        self.updated = None

    def select(self, *_): return self
    def eq(self, key, value):
        self.filters[key] = value
        return self
    def limit(self, *_): return self
    def insert(self, row):
        self.inserted = row
        return self
    def update(self, row):
        self.updated = row
        return self
    def execute(self):
        if self.store.offline:
            raise RuntimeError("synthetic DB down")
        if self.name == "audit_events":
            if self.store.audit_down:
                raise RuntimeError("synthetic audit down")
            self.store.events.append(self.inserted)
            return SimpleNamespace(data=[self.inserted])
        rows = [row for row in self.store.users if all(
            row.get(key) == value for key, value in self.filters.items()
        )]
        if self.updated is not None:
            if self.store.concurrent_change:
                self.store.users[0]["hashed_password"] = hash_password("ElsewherePassword987")
                self.store.concurrent_change = False
                rows = [row for row in self.store.users if all(
                    row.get(key) == value for key, value in self.filters.items()
                )]
            for row in rows:
                row.update(self.updated)
        return SimpleNamespace(data=[dict(row) for row in rows])


class FakeDB:
    def __init__(self):
        self.offline = self.audit_down = self.concurrent_change = False
        self.events = []
        self.users = [{"id": OWNER, "company_id": COMPANY, "role": "owner",
                       "email": MAIL, "is_active": True,
                       "hashed_password": hash_password("OriginalPassword123")}]

    def table(self, name):
        assert name in {"users", "audit_events"}
        return Query(self, name)


@pytest.fixture
def pilot(monkeypatch):
    db, redis = FakeDB(), MemoryRedis()
    settings = SimpleNamespace(
        secretaryai_deployment_env="staging", secretaryai_invite_delivery_enabled=True,
        secretaryai_invite_email_recipient=MAIL,
        frontend_url="https://secretaryai-web-staging.up.railway.app",
    )
    sent = []
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "_db", lambda: db)
    monkeypatch.setattr(security, "_redis", lambda: redis)
    monkeypatch.setattr(security, "_send_reset_mail", lambda email, url, _: sent.append((email, url)))
    monkeypatch.setattr(security._EMAIL_LIMITER, "is_allowed", lambda _: True)
    monkeypatch.setattr(security._CHANGE_LIMITER, "is_allowed", lambda _: True)
    return db, redis, settings, sent


@pytest.mark.asyncio
async def test_forgot_password_is_non_enumerating_and_mailbox_gated(pilot):
    db, redis, settings, sent = pilot
    wanted = await security.forgot_password(security.ForgotPasswordRequest(email=MAIL))
    assert wanted == {"status": "ok", "message": "If that email has an account, a reset link is on its way."}
    assert len(sent) == 1 and sent[0][0] == MAIL
    assert sent[0][1].startswith("https://secretaryai-web-staging.up.railway.app/reset-password#token=")
    raw = sent[0][1].split("#token=")[1]
    assert all(raw not in key and raw not in value for key, value in redis.values.items())
    unknown = await security.forgot_password(security.ForgotPasswordRequest(email="unknown@example.com"))
    assert unknown == wanted and len(sent) == 1
    settings.secretaryai_invite_delivery_enabled = False
    assert await security.forgot_password(security.ForgotPasswordRequest(email=MAIL)) == wanted
    assert len(sent) == 1
    assert db.events[0]["event_type"] == "password_reset_link_requested"


@pytest.mark.asyncio
async def test_reset_is_single_use_and_invalidates_all_existing_sessions(pilot, monkeypatch):
    db, redis, _, sent = pilot
    monkeypatch.setattr(gate6c, "get_db", lambda: db)
    monkeypatch.setattr(gate6c, "is_token_revoked", lambda _: False)
    old_hash = db.users[0]["hashed_password"]
    payload = {"sub": OWNER, "company_id": COMPANY, "role": "owner", "jti": "old-jti",
               "pwd": password_session_version(OWNER, old_hash)}
    assert gate6c._session_check(payload) is None
    await security.forgot_password(security.ForgotPasswordRequest(email=MAIL))
    token = sent[0][1].split("#token=")[1]
    result = await security.reset_password(security.ResetPasswordRequest(
        token=token, new_password="FreshPassword456",
    ))
    assert result["status"] == "ok" and not redis.values
    assert verify_password("FreshPassword456", db.users[0]["hashed_password"])
    assert not verify_password("OriginalPassword123", db.users[0]["hashed_password"])
    assert gate6c._session_check(payload)[0] == 401
    assert gate6c._session_check({**payload, "pwd": password_session_version(
        OWNER, db.users[0]["hashed_password"]
    )}) is None
    with pytest.raises(HTTPException) as exc:
        await security.reset_password(security.ResetPasswordRequest(token=token, new_password="AgainPassword456"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_second_unexpired_link_cannot_reset_a_changed_password(pilot):
    _, _, _, sent = pilot
    await security.forgot_password(security.ForgotPasswordRequest(email=MAIL))
    await security.forgot_password(security.ForgotPasswordRequest(email=MAIL))
    first, second = [item[1].split("#token=")[1] for item in sent]
    await security.reset_password(security.ResetPasswordRequest(token=first, new_password="NewPassword987"))
    with pytest.raises(HTTPException) as exc:
        await security.reset_password(security.ResetPasswordRequest(token=second, new_password="AgainPassword123"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_audit_failure_and_concurrent_change_block_reset(pilot):
    db, _, _, sent = pilot
    await security.forgot_password(security.ForgotPasswordRequest(email=MAIL))
    token = sent[-1][1].split("#token=")[1]
    old = db.users[0]["hashed_password"]
    db.audit_down = True
    with pytest.raises(HTTPException) as exc:
        await security.reset_password(security.ResetPasswordRequest(token=token, new_password="NewPassword987"))
    assert exc.value.status_code == 503 and db.users[0]["hashed_password"] == old
    db.audit_down = False
    await security.forgot_password(security.ForgotPasswordRequest(email=MAIL))
    token = sent[-1][1].split("#token=")[1]
    db.concurrent_change = True
    with pytest.raises(HTTPException) as exc:
        await security.reset_password(security.ResetPasswordRequest(token=token, new_password="NewPassword987"))
    assert exc.value.status_code == 400
    assert not verify_password("NewPassword987", db.users[0]["hashed_password"])


@pytest.mark.asyncio
async def test_change_password_checks_existing_secret_and_returns_fresh_token(pilot):
    from app.auth.jwt import decode_access_token
    db, _, _, _ = pilot
    old = db.users[0]["hashed_password"]
    with pytest.raises(HTTPException) as exc:
        await security.change_password(security.ChangePasswordRequest(
            current_password="WrongPassword123", new_password="ChangedPassword789",
        ), ACTOR)
    assert exc.value.status_code == 400 and db.users[0]["hashed_password"] == old
    updated = await security.change_password(security.ChangePasswordRequest(
        current_password="OriginalPassword123", new_password="ChangedPassword789",
    ), ACTOR)
    claims = decode_access_token(updated["access_token"])
    assert claims["pwd"] == password_session_version(OWNER, db.users[0]["hashed_password"])
    assert claims["role"] == "owner"
    assert verify_password("ChangedPassword789", db.users[0]["hashed_password"])


def test_password_schemas_exclude_weak_and_extra_fields():
    for password in ("weak", "passwordonly", "12345678", "🦁" * 30):
        with pytest.raises(ValidationError):
            security.ResetPasswordRequest(token="a" * 44, new_password=password)
    with pytest.raises(ValidationError):
        security.ChangePasswordRequest(current_password="OldPassword123", new_password="NewPassword456", company_id=COMPANY)


def test_atomic_jti_claim_allows_only_one_concurrent_refresh(monkeypatch):
    import redis as redis_lib
    from app.auth.jwt import claim_jti_once, is_token_revoked
    memory = MemoryRedis()
    monkeypatch.setattr(redis_lib, "from_url", lambda *_args, **_kwargs: memory)
    assert claim_jti_once("one-test-token", 600) is True
    assert claim_jti_once("one-test-token", 600) is False
    assert is_token_revoked("one-test-token") is True


def test_refresh_jti_atomic_claim_rejects_parallel_replays(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import fakeredis
    import redis as redis_lib
    from app.auth.jwt import claim_jti_once

    memory = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_lib, "from_url", lambda *_args, **_kwargs: memory)
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: claim_jti_once("parallel-test-token", 600), range(16)))
    assert sum(outcomes) == 1


def test_real_mounted_recovery_route_is_public_and_generic(pilot):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    result = client.post("/auth/forgot-password", json={"email": MAIL})
    assert result.status_code == 200, result.text
    assert "If that email has an account" in result.json()["message"]
    invalid = client.post("/auth/reset-password", json={"token": "x" * 44,
                                                         "new_password": "NewPassword987"})
    assert invalid.status_code == 400
