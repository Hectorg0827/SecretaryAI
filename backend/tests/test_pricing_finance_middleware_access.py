"""Pricing role must reach only explicit analysis endpoints through finance gate.

Synthetic signed JWTs exercise middleware route allowlisting, not the real
live-user / tenant database check that runs afterward in staging.
"""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.middleware.finance_role_boundary import (
    ASSESSMENT_READ_PREFIX,
    FINANCE_ALLOWED_ENDPOINTS,
    PRICING_BILLBACKS_ENDPOINTS,
    FinanceRoleBoundaryMiddleware,
)

UUID = "123e4567-e89b-12d3-a456-426614174001"


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(FinanceRoleBoundaryMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "PUT"])
    async def generic(path: str):
        return {"reached": path}

    return TestClient(app)


def request(client, role, method, path):
    token = create_access_token(
        {"sub": "employee", "company_id": "tenant-a", "role": role},
        expires_delta=timedelta(minutes=5),
    )
    return client.request(method, path, headers={"Authorization": "Bearer " + token})


@pytest.mark.parametrize("method,path", sorted(PRICING_BILLBACKS_ENDPOINTS))
def test_pricing_staff_only_exact_scoped_routes(client, method, path):
    assert request(client, "pricing_billbacks", method, path).status_code == 200


@pytest.mark.parametrize("role", ("finance_controller", "ar_specialist", "ap_specialist"))
@pytest.mark.parametrize("method,path", sorted(PRICING_BILLBACKS_ENDPOINTS))
def test_other_finance_staff_cannot_reach_pricing_endpoints(client, role, method, path):
    response = request(client, role, method, path)
    assert response.status_code == 403
    assert response.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("raw_id", (UUID, UUID.upper()))
def test_pricing_staff_can_read_canonical_assessment_uuid(client, raw_id):
    assert request(client, "pricing_billbacks", "GET",
                   ASSESSMENT_READ_PREFIX + raw_id).status_code == 200


@pytest.mark.parametrize("role", ("finance_controller", "ar_specialist", "ap_specialist"))
def test_other_finance_roles_denied_assessment_read(client, role):
    assert request(client, role, "GET", ASSESSMENT_READ_PREFIX + UUID).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/insights"),
    ("GET", "/api/insights/price-waterfall"),
    ("GET", "/api/insights/price-book/sources"),
    ("GET", "/api/insights/price-book/sources/assessments"),
    ("GET", ASSESSMENT_READ_PREFIX + "not-a-uuid"),
    ("GET", ASSESSMENT_READ_PREFIX + UUID.replace("-", "")),
    ("GET", ASSESSMENT_READ_PREFIX + UUID + "/other"),
    ("POST", ASSESSMENT_READ_PREFIX + UUID),
    ("POST", "/api/insights/price-book"),
    ("PUT", "/api/insights/price-book"),
    ("DELETE", "/api/insights/price-book"),
    ("PATCH", "/api/insights/price-book"),
    ("GET", "/api/inventory"),
    ("GET", "/api/logistics/orders"),
    ("POST", "/api/logistics/warehouses"),
    ("GET", "/auth/qbo/connect"),
    ("POST", "/api/connectors/sync"),
    ("GET", "/api/team/members"),
    ("POST", "/api/billing/checkout"),
])
def test_pricing_staff_cannot_reach_other_apis_or_write(client, method, path):
    response = request(client, "pricing_billbacks", method, path)
    assert response.status_code == 403
    assert response.headers.get("cache-control") == "no-store"
    assert "reached" not in response.json()


def test_query_parameters_do_not_change_allowed_path(client):
    assert request(client, "pricing_billbacks", "GET",
                   "/api/insights/price-book?limit=100").status_code == 200


@pytest.mark.parametrize("method,path", sorted(FINANCE_ALLOWED_ENDPOINTS))
@pytest.mark.parametrize("role", ("finance_controller", "ar_specialist",
                                   "ap_specialist", "pricing_billbacks"))
def test_existing_finance_profile_and_workspace_allowlist_unchanged(client, role, method, path):
    assert request(client, role, method, path).status_code == 200


def test_owner_and_manager_remain_unaffected(client):
    for role in ("owner", "manager"):
        assert request(client, role, "GET", "/api/logistics/orders").status_code == 200
