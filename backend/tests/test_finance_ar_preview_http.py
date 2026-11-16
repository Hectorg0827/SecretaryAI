"""HTTP-level acceptance for the feature-disabled, owner-only AR preview.

Signed synthetic tokens and in-memory company/role fixtures. No QBO calls,
hosted DB reads or financial writes. Confirms FastAPI dependencies, route
registration and response headers instead of only invoking the Python handler.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.jwt import create_access_token
from app.main import app

PATH = "/api/finance/ar-payment-preview"
COMPANY = "synthetic-company-A"
IDENTITY = "synthetic-owner-A"


class ScopedUsers:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, _):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, n):
        assert n == 2
        return self

    def execute(self):
        selected = [
            row for row in self.rows
            if all(row.get(k) == v for k, v in self.filters)
        ]
        return SimpleNamespace(data=selected)


class ScopedDB:
    def __init__(self, row):
        self.query = ScopedUsers([row] if row is not None else [])

    def table(self, name):
        assert name == "users"
        return self.query


def token(role="owner", company=COMPANY):
    return create_access_token({
        "sub": IDENTITY, "company_id": company, "role": role,
    })


def mock_settings(enabled=True, environment="staging"):
    return SimpleNamespace(
        secretaryai_deployment_env=environment,
        secretaryai_ar_payment_preview_enabled=enabled,
    )


def sample_result():
    return {
        "status": "aligned_not_certified",
        "reason": None,
        "invoice_count": 2, "linked_invoice_count": 1,
        "variance_invoice_count": 0,
        "partially_paid_invoice_count": 1,
        "unapplied_payment_count": 1,
        "accounting_reconciled": False,
        "financial_write_authorized": False,
    }


@pytest.fixture
def http_route():
    # The ordinary test subscription override from conftest remains intact.
    # Only get_db is replaced; real RBAC and JWT dependency resolution run.
    def call(*, role="owner", db_role=None, company=COMPANY, stored_company=None,
             active=True, enabled=True, environment="staging", source=None):
        record = {
            "id": IDENTITY,
            "company_id": stored_company or COMPANY,
            "role": db_role or role,
            "is_active": active,
        }
        db = ScopedDB(record)
        adapter = SimpleNamespace(
            get_qbo_ar_payment_match_preview_read_only=AsyncMock(
                return_value=sample_result()
                if source is None else source
            ),
            create_payment=AsyncMock(),
        )
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.auth.jwt.is_token_revoked", return_value=False), \
                 patch("app.api.finance_ar_preview.get_settings",
                       return_value=mock_settings(enabled, environment)), \
                 patch("app.api.finance_ar_preview.get_adapter",
                       return_value=adapter) as make_adapter:
                with TestClient(app) as client:
                    response = client.get(
                        PATH, headers={"Authorization": f"Bearer {token(role, company)}"},
                    )
            return response, adapter, make_adapter, db
        finally:
            app.dependency_overrides.pop(get_db, None)
    return call


def test_signed_owner_get_receives_only_aggregate_evidence(http_route):
    response, adapter, build, db = http_route()
    assert response.status_code == 200, response.text
    assert response.headers.get("cache-control") == "private, no-store"
    assert response.json() == {
        **sample_result(),
        "source_system": "qbo_sandbox", "source_quality": "partial",
        "as_of": response.json()["as_of"],
    }
    assert "synthetic-owner" not in response.text
    assert "synthetic-company" not in response.text
    assert ("company_id", COMPANY) in db.query.filters
    assert ("id", IDENTITY) in db.query.filters
    build.assert_called_once()
    adapter.get_qbo_ar_payment_match_preview_read_only.assert_awaited_once()
    adapter.create_payment.assert_not_awaited()


@pytest.mark.parametrize("enabled,environment", [
    (False, "staging"), (True, "production"), (True, "local"),
])
def test_http_route_off_when_flag_or_deployment_is_not_approved(
    http_route, enabled, environment,
):
    response, adapter, build, _ = http_route(
        enabled=enabled, environment=environment,
    )
    assert response.status_code == 404
    assert response.headers.get("cache-control") == "private, no-store"
    build.assert_not_called()
    adapter.get_qbo_ar_payment_match_preview_read_only.assert_not_awaited()


@pytest.mark.parametrize("role", [
    "viewer", "sales_rep", "manager", "ar_specialist",
    "ap_specialist", "finance_controller", "pricing_billbacks",
])
def test_http_nonowners_cannot_retrieve_payment_evidence(http_route, role):
    response, adapter, build, _ = http_route(role=role)
    assert response.status_code == 403
    assert response.headers.get("cache-control", "").startswith("no-store") or (
        response.headers.get("cache-control") == "private, no-store"
    )
    build.assert_not_called()
    adapter.get_qbo_ar_payment_match_preview_read_only.assert_not_awaited()


@pytest.mark.parametrize("override", [
    {"db_role": "manager"},
    {"stored_company": "synthetic-other-company"},
    {"active": False},
])
def test_http_db_role_or_tenant_drift_denied_before_provider(http_route, override):
    response, adapter, build, _ = http_route(**override)
    assert response.status_code == 403
    assert response.headers.get("cache-control") == "private, no-store"
    build.assert_not_called()
    adapter.get_qbo_ar_payment_match_preview_read_only.assert_not_awaited()


def test_http_invalid_unsigned_token_never_exposes_financial_evidence():
    adapter = SimpleNamespace(
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(),
    )
    with patch("app.api.finance_ar_preview.get_settings",
               return_value=mock_settings()), \
         patch("app.api.finance_ar_preview.get_adapter",
               return_value=adapter):
        with TestClient(app) as client:
            r = client.get(PATH, headers={"Authorization": "Bearer forged.jwt.token"})
    assert r.status_code == 401
    adapter.get_qbo_ar_payment_match_preview_read_only.assert_not_awaited()


def test_http_provider_outage_is_generic_503_not_zero_balance(http_route):
    # The in-memory fixture returns data with a forbidden field to exercise
    # the same validation failure path as a malformed QBO projection.
    data = {**sample_result(), "customer_name": "SECRET-CUSTOMER"}
    r, adapter, _, _ = http_route(source=data)
    assert r.status_code == 503
    assert r.headers.get("cache-control") == "private, no-store"
    assert "SECRET-CUSTOMER" not in r.text
    assert "0 balance" not in r.text
    adapter.create_payment.assert_not_awaited()
