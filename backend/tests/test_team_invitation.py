"""Security gates for staging-only independently accepted Manager invitations."""

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api import team


COMPANY = "89399925-ea7d-43f3-91cd-95f9a5636b73"
OWNER = "54bde80b-3d42-4077-a0af-ed3b7384110d"
ACTOR = {"role": "owner", "sub": OWNER, "company_id": COMPANY}
EMAIL = "crazywillysbar@gmail.com"


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def setex(self, key, seconds, value):
        self.data[key] = value
        self.ttls[key] = seconds
        return True

    def getdel(self, key):
        return self.data.pop(key, None)

    def delete(self, key):
        return int(self.data.pop(key, None) is not None)


class Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters = {}
        self.to_insert = None

    def select(self, *_): return self
    def eq(self, key, val):
        self.filters[key] = val
        return self
    def limit(self, *_): return self
    def order(self, *_): return self
    def insert(self, row):
        self.to_insert = row
        return self
    def execute(self):
        if self.to_insert is not None:
            if self.db.fail_insert:
                raise RuntimeError("simulated DB outage")
            if any(row["email"] == self.to_insert["email"] for row in self.db.users):
                raise RuntimeError("duplicate email")
            new = {**self.to_insert, "id": "new-manager-id"}
            self.db.users.append(new)
            return SimpleNamespace(data=[new])
        return SimpleNamespace(data=[row for row in self.db.users if all(
            row.get(k) == v for k, v in self.filters.items()
        )])


class FakeDB:
    def __init__(self):
        self.users = [{"id": OWNER, "company_id": COMPANY, "email": "hector@example.com",
                       "role": "owner", "is_active": True}]
        self.fail_insert = False

    def table(self, name):
        assert name == "users"
        return Query(self, name)


@pytest.fixture
def setup(monkeypatch):
    db, redis = FakeDB(), MemoryRedis()
    settings = SimpleNamespace(
        secretaryai_deployment_env="staging", secretaryai_invite_delivery_enabled=True,
        secretaryai_invite_email_recipient=EMAIL,
        frontend_url="https://secretaryai-web-staging.up.railway.app",
        sendgrid_api_key="", resend_api_key="", resed_api_key="",
        from_email="invites@example.com",
    )
    sent = []
    monkeypatch.setattr(team, "_db", lambda: db)
    monkeypatch.setattr(team, "_redis", lambda: redis)
    monkeypatch.setattr(team, "get_settings", lambda: settings)
    monkeypatch.setattr(team, "_send_invite_email", lambda email, name, link, _: sent.append((email, name, link)))
    monkeypatch.setattr(team, "_audit", Mock())
    return db, redis, settings, sent


@pytest.mark.asyncio
async def test_owner_sends_mailbox_only_invitation_and_no_token_in_response(setup):
    db, redis, _, sent = setup
    response = await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    assert response == {"status": "sent", "role": "manager", "expires_in_hours": 48}
    assert len(db.users) == 1  # No account before mailbox acceptance.
    assert sent[0][:2] == (EMAIL, "Abel Garcia")
    assert sent[0][2].startswith("https://secretaryai-web-staging.up.railway.app/accept-invite#token=")
    token = sent[0][2].split("#token=")[1]
    assert len(redis.data) == 1
    assert all(token not in key and token not in value for key, value in redis.data.items())
    assert list(redis.ttls.values()) == [48 * 60 * 60]
    assert list(redis.data.keys())[0].endswith(hashlib.sha256(token.encode()).hexdigest())


@pytest.mark.asyncio
async def test_only_current_active_owner_can_invite(setup):
    db, redis, _, _ = setup
    with pytest.raises(HTTPException) as exc:
        await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"),
                                  {**ACTOR, "role": "manager"})
    assert exc.value.status_code == 403
    db.users[0]["is_active"] = False
    with pytest.raises(HTTPException) as exc:
        await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    assert exc.value.status_code == 403
    assert not redis.data


