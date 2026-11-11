"""Signed finance sessions must not enter historical generic authenticated APIs."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.middleware.finance_role_boundary import (
    FINANCE_ALLOWED_ENDPOINTS, FINANCE_EMPLOYEE_ROLES, FinanceRoleBoundaryMiddleware,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(FinanceRoleBoundaryMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE", "PATCH"])
    async def generic_route(path: str):
        return {"reached": path}

    return TestClient(app)


def _session(role: str) -> str:
    return create_access_token({
        "sub": "employee", "company_id": "tenant", "role": role,
    }, expires_delta=timedelta(minutes=5))


@pytest.mark.parametrize("role", sorted(FINANCE_EMPLOYEE_ROLES))
@pytest.mark.parametrize("path,method", [
    ("/api/accounts", "GET"),
    ("/api/dashboard", "GET"),
    ("/api/agent/run", "POST"),
    ("/api/settings", "PATCH"),
    ("/api/connectors/sync", "POST"),
    ("/api/billing/checkout", "POST"),
    ("/api/team/members", "GET"),
    ("/auth/qbo/connect", "GET"),
    ("/auth/2fa/disable", "DELETE"),
    ("/api/finance/workspace/other", "GET"),
    ("/api/finance/workspace", "POST"),
])
def test_finance_session_denied_on_generic_routes(client, role, path, method):
    result = client.request(method, path, headers={"Authorization": f"bEaReR {_session(role)}"})
    assert result.status_code == 403
    assert result.headers["cache-control"] == "no-store"
    assert "reached" not in result.json()


@pytest.mark.parametrize("role", sorted(FINANCE_EMPLOYEE_ROLES))
@pytest.mark.parametrize("method,path", sorted(FINANCE_ALLOWED_ENDPOINTS))
def test_finance_session_reaches_only_explicit_routes(client, role, method, path):
    result = client.request(method, path, headers={"Authorization": f"Bearer {_session(role)}"})
    assert result.status_code == 200


@pytest.mark.parametrize("role", ["owner", "manager", "back_office", "viewer"])
def test_existing_roles_unaffected(client, role):
    result = client.get("/api/dashboard", headers={"Authorization": f"Bearer {_session(role)}"})
    assert result.status_code == 200


def test_missing_or_tampered_token_does_not_create_unauthenticated_authority(client):
    # Middleware is an additional role boundary; endpoint auth remains the
    # authority over invalid tokens and anonymous requests.
    assert client.get("/api/dashboard").status_code == 200
    token = _session("ar_specialist")
    header, payload, signature = token.split(".")
    altered = f"{header}.{payload}.{'a' if signature[0] != 'a' else 'b'}{signature[1:]}"
    assert client.get("/api/dashboard", headers={"Authorization": f"Bearer {altered}"}).status_code == 200


def test_non_access_scoped_token_is_left_to_route_auth(client):
    token = create_access_token({
        "sub": "employee", "company_id": "tenant", "role": "ar_specialist",
        "scope": "connector",
    })
    assert client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"}).status_code == 200
