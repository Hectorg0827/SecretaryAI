"""Read-only source integration: no broad inventory grants or false valuation claims."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app
from pricing_auth_testkit import RoleDB


ASSESSMENT_ID = "123e4567-e89b-12d3-a456-426614174001"
PO_ID = "123e4567-e89b-12d3-a456-426614174002"
RECEIPT_ID = "123e4567-e89b-12d3-a456-426614174003"
URL = f"/api/insights/price-book/sources/assessments/{ASSESSMENT_ID}"


class Rows:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name
        self.filters = {}
        self.list_filter = {}
        self.selected = None
        self.max_records = None

    def select(self, columns):
        self.selected = columns
        return self

    def eq(self, key, value):
        self.filters[key] = str(value)
        return self

    def in_(self, key, values):
        self.list_filter[key] = {str(v) for v in values}
        return self

    def limit(self, count):
        self.max_records = count
        return self

    def execute(self):
        self.owner.read_log.append((self.name, dict(self.filters)))
        result = []
        for row in self.owner.contents.get(self.name, []):
            if any(str(row.get(k)) != v for k, v in self.filters.items()):
                continue
            if any(str(row.get(k)) not in vals for k, vals in self.list_filter.items()):
                continue
            result.append(row)
        if self.max_records:
            result = result[:self.max_records]
        return SimpleNamespace(data=result)


class SourceDB(RoleDB):
    def __init__(self, *, role="pricing_billbacks", active=True,
                 assessment_tenant="tenant-a", corrupt=False, received=True):
        super().__init__(identity="staff-user", company="tenant-a", role=role, active=active)
        self.read_log = []
        self.contents = {
            "purchasing_cost_assessments": [{
                "id": ASSESSMENT_ID, "company_id": assessment_tenant, "po_id": PO_ID,
                "assessment_key": "draft-cost-1", "currency": "USD",
                "fx_rate_to_usd": "1", "landed_cost_total": "40",
                "lines": json.dumps([{
                    "sku": "BEER", "qty_ordered": "100",
                    "unit_cost": "-1" if corrupt else "18",
                }]),
                "assertion_status": "provisional", "created_by": "staff-user",
                "created_at": "2026-11-01T12:00:00Z",
            }],
            "purchasing_orders": [{
                "id": PO_ID, "company_id": assessment_tenant,
                "po_number": "PO-77", "supplier_ref": "Supplier-1", "status": "approved",
            }],
            "purchasing_order_lines": [{
                "company_id": assessment_tenant,
                "po_id": PO_ID, "sku": "BEER", "qty_ordered": "100",
                "qty_received": "100",
            }],
            "warehouse_receipt_documents": [{
                "company_id": assessment_tenant,
                "id": RECEIPT_ID, "po_id": PO_ID,
            }] if received else [],
            "warehouse_receipt_lines": [{
                "company_id": assessment_tenant, "receipt_id": RECEIPT_ID,
                "sku": "BEER", "accepted_qty": "80",
                "damaged_qty": "10", "quarantined_qty": "10",
            }] if received else [],
        }

    def table(self, name):
        if name == "users":
            return super().table(name)
        assert name in self.contents, name
        return Rows(self, name)


@pytest.fixture
def client():
    def make(role="pricing_billbacks", *, db=None):
        db = db or SourceDB(role=role)
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "staff-user", "role": role, "company_id": "tenant-a",
        }
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app), db
    yield make
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_pricing_employee_reads_received_accepted_cases_without_writes(client):
    web, db = client()
    response = web.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["actions_executed"] is False
    assert body["valuation_verified"] is False
    assert body["accounting_reconciled"] is False
    assert body["preallocated_landed_cost_not_applied"] is True
    assert body["landed_cost_total"] == "40.00"
    assert body["currency"] == "USD"
    assert body["po_number"] == "PO-77"
    assert body["lines"][0]["sku"] == "BEER"
    assert body["lines"][0]["unit_cost"] == "18"
    assert body["lines"][0]["cases"]["available"] == "80"
    assert body["lines"][0]["cases"]["damaged"] == "10"
    assert body["lines"][0]["cases"]["quarantined"] == "10"
    assert response.headers["cache-control"] == "private, no-store"
    assert all(filters.get("company_id") == "tenant-a" for name, filters in db.read_log)


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "ap_specialist", "viewer"])
def test_unrelated_roles_cannot_read_supplier_costs(client, role):
    web, db = client(role, db=SourceDB(role=role))
    assert web.get(URL).status_code == 403
    assert not db.read_log


def test_wrong_tenant_source_is_not_visible(client):
    db = SourceDB(assessment_tenant="tenant-b")
    web, _ = client(db=db)
    assert web.get(URL).status_code == 404


def test_uncertain_physical_receipts_are_not_synthetic_available_stock(client):
    web, _ = client(db=SourceDB(received=False))
    response = web.get(URL)
    assert response.status_code == 200
    assert response.json()["lines"][0]["cases"]["available"] == "0"


def test_invalid_supplier_finance_amounts_are_not_published(client):
    web, _ = client(db=SourceDB(corrupt=True))
    response = web.get(URL)
    assert response.status_code == 503
    assert "18" not in response.text


def test_expired_pricing_staff_role_cannot_read_purchase_source(client):
    web, _ = client(db=SourceDB(role="sales_rep"))
    assert web.get(URL).status_code == 403
