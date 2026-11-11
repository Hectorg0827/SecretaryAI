"""Gate 6C acceptance contracts on the REAL mounted FastAPI app.

No Supabase network, real Redis mutations, invitations or accounting writes:
the only database is an in-memory service-role stand-in. These tests complement
but do not replace live staging synthetic-identity acceptance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.jwt import create_access_token, decode_access_token
from app.main import app
from app.middleware import active_session_boundary as gate6c


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = {}
        self.max_rows = None

    def select(self, _columns, **_kwargs):
        return self

    def eq(self, name, value):
        self.filters[name] = value
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def execute(self):
        if self.store.offline:
            raise ConnectionError("Synthetic authorization database outage")
        rows = list(self.store.tables.get(self.table, []))
        for name, value in self.filters.items():
            rows = [record for record in rows if record.get(name) == value]
        if self.max_rows is not None:
            rows = rows[:self.max_rows]
        return SimpleNamespace(data=[dict(record) for record in rows])


class _Database:
    def __init__(self):
        self.offline = False
        self.tables = {
            "users": [
                {"id": "test-actor-1", "company_id": "test-tenant-a",
                 "role": "manager", "is_active": True},
                {"id": "test-finance-2", "company_id": "test-tenant-b",
                 "role": "ar_specialist", "is_active": True},
            ],
            "companies": [
                {"id": "test-tenant-a", "qb_type": None,
                 "qb_connection_status": "disconnected"},
                {"id": "test-tenant-b", "qb_type": None,
                 "qb_connection_status": "disconnected"},
            ],
        }

    def table(self, name):
        return _Query(self, name)

    def employee(self):
        return self.tables["users"][0]


def _jwt(*, identity="test-actor-1", company="test-tenant-a",
         role="manager", scope="access"):
    return create_access_token({"sub": identity, "company_id": company,
                                "role": role, "scope": scope})


def _get(client, path, token):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def actual_app(monkeypatch):
    """Exercise app.main's complete real middleware/router stack with fake DB."""
    from app.auth import jwt as auth_jwt
    from app.api import finance_workspace

    db = _Database()
    revoked = set()
    monkeypatch.setattr(
        gate6c, "get_settings",
        lambda: SimpleNamespace(secretaryai_deployment_env="staging"),
    )
    monkeypatch.setattr(gate6c, "get_db", lambda: db)
    monkeypatch.setattr(gate6c, "is_token_revoked", lambda jti: jti in revoked)
    monkeypatch.setattr(auth_jwt, "is_token_revoked", lambda jti: jti in revoked)
    # Both the session middleware AND the real finance router independently
    # enforce staging-only behavior; mirror the same environment in this test.
    monkeypatch.setattr(
        finance_workspace, "get_settings",
        lambda: SimpleNamespace(secretaryai_deployment_env="staging"),
    )

    def snapshot(_db, _company, _report_type):
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "source_read_status": "completed",
                "source_quality": "partial",
            },
        }

    monkeypatch.setattr(finance_workspace, "read_snapshot", snapshot)
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, raise_server_exceptions=False), db, revoked
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_actual_legacy_settings_route_was_accessible_before_demotion(actual_app):
    client, _, _ = actual_app
    result = _get(client, "/api/settings/integrations", _jwt())
    assert result.status_code == 200, result.text
    assert result.json()["quickbooks_online"]["connected"] is False


@pytest.mark.parametrize("path", [
    "/api/settings/integrations",
    "/auth/me",
    "/api/finance/workspace",
    "/api/team/members",
])
def test_actual_routes_block_stale_manager_after_role_change(actual_app, path):
    client, db, _ = actual_app
    old_token = _jwt()
    # This is the exact Gate 6B bypass: the session retains signed Manager,
    # but the authoritative employee is now an AR specialist.
    assert _get(client, "/api/settings/integrations", old_token).status_code == 200
    db.employee()["role"] = "ar_specialist"
    result = _get(client, path, old_token)
    assert result.status_code == 403, (path, result.text)
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("changed", [
    {"is_active": False},
    {"company_id": "test-tenant-b"},
    {"role": "viewer"},
])
def test_actual_routes_reject_disabled_moved_or_downgraded_staff(actual_app, changed):
    client, db, _ = actual_app
    old_token = _jwt()
    db.employee().update(changed)
    assert _get(client, "/api/settings/integrations", old_token).status_code == 403


def test_current_finance_employee_reaches_only_own_scope(actual_app):
    client, db, _ = actual_app
    db.employee()["role"] = "ar_specialist"
    new_token = _jwt(role="ar_specialist")
    response = _get(client, "/api/finance/workspace", new_token)
    assert response.status_code == 200, response.text
    assert response.json()["authorized_scopes"] == ["ar"]
    assert list(response.json()["sections"]) == ["ar"]
    assert _get(client, "/api/settings/integrations", new_token).status_code == 403
    # A different valid tenant's AR user cannot reach generic endpoints either.
    other = _jwt(identity="test-finance-2", company="test-tenant-b",
                 role="ar_specialist")
    assert _get(client, "/api/settings/integrations", other).status_code == 403


def test_cross_company_claim_for_existing_user_denied(actual_app):
    client, _, _ = actual_app
    assert _get(
        client, "/api/settings/integrations", _jwt(company="test-tenant-b"),
    ).status_code == 403


def test_revoked_token_cannot_enter_actual_legacy_route(actual_app):
    client, _, revoked = actual_app
    token = _jwt()
    revoked.add(decode_access_token(token)["jti"])
    assert _get(client, "/api/settings/integrations", token).status_code == 401


def test_actual_legacy_route_fails_closed_on_database_outage(actual_app):
    client, db, _ = actual_app
    db.offline = True
    result = _get(client, "/api/settings/integrations", _jwt())
    assert result.status_code == 503
    assert "quickbooks_online" not in result.text


def test_connector_and_2fa_tokens_cannot_enter_human_route(actual_app):
    client, _, _ = actual_app
    for scope in ("connector", "2fa_pending"):
        result = _get(client, "/api/settings/integrations", _jwt(scope=scope))
        assert result.status_code == 401


def test_authenticated_employee_check_cannot_be_bypassed_by_query_company(actual_app):
    client, db, _ = actual_app
    old_token = _jwt()
    db.employee()["role"] = "ar_specialist"
    result = _get(
        client, "/api/settings/integrations?company_id=test-tenant-a",
        old_token,
    )
    assert result.status_code == 403
