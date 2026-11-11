"""Staging Outlook PKCE, one-use state, owner, tenant, and rotation safeguards."""
from contextlib import contextmanager
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException

from app.api import outlook_oauth
from app.utils.encryption import decrypt, encrypt


COMPANY = "company-synthetic-1"
OWNER = "owner-synthetic-1"
ACTOR = {"sub": OWNER, "company_id": COMPANY, "role": "owner"}


class CompanyQuery:
    def __init__(self, db):
        self.db, self.filters, self.updates = db, {}, None

    def select(self, *_): return self
    def limit(self, *_): return self
    def eq(self, name, value):
        self.filters[name] = value
        return self
    def is_(self, name, value):
        assert value == "null"
        self.filters[name] = None
        return self
    def update(self, values):
        self.updates = values
        return self
    def execute(self):
        matches = [r for r in self.db.rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.updates is not None:
            for row in matches:
                row.update(self.updates)
        return SimpleNamespace(data=[dict(row) for row in matches])


class FakeDB:
    def __init__(self):
        self.rows = [
            {"id": COMPANY, "ms_access_token": None, "ms_refresh_token": None},
            {"id": "company-foreign", "ms_access_token": "foreign", "ms_refresh_token": "foreign"},
        ]

    def table(self, name):
        assert name == "companies"
        return CompanyQuery(self)


def _settings():
    return SimpleNamespace(
        secretaryai_deployment_env="staging", ms_client_id="synthetic-ms-client",
        ms_client_secret="synthetic-ms-secret", ms_tenant="common",
        ms_redirect_uri="https://secretaryai-backend-staging.up.railway.app/auth/outlook/callback",
        frontend_url="https://secretaryai-web-staging.up.railway.app",
        secret_key="test-only-key-never-used-in-production",
    )


@pytest.fixture
def harness(monkeypatch):
    db, states, settings = FakeDB(), {}, _settings()
    authorization = {"active": True}
    epoch = {COMPANY: 0}

    class FakeCache:
        def get(self, key):
            return str(epoch.get(key.split(":")[-1], 0))
        def incr(self, key):
            company_id = key.split(":")[-1]
            epoch[company_id] = epoch.get(company_id, 0) + 1
            return epoch[company_id]

    @contextmanager
    def company_lock(_company_id):
        yield FakeCache()

    def verify_owner(user_id, company_id):
        if user_id != OWNER or company_id != COMPANY or not authorization["active"]:
            raise HTTPException(status_code=403, detail="Owner revoked")

    monkeypatch.setattr(outlook_oauth, "_db", lambda: db)
    monkeypatch.setattr(outlook_oauth, "get_settings", lambda: settings)
    monkeypatch.setattr(outlook_oauth, "_active_owner", verify_owner)
    monkeypatch.setattr(outlook_oauth, "_company_lock", company_lock)
    monkeypatch.setattr(outlook_oauth, "_state_set", lambda key, val: states.__setitem__(key, val))
    monkeypatch.setattr(outlook_oauth, "_state_pop", lambda key: states.pop(key, None))
    monkeypatch.setattr(outlook_oauth, "_require_audit", lambda *_: None)

    async def fake_exchange(code, pkce, used_settings):
        assert code == "synthetic-code" and len(pkce) > 20 and used_settings is settings
        return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}

    monkeypatch.setattr(outlook_oauth, "_exchange_code", fake_exchange)
    return db, states, settings, authorization


@pytest.mark.asyncio
async def test_oauth_pkce_one_time_state_and_ciphertext(harness):
    db, states, settings, _ = harness
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    consent = urlsplit(response["url"])
    assert consent.hostname == "login.microsoftonline.com"
    assert consent.path == "/common/oauth2/v2.0/authorize"
    params = parse_qs(consent.query)
    assert params["code_challenge_method"] == ["S256"]
    assert "Mail.ReadWrite" in params["scope"][0]
    state = params["state"][0]
    assert "synthetic-ms-secret" not in response["url"]
    assert all("synthetic-access" not in data for data in states.values())
    result = await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert result.status_code == 307 and "outlook=connected" in result.headers["location"]
    assert decrypt(db.rows[0]["ms_access_token"], settings.secret_key) == "synthetic-access"
    assert decrypt(db.rows[0]["ms_refresh_token"], settings.secret_key) == "synthetic-refresh"
    assert db.rows[0]["ms_tenant_id"] == "common"
    assert db.rows[1]["ms_access_token"] == "foreign"
    with pytest.raises(HTTPException) as error:
        await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_owner_demoted_before_callback_fails_closed(harness):
    db, _, _, authorization = harness
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    state = parse_qs(urlsplit(response["url"]).query)["state"][0]
    authorization["active"] = False
    with pytest.raises(HTTPException) as error:
        await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert error.value.status_code == 403
    assert db.rows[0]["ms_access_token"] is None


