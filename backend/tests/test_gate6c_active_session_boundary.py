"""Gate 6C: stale user tokens must not reach any legacy authenticated route."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.middleware import active_session_boundary as gate6c
from app.middleware.finance_role_boundary import FinanceRoleBoundaryMiddleware


class FakeDB:
    def __init__(self):
        self.users = {
            "u1": {"id": "u1", "company_id": "co-a", "role": "manager", "is_active": True},
            "u2": {"id": "u2", "company_id": "co-b", "role": "ar_specialist", "is_active": True},
        }
        self.unavailable = False
        self.filters = {}

    def table(self, name):
        assert name == "users"
        self.filters = {}
        return self

    def select(self, fields):
        assert fields == "id,company_id,role,is_active"
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, count):
        assert count == 2
        return self

    def execute(self):
        if self.unavailable:
            raise ConnectionError("synthetic outage")
        matches = [row.copy() for row in self.users.values()
                   if all(row.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=matches)


def _token(role="manager", sub="u1", company="co-a", scope="access"):
    return create_access_token({
        "sub": sub, "company_id": company, "role": role, "scope": scope,
    })


def _get(client, token, path="/api/settings/integrations"):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def setup(monkeypatch):
    db = FakeDB()
    revoked = set()
    monkeypatch.setattr(gate6c, "get_settings", lambda: SimpleNamespace(secretaryai_deployment_env="staging"))
    monkeypatch.setattr(gate6c, "get_db", lambda: db)
    monkeypatch.setattr(gate6c, "is_token_revoked", lambda jti: jti in revoked)
    app = FastAPI()
    app.add_middleware(FinanceRoleBoundaryMiddleware)
    app.add_middleware(gate6c.ActiveSessionBoundaryMiddleware)

    @app.get("/api/settings/integrations")
    def integrations():
        return {"sensitive": True}

    @app.get("/api/actions/approve")
    def approve():
        return {"sensitive": True}

    @app.get("/api/finance/workspace")
    def workspace():
        return {"scoped": True}

    @app.get("/auth/me")
    def profile():
        return {"ok": True}

    @app.post("/auth/refresh")
    def refresh():
        return {"authenticate_from_body": True}

    @app.post("/api/connectors/heartbeat")
    def connector_heartbeat():
        return {"machine": True}

    @app.get("/api/connectors/status")
    def connector_status():
        return {"sensitive": True}

    return TestClient(app), db, revoked


def test_unchanged_manager_keeps_authorized_legacy_access(setup):
    client, _, _ = setup
    assert _get(client, _token()).status_code == 200


@pytest.mark.parametrize("path", [
    "/api/settings/integrations", "/api/actions/approve", "/auth/me",
])
def test_manager_demoted_to_finance_denied_on_legacy_route(setup, path):
    client, db, _ = setup
    token = _token()
    assert _get(client, token, path).status_code == 200
    db.users["u1"]["role"] = "ar_specialist"
    result = _get(client, token, path)
    assert result.status_code == 403
    assert result.headers["cache-control"] == "no-store"
    assert "sensitive" not in result.text


@pytest.mark.parametrize("mutation", [
    lambda db: db.users["u1"].update(is_active=False),
    lambda db: db.users["u1"].update(company_id="co-b"),
    lambda db: db.users.pop("u1"),
])
def test_disabled_moved_or_deleted_user_cannot_reuse_token(setup, mutation):
    client, db, _ = setup
    token = _token()
    mutation(db)
    assert _get(client, token).status_code == 403


def test_cross_company_token_claim_is_denied_even_when_user_exists(setup):
    client, _, _ = setup
    assert _get(client, _token(company="co-b")).status_code == 403


def test_finance_employee_can_only_reach_finance_workspace(setup):
    client, _, _ = setup
    token = _token(role="ar_specialist", sub="u2", company="co-b")
    assert _get(client, token, "/api/finance/workspace").status_code == 200
    assert _get(client, token).status_code == 403


def test_fresh_token_for_updated_role_gets_only_updated_permissions(setup):
    client, db, _ = setup
    db.users["u1"]["role"] = "ar_specialist"
    fresh = _token(role="ar_specialist")
    assert _get(client, fresh, "/api/finance/workspace").status_code == 200
    assert _get(client, fresh).status_code == 403


def test_revocation_denies_legacy_access(setup):
    from app.auth.jwt import decode_access_token
    client, _, revoked = setup
    token = _token()
    revoked.add(decode_access_token(token)["jti"])
    assert _get(client, token).status_code == 401


def test_database_outage_fails_closed_without_sensitive_response(setup):
    client, db, _ = setup
    db.unavailable = True
    response = _get(client, _token())
    assert response.status_code == 503
    assert "sensitive" not in response.text


def test_revocation_outage_fails_closed(setup, monkeypatch):
    client, _, _ = setup
    def offline(_jti):
        raise RuntimeError("Redis unavailable")
    monkeypatch.setattr(gate6c, "is_token_revoked", offline)
    assert _get(client, _token()).status_code == 503


def test_invalid_and_special_purpose_tokens_rejected(setup):
    client, _, _ = setup
    assert _get(client, "tampered").status_code == 401
    assert _get(client, _token(scope="2fa_pending")).status_code == 401
    assert _get(client, _token(scope="connector")).status_code == 401


def test_connector_token_only_reaches_machine_routes(setup):
    client, _, _ = setup
    token = _token(scope="connector")
    assert client.post("/api/connectors/heartbeat", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert _get(client, token, "/api/connectors/status").status_code == 401


def test_refresh_bootstrap_not_blocked_by_stale_authorization_header(setup):
    client, db, _ = setup
    token = _token()
    db.users["u1"]["role"] = "ar_specialist"
    # The actual refresh handler independently rechecks the DB and token JTI.
    result = client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200


def test_nonstaging_is_untouched(setup, monkeypatch):
    client, db, _ = setup
    monkeypatch.setattr(gate6c, "get_settings", lambda: SimpleNamespace(secretaryai_deployment_env="production"))
    db.users["u1"]["role"] = "ar_specialist"
    # Staging-only change: production's existing middleware is unchanged.
    assert _get(client, _token()).status_code == 200
