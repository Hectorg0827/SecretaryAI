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
    monkeypatch.setattr(
        team, "_send_invite_email",
        lambda email, name, link, _, role="manager": sent.append((email, name, link, role)),
    )
    monkeypatch.setattr(team, "_audit", Mock())
    monkeypatch.setattr(team, "_require_finance_audit", Mock())
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
    with pytest.raises(ValidationError):
        team.InviteTeamRequest(email=EMAIL, full_name="Test Role", role="back_office")
    with pytest.raises(ValidationError):
        team.InviteTeamRequest(email=EMAIL, full_name="Test Role", role="ar_specialist", company_id="other")
    with pytest.raises(ValidationError):
        team.AcceptInviteRequest(token="a" * 44, password="SecurePassword123", role="owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("role,authorized_scopes", [
    ("finance_controller", {"ar", "ap", "ledger"}),
    ("ar_specialist", {"ar"}),
    ("ap_specialist", {"ap"}),
    ("pricing_billbacks", {"pricing"}),
])
async def test_finance_role_invitation_activates_only_server_bound_tenant_and_scope(
    setup, role, authorized_scopes,
):
    from app.auth.rbac import ROLE_PERMISSIONS
    from app.domain.finance_access import finance_scopes_for_role

    db, redis, settings, sent = setup
    invitation = team.InviteTeamRequest(email=EMAIL, full_name="Staging Test", role=role)
    result = await team.invite_manager(invitation, ACTOR)
    assert result == {"status": "sent", "role": role, "expires_in_hours": 48}
    assert len(db.users) == 1  # An invite is not an account.
    assert sent[0][3] == role
    assert len(redis.data) == 1
    token = sent[0][2].split("#token=")[1]
    assert token not in repr(result)

    activated = await team.accept_invitation(
        team.AcceptInviteRequest(token=token, password="SyntheticPassword987")
    )
    assert activated["role"] == role
    assert db.users[-1]["company_id"] == ACTOR["company_id"]
    assert db.users[-1]["role"] == role
    assert db.users[-1]["is_active"] is True
    assert finance_scopes_for_role(role) == authorized_scopes
    assert ROLE_PERMISSIONS[role] == {"view_finance_workspace"}
    with pytest.raises(HTTPException) as replay:
        await team.accept_invitation(team.AcceptInviteRequest(token=token, password="SyntheticPassword987"))
    assert replay.value.status_code == 400


@pytest.mark.asyncio
async def test_finance_role_invites_fail_closed_for_manager_disabled_mail_and_wrong_tenant(setup):
    db, redis, settings, _ = setup
    invite = team.InviteTeamRequest(email=EMAIL, full_name="Staging Test", role="ar_specialist")
    with pytest.raises(HTTPException) as denied:
        await team.invite_manager(invite, {**ACTOR, "role": "manager"})
    assert denied.value.status_code == 403

    db.users[0]["company_id"] = "wrong-tenant"
    with pytest.raises(HTTPException) as wrong_tenant:
        await team.invite_manager(invite, ACTOR)
    assert wrong_tenant.value.status_code == 403
    db.users[0]["company_id"] = COMPANY

    settings.secretaryai_invite_delivery_enabled = False
    with pytest.raises(HTTPException) as disabled:
        await team.invite_manager(invite, ACTOR)
    assert disabled.value.status_code == 503
    assert not redis.data


@pytest.mark.asyncio
async def test_modified_stored_invite_cannot_promote_to_owner(setup):
    import json
    db, redis, _, sent = setup
    invite = team.InviteTeamRequest(email=EMAIL, full_name="Staging Test", role="ar_specialist")
    await team.invite_manager(invite, ACTOR)
    key = next(iter(redis.data))
    token = sent[0][2].split("#token=")[1]
    assert token not in redis.data[key]  # Only the token digest is stored as key.
    # Exercise a malformed server-side value without contacting a real provider.
    payload = json.loads(redis.data[key])
    payload["role"] = "owner"
    redis.data[key] = json.dumps(payload)
    with pytest.raises(HTTPException) as invalid:
        await team.accept_invitation(team.AcceptInviteRequest(token=token, password="SecurePassword123"))
    assert invalid.value.status_code == 400
    assert len(db.users) == 1


