"""Price-book listing: company isolation, fail-closed sources, no writes."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app
from pricing_auth_testkit import RoleQuery


class Query:
    def __init__(self, rows, *, fail=False):
        self.rows = rows
        self.fail = fail
        self.filters = []
        self.start = None
        self.end = None

    def select(self, columns):
        assert "company_id" not in columns  # response projection never leaks tenant column
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        assert field == "effective_from" and desc
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("provider failure: don't leak this")
        tenant = next(value for key, value in self.filters if key == "company_id")
        rows = [r for r in self.rows if r["company_id"] == tenant]
        return SimpleNamespace(data=rows[self.start:self.end + 1])


class Db:
    def __init__(self, rows, *, identity="operator-1", company="tenant-a",
                 role="owner", active=True, fail=False, role_fail=False):
        self.query = Query(rows, fail=fail)
        self.role_query = RoleQuery(identity, company, role, active=active, fail=role_fail)

    def table(self, name):
        if name == "users":
            return self.role_query
        assert name == "pricing_price_book"
        return self.query


@pytest.fixture
def client():
    def build(*, role="owner", company_id="tenant-a", rows=None, fail=False,
              current_role=None, active=True, role_fail=False):
        db = Db(rows or [], company=company_id, role=current_role or role,
                active=active, fail=fail, role_fail=role_fail)
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "operator-1", "company_id": company_id, "role": role,
        }
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app), db
    yield build
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_tenant_separation_price_history_and_no_write(client):
    sample = {
        "sku": "test-sku", "package_label": "12x750", "market_code": "NY",
        "channel": "distributor", "customer_ref": "", "currency": "USD",
        "list_price_per_case": "30.000000", "effective_from": "2026-11-01",
        "effective_to": None, "revision": 1, "status": "draft",
        "cost_snapshot_id": None, "evidence_ref": "", "approved_at": None,
    }
    web, db = client(rows=[
        {"company_id": "tenant-a", "id": "a", **sample},
        {"company_id": "tenant-b", "id": "b", **sample},
    ])
    response = web.get("/api/insights/price-book")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "connected_data"
    assert payload["status"] == "analysis_only"
    assert payload["price_changes_allowed"] is False
    assert payload["cost_source_certified"] is False
    assert [row["id"] for row in payload["items"]] == ["a"]
    assert all("company_id" not in row for row in payload["items"])
    assert db.query.filters == [("company_id", "tenant-a")]


def test_empty_book_does_not_imply_prices_are_zero(client):
    web, _ = client(rows=[])
    payload = web.get("/api/insights/price-book").json()
    assert payload["book_state"] == "unpopulated"
    assert payload["items"] == []


def test_source_outage_fails_closed_and_sanitizes_error(client):
    web, _ = client(fail=True)
    response = web.get("/api/insights/price-book")
    assert response.status_code == 503
    assert "provider failure" not in response.text


@pytest.mark.parametrize("role", ["sales_rep", "viewer", "back_office", "ap_specialist"])
def test_unauthorized_roles_cannot_read_confidential_prices(client, role):
    web, _ = client(role=role)
    assert web.get("/api/insights/price-book").status_code == 403


def test_page_bounds_are_enforced(client):
    web, _ = client()
    assert web.get("/api/insights/price-book?limit=1000").status_code == 422
    assert web.get("/api/insights/price-book?offset=-1").status_code == 422


def test_pricing_billbacks_has_scoped_read_only_access(client):
    web, _ = client(role="pricing_billbacks")
    resp = web.get("/api/insights/price-book")
    assert resp.status_code == 200
    assert resp.json()["price_changes_allowed"] is False


@pytest.mark.parametrize("change", [
    {"current_role": "sales_rep"},
    {"current_role": "manager"},
    {"active": False},
])
def test_stale_or_revoked_role_fails_closed(client, change):
    web, _ = client(role="pricing_billbacks", **change)
    assert web.get("/api/insights/price-book").status_code == 403


def test_role_db_outage_does_not_show_pricing_data(client):
    web, _ = client(role="pricing_billbacks", role_fail=True)
    response = web.get("/api/insights/price-book")
    assert response.status_code == 503
    assert "Synthetic role database outage" not in response.text
