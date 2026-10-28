"""Read/analysis endpoints used by the Logistics web screens.

All database access is a MagicMock; no provider, ledger or network is used.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user


def _client(role: str = "manager"):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": "test-tenant"}])
    )
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "staff-1", "company_id": "test-tenant", "role": role,
    }
    app.dependency_overrides[logistics.get_db] = lambda: db
    return TestClient(app), db


def _history(days: int, units=lambda i: float(5 + (i % 7))):
    start = date.today() - timedelta(days=days)
    return start, [
        {"date": (start + timedelta(days=i)).isoformat(), "units": units(i)}
        for i in range(days)
    ]


# ─── Seasonal indices ────────────────────────────────────────────────────────


def test_seasonal_indices_accept_a_full_daily_history():
    client, db = _client()
    start, history = _history(200)
    response = client.post("/api/logistics/learning/seasonal-indices",
                           json={"sku": "CASE-A", "daily_sales": history})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["modeled_data_quality"] == "provisional"
    assert body["index_count"] == 52
    assert len(body["indices"]) == 52
    assert body["week_alignment"] == "counted_from_first_sales_date"
    assert body["week_start_dates"][0] == start.isoformat()
    assert body["week_start_dates"][1] == (start + timedelta(days=7)).isoformat()
    assert body["days_available"] == 200
    assert body["historical_learning_persisted"] is False
    db.rpc.assert_not_called()


def test_seasonal_indices_never_present_placeholder_multipliers():
    client, _ = _client()
    _, short = _history(30)
    response = client.post("/api/logistics/learning/seasonal-indices",
                           json={"sku": "CASE-A", "daily_sales": short})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["modeled_data_quality"] == "insufficient_data"
    assert body["indices"] == [] and body["index_count"] == 0
    assert body["week_start_dates"] == []

    _, zeros = _history(200, units=lambda i: 0.0)
    response = client.post("/api/logistics/learning/seasonal-indices",
                           json={"sku": "CASE-A", "daily_sales": zeros})
    assert response.status_code == 200, response.text
    assert response.json()["modeled_data_quality"] == "no_demand"
    assert response.json()["indices"] == []


def test_seasonal_indices_reject_calendar_gaps_before_company_read():
    client, db = _client()
    _, history = _history(200)
    del history[50]
    response = client.post("/api/logistics/learning/seasonal-indices",
                           json={"sku": "CASE-A", "daily_sales": history})
    assert response.status_code == 422
    assert "every calendar day" in response.text
    db.table.assert_not_called()


# ─── Legacy pipeline status ──────────────────────────────────────────────────


def _pipeline_query(db):
    return (db.table.return_value.select.return_value.eq.return_value
            .eq.return_value.maybe_single.return_value.execute)


def test_pipeline_missing_cycle_is_404_without_echoing_input():
    client, db = _client()
    _pipeline_query(db).return_value = None  # postgrest maybe_single() on 0 rows
    response = client.get("/api/logistics/pipeline/PO-<script>")
    assert response.status_code == 404
    assert response.json()["detail"] == "Procurement cycle not found"


def test_pipeline_read_failure_is_503_not_500():
    client, db = _client()
    _pipeline_query(db).side_effect = RuntimeError("connection reset with secrets")
    response = client.get("/api/logistics/pipeline/PO-1")
    assert response.status_code == 503
    assert "secrets" not in response.text


def test_pipeline_found_cycle_is_labelled_unverified():
    client, db = _client()
    _pipeline_query(db).return_value = SimpleNamespace(data={
        "po_number": "PO-1", "current_stage": "in_transit", "supplier_id": "SUP-1",
    })
    response = client.get("/api/logistics/pipeline/PO-1")
    assert response.status_code == 200
    body = response.json()
    assert body["current_stage"] == "in_transit"
    assert body["stage_verified"] is False
    assert body["authoritative_purchasing_state"] is False


# ─── Warehouse picker ────────────────────────────────────────────────────────


def _warehouse_query(db):
    return (db.table.return_value.select.return_value.eq.return_value
            .order.return_value.limit.return_value.execute)


def test_warehouses_are_tenant_scoped_reads():
    client, db = _client("back_office")
    rows = [{"id": "5b0c6f0e-8f5c-4c47-9f39-6a1e1f7a4c11", "code": "MAIN", "name": "Main"}]
    _warehouse_query(db).return_value = SimpleNamespace(data=rows)
    response = client.get("/api/logistics/warehouses")
    assert response.status_code == 200, response.text
    assert response.json() == {"warehouses": rows, "count": 1}
    db.table.assert_called_with("stock_warehouses")
    db.table.return_value.select.return_value.eq.assert_called_with("company_id", "test-tenant")
    db.rpc.assert_not_called()


def test_warehouses_fail_closed():
    client, db = _client()
    _warehouse_query(db).side_effect = RuntimeError("down")
    assert client.get("/api/logistics/warehouses").status_code == 503
    _warehouse_query(db).side_effect = None
    _warehouse_query(db).return_value = SimpleNamespace(data=[{"id": str(i)} for i in range(201)])
    assert client.get("/api/logistics/warehouses").status_code == 503


def test_warehouses_reject_non_purchasing_roles():
    client, db = _client("sales_rep")
    response = client.get("/api/logistics/warehouses")
    assert response.status_code == 403
    db.table.assert_not_called()