@pytest.mark.asyncio
async def test_finance_invitation_requires_durable_audit_before_token_or_activation(setup, monkeypatch):
    db, redis, _, sent = setup
    invite = team.InviteTeamRequest(email=EMAIL, full_name="Synthetic Staff", role="ar_specialist")

    def unavailable(*_):
        raise HTTPException(status_code=503, detail="Finance role audit unavailable")

    monkeypatch.setattr(team, "_require_finance_audit", unavailable)
    with pytest.raises(HTTPException) as initial:
        await team.invite_manager(invite, ACTOR)
    assert initial.value.status_code == 503
    assert not redis.data and not sent and len(db.users) == 1

    monkeypatch.setattr(team, "_require_finance_audit", Mock())
    await team.invite_manager(invite, ACTOR)
    token = sent[0][2].split("#token=")[1]
    monkeypatch.setattr(team, "_require_finance_audit", unavailable)
    with pytest.raises(HTTPException) as acceptance:
        await team.accept_invitation(team.AcceptInviteRequest(token=token, password="SecurePassword123"))
    assert acceptance.value.status_code == 503
    assert len(db.users) == 1


def test_finance_audit_writer_fail_closed_when_event_not_persisted(monkeypatch):
    from app.utils import audit
    monkeypatch.setattr(audit, "write_audit_event", lambda *_args, **_kwargs: None)
    with pytest.raises(HTTPException) as denied:
        team._require_finance_audit(FakeDB(), COMPANY, "finance_role_invite_attempt", OWNER, "ap_specialist")
    assert denied.value.status_code == 503


def test_lionbi_lola_resend_invitation_is_branded_and_mocked(monkeypatch):
    """Validate the exact provider payload without any network or mailbox delivery."""
    import httpx

    settings = SimpleNamespace(
        secretaryai_invite_delivery_enabled=True,
        secretaryai_invite_email_recipient=EMAIL,
        resend_api_key="test-only-resend-key",
        resed_api_key="",
        sendgrid_api_key="",
        from_email="lola@lionbi.app",
    )
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=202)

    monkeypatch.setattr(httpx, "post", fake_post)
    invite_url = "https://secretaryai-web-staging.up.railway.app/accept-invite#token=test"
    team._send_invite_email(EMAIL, "Abel Garcia", invite_url, settings)

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://api.resend.com/emails"
    payload = kwargs["json"]
    assert payload["from"] == "lola@lionbi.app"
    assert payload["to"] == [EMAIL]
    assert payload["subject"] == "Lion BI staging — Manager invitation"
    assert "Lion Business Intelligence (Lion BI)" in payload["text"]
    assert "Lola" in payload["text"]
    assert invite_url in payload["text"]
    assert "SecretaryAI" not in payload["text"]

    with pytest.raises(HTTPException) as exc:
        team._send_invite_email("not-allowed@example.com", "Other Person", invite_url, settings)
    assert exc.value.status_code == 403
    assert len(calls) == 1  # No additional delivery attempt.

    settings.secretaryai_invite_delivery_enabled = False
    with pytest.raises(HTTPException) as exc:
        team._send_invite_email(EMAIL, "Abel Garcia", invite_url, settings)
    assert exc.value.status_code == 503
    assert len(calls) == 1


def test_http_routes_are_registered_and_maintain_separate_auth(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.utils.rate_limiter import login_limiter

    db, _, _, sent = setup
    monkeypatch.setattr(login_limiter, "is_allowed", lambda *_: True)
    app = FastAPI()
    app.include_router(team.router, prefix="/api/team")
    app.dependency_overrides[team.get_current_user] = lambda: ACTOR
    # The paid owner routes still work in billing-disabled staging, without a
    # real subscription database or any external access during this test.
    from app.api.deps import get_db
    from app.middleware import subscription_guard
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(subscription_guard, "get_settings", lambda: SimpleNamespace(billing_enabled=False))
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