@pytest.mark.asyncio
async def test_staging_allowlist_and_disabled_delivery_fail_closed(setup):
    _, redis, settings, _ = setup
    with pytest.raises(HTTPException) as exc:
        await team.invite_manager(team.InviteManagerRequest(email="outsider@example.com", full_name="Outside Person"), ACTOR)
    assert exc.value.status_code == 403
    settings.secretaryai_invite_delivery_enabled = False
    with pytest.raises(HTTPException) as exc:
        await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    assert exc.value.status_code == 503
    assert not redis.data


@pytest.mark.asyncio
async def test_one_time_acceptance_creates_only_manager_then_replay_fails(setup):
    db, redis, _, sent = setup
    await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    token = sent[0][2].split("#token=")[1]
    result = await team.accept_invitation(team.AcceptInviteRequest(token=token, password="StrongPassword789"))
    assert result["status"] == "accepted"
    assert len(db.users) == 2
    manager = db.users[-1]
    assert manager["company_id"] == COMPANY
    assert manager["email"] == EMAIL
    assert manager["role"] == "manager"
    assert manager["is_active"] is True
    assert manager["hashed_password"] != "StrongPassword789"
    assert not redis.data
    with pytest.raises(HTTPException) as exc:
        await team.accept_invitation(team.AcceptInviteRequest(token=token, password="StrongPassword789"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_existing_account_and_email_delivery_failure_do_not_grant_access(setup, monkeypatch):
    db, redis, _, _ = setup
    db.users.append({"id": "other", "company_id": COMPANY, "email": EMAIL, "role": "viewer"})
    with pytest.raises(HTTPException) as exc:
        await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    assert exc.value.status_code == 409
    db.users.pop()
    def failure(*_): raise HTTPException(status_code=503, detail="Provider unavailable")
    monkeypatch.setattr(team, "_send_invite_email", failure)
    with pytest.raises(HTTPException) as exc:
        await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    assert exc.value.status_code == 503
    assert not redis.data


@pytest.mark.asyncio
async def test_acceptance_refuses_revoked_owner_and_db_failure_consumes_token(setup):
    db, redis, _, sent = setup
    await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    token = sent[0][2].split("#token=")[1]
    db.users[0]["is_active"] = False
    with pytest.raises(HTTPException) as exc:
        await team.accept_invitation(team.AcceptInviteRequest(token=token, password="SecurePassword123"))
    assert exc.value.status_code == 403
    assert len(db.users) == 1
    assert not redis.data

    db.users[0]["is_active"] = True
    await team.invite_manager(team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia"), ACTOR)
    token = sent[-1][2].split("#token=")[1]
    db.fail_insert = True
    with pytest.raises(HTTPException) as exc:
        await team.accept_invitation(team.AcceptInviteRequest(token=token, password="SecurePassword123"))
    assert exc.value.status_code == 409
    assert len(db.users) == 1
    assert not redis.data


def test_invalid_invitation_role_and_password_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        team.InviteManagerRequest(email=EMAIL, full_name="Abel Garcia", role="owner")
    with pytest.raises(ValidationError):
        team.AcceptInviteRequest(token="a" * 44, password="weak")


def test_http_routes_are_registered_and_maintain_separate_auth(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.utils.rate_limiter import login_limiter

    _, _, _, sent = setup
    monkeypatch.setattr(login_limiter, "is_allowed", lambda *_: True)
    app = FastAPI()
    app.include_router(team.router, prefix="/api/team")
    app.dependency_overrides[team.get_current_user] = lambda: ACTOR
    client = TestClient(app)

    response = client.post("/api/team/invitations", json={
        "email": EMAIL, "full_name": "Abel Garcia", "role": "manager",
    })
    assert response.status_code == 201
    assert "token" not in response.json()
    token = sent[0][2].split("#token=")[1]
    response = client.post("/api/team/invitations/accept", json={
        "token": token, "password": "LongPassword998",
    })
    assert response.status_code == 201
    assert response.json()["role"] == "manager"

    # It must never accept client-selected owner roles at invitation time.
    response = client.post("/api/team/invitations", json={
        "email": EMAIL, "full_name": "Abel Garcia", "role": "owner",
    })
    assert response.status_code == 422
