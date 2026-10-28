"""HTTP regression coverage for the public reorder API -> engine contract.

Unlike isolated ReorderEngine tests, these exercise the actual FastAPI
serializer and tenant-scoped company configuration lookup. No external IO.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user


def _client():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": "tenant-1"}])
    )
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "employee-1", "company_id": "tenant-1", "role": "manager"
    }
    app.dependency_overrides[logistics.get_db] = lambda: db
    return TestClient(app), db


def _sku(**overrides):
    data = {
        "sku": "CASE-001", "name": "Case Product", "supplier_id": "sup-1",
        "supply_chain_type": "domestic", "stock_on_hand": 15,
        "cases_per_pallet": 12, "daily_sales": [10.0] * 30,
        "lead_time_days": [6.0, 8.0, 7.0, 7.0],
    }
    data.update(overrides)
    return data


def test_http_reorder_maps_histories_and_serializes_real_recommendation():
    client, db = _client()
    response = client.post("/api/logistics/reorder/evaluate", json={"skus": [_sku()]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == body["red_count"] == 1
    decision = body["reorder_queue"][0]
    assert decision["sku"] == "CASE-001"
    assert decision["urgency"] == "red"
    assert decision["recommended_qty"] > 0
    assert decision["lead_time_p80_days"] >= 7
    db.table.assert_called_once_with("companies")
    db.table.return_value.select.return_value.eq.assert_called_once_with(
        "id", "tenant-1"
    )


def test_http_reorder_distinguishes_comfortable_stock_from_missing_history():
    client, _ = _client()
    safe = client.post("/api/logistics/reorder/evaluate", json={
        "skus": [_sku(stock_on_hand=10000)]
    })
    assert safe.status_code == 200, safe.text
    assert safe.json()["count"] == 0

    missing_history = client.post("/api/logistics/reorder/evaluate", json={
        "skus": [_sku(daily_sales=[])]
    })
    assert missing_history.status_code == 422


def test_http_reorder_normalizes_seasonal_indices_and_rejects_bad_supply_type():
    client, _ = _client()
    seasonal = client.post("/api/logistics/reorder/evaluate", json={
        "skus": [_sku(seasonal_indices={str(i): 1.0 for i in range(1, 53)})]
    })
    assert seasonal.status_code == 200, seasonal.text
    assert seasonal.json()["count"] == 1

    invalid = client.post("/api/logistics/reorder/evaluate", json={
        "skus": [_sku(supply_chain_type="unknown")]
    })
    assert invalid.status_code == 422
