"""Client-supplied PO/cost inputs cannot advance authoritative procurement state."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user


def _client(role="manager"):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": "tenant-1"}])
    )
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "user-1", "company_id": "tenant-1", "role": role,
    }
    app.dependency_overrides[logistics.get_db] = lambda: db
    return TestClient(app), db


def _cost(fob=20, **changes):
    value = {
        "po_number": "PO-100", "sku": "SKU-1", "supplier_id": "supplier-1",
        "cases": 120, "fob_cost_per_case": fob,
        "ocean_freight_per_case": 2.0,
    }
    value.update(changes)
    return value


def test_unverified_client_approval_cannot_generate_or_persist_po():
    client, db = _client()
    response = client.post("/api/logistics/po/generate", json={
        "approved_packages": [{"sku": "SKU-1", "approved": True,
                               "recommended_qty": 120, "urgency": "red"}],
        "supplier_configs": {"supplier-1": {}},
    })
    assert response.status_code == 409, response.text
    assert "approval" in response.json()["detail"].lower()
    db.table.assert_not_called()
    db.rpc.assert_not_called()


def test_unverified_cost_variance_is_preview_only_and_does_not_advance_stage():
    client, db = _client()
    response = client.post("/api/logistics/cost/reconcile", json={
        "current": _cost(fob=22), "previous": _cost(fob=20),
        "selling_price_per_case": 38,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["variance_count"] >= 1
    assert result["status"] == "analysis_only"
    assert result["landed_cost_verified"] is False
    assert result["accounting_reconciled"] is False
    assert result["pipeline_status_changed"] is False
    assert result["requires_independent_invoice_review"] is True
    db.table.assert_called_once_with("companies")
    db.table.return_value.upsert.assert_not_called()
    db.rpc.assert_not_called()


def test_cost_compare_rejects_unmatched_sku_and_invalid_quantities():
    client, db = _client()
    mismatch = client.post("/api/logistics/cost/reconcile", json={
        "current": _cost(), "previous": _cost(sku="DIFFERENT"),
    })
    assert mismatch.status_code == 422

    negative = client.post("/api/logistics/cost/reconcile", json={
        "current": _cost(cases=-1), "previous": _cost(),
    })
    assert negative.status_code == 422
    db.table.assert_not_called()


def test_insufficient_cost_source_identity_cannot_be_marked_reconciled():
    client, db = _client()
    response = client.post("/api/logistics/cost/reconcile", json={
        "current": {"sku": "SKU-1", "cases": 10}, "previous": _cost(),
    })
    assert response.status_code == 422
    db.table.assert_not_called()