@pytest.mark.asyncio
async def test_consented_callback_cannot_override_new_connection(harness):
    db, _, settings, _ = harness
    old = encrypt("prior-token", settings.secret_key)
    db.rows[0]["ms_access_token"] = old
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    state = parse_qs(urlsplit(response["url"]).query)["state"][0]
    db.rows[0]["ms_access_token"] = encrypt("newer-token", settings.secret_key)
    result = await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert "outlook=error" in result.headers["location"]
    assert decrypt(db.rows[0]["ms_access_token"], settings.secret_key) == "newer-token"


@pytest.mark.asyncio
async def test_disconnected_token_generation_is_not_rewritten_by_callback(harness):
    db, _, settings, _ = harness
    db.rows[0]["ms_access_token"] = encrypt("original", settings.secret_key)
    db.rows[0]["ms_refresh_token"] = encrypt("original-refresh", settings.secret_key)
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    state = parse_qs(urlsplit(response["url"]).query)["state"][0]
    assert (await outlook_oauth.outlook_disconnect(ACTOR))["status"] == "disconnected"
    result = await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert "outlook=error" in result.headers["location"]
    assert db.rows[0]["ms_access_token"] is None
    assert db.rows[1]["ms_access_token"] == "foreign"


@pytest.mark.asyncio
async def test_explicit_disconnect_invalidates_pending_first_connection(harness):
    db, _, _, _ = harness
    assert db.rows[0]["ms_access_token"] is None
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    state = parse_qs(urlsplit(response["url"]).query)["state"][0]
    await outlook_oauth.outlook_disconnect(ACTOR)
    result = await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert "outlook=error" in result.headers["location"]
    assert db.rows[0]["ms_access_token"] is None


@pytest.mark.asyncio
async def test_provider_error_consumes_state_and_no_account_write(harness):
    db, _, _, _ = harness
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    state = parse_qs(urlsplit(response["url"]).query)["state"][0]
    result = await outlook_oauth.outlook_callback(code="", state=state, error="access_denied")
    assert "outlook=error" in result.headers["location"]
    with pytest.raises(HTTPException):
        await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert db.rows[0]["ms_access_token"] is None


@pytest.mark.asyncio
async def test_unavailable_audit_blocks_connect_and_disconnect(harness, monkeypatch):
    db, _, settings, _ = harness
    monkeypatch.setattr(outlook_oauth, "_require_audit", lambda *_:
                        (_ for _ in ()).throw(HTTPException(status_code=503)))
    response = await outlook_oauth.outlook_connect_url(ACTOR)
    state = parse_qs(urlsplit(response["url"]).query)["state"][0]
    with pytest.raises(HTTPException) as error:
        await outlook_oauth.outlook_callback(code="synthetic-code", state=state, error="")
    assert error.value.status_code == 503 and db.rows[0]["ms_access_token"] is None
    db.rows[0]["ms_access_token"] = encrypt("keep", settings.secret_key)
    with pytest.raises(HTTPException) as error:
        await outlook_oauth.outlook_disconnect(ACTOR)
    assert error.value.status_code == 503
    assert decrypt(db.rows[0]["ms_access_token"], settings.secret_key) == "keep"


def test_prod_cannot_open_staging_oauth_routes(monkeypatch):
    settings = _settings()
    settings.secretaryai_deployment_env = "production"
    monkeypatch.setattr(outlook_oauth, "get_settings", lambda: settings)
    with pytest.raises(HTTPException) as error:
        outlook_oauth._settings()
    assert error.value.status_code == 404


def test_microsoft_token_fingerprint_never_exposes_ciphertext():
    token = "encrypted-canary"
    assert outlook_oauth._token_fingerprint(token) == hashlib.sha256(token.encode()).hexdigest()
    assert token not in outlook_oauth._token_fingerprint(token)


@pytest.mark.asyncio
async def test_code_exchange_requires_graph_identity_and_refresh_credential():
    settings = _settings()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = httpx.Response(200, json={
        "access_token": "synthetic-token", "refresh_token": "synthetic-refresh",
    }, request=httpx.Request("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token"))
    client.get.return_value = httpx.Response(200, json={"id": "synthetic-mailbox"},
                                            request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me"))
    with patch("app.api.outlook_oauth.httpx.AsyncClient", return_value=client):
        result = await outlook_oauth._exchange_code("synthetic-code", "synthetic-pkce", settings)
    assert result["refresh_token"] == "synthetic-refresh"
    submitted = client.post.await_args.kwargs["data"]
    assert submitted["client_secret"] == settings.ms_client_secret
    assert submitted["code_verifier"] == "synthetic-pkce"
    assert client.get.await_args.kwargs["headers"]["Authorization"] == "Bearer synthetic-token"

    client.post.return_value = httpx.Response(200, json={"access_token": "token-only"},
                                               request=httpx.Request("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token"))
    client.get.reset_mock()
    with patch("app.api.outlook_oauth.httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="offline credentials"):
            await outlook_oauth._exchange_code("synthetic-code", "synthetic-pkce", settings)
    client.get.assert_not_awaited()
