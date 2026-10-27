"""Synthetic nine-stage HTTP chain must not impersonate operational E2E proof.

No real provider, ledger, supplier, inbox or notification is contacted. The
mock database intentionally reveals route-side effects but cannot certify
Postgres atomicity or durable process replay.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user
from app.intelligence.wholesale_distribution.logistics.learning import LearningEngine


def _client():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": "test-tenant"}])
    )
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
        SimpleNamespace(data={
            "po_number": "PO-1", "current_stage": "warehouse_received",
            "supplier_id": "SUP-1", "total_cases": 40,
        })
    )
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "staff-1", "company_id": "test-tenant", "role": "manager",
    }
    app.dependency_overrides[logistics.get_db] = lambda: db
    return TestClient(app), db


def _sku():
    return {
        "sku": "CASE-A", "name": "Sample Case", "supplier_id": "SUP-1",
        "supply_chain_type": "domestic", "stock_on_hand": 2,
        "daily_sales": [8.0] * 35,
        "lead_time_days": [5.0, 6.0, 7.0, 6.0, 5.0],
    }


def _cost(price):
    return {
        "po_number": "PO-1", "sku": "CASE-A", "supplier_id": "SUP-1",
        "cases": 40, "fob_cost_per_case": price,
    }


def _receipt():
    return {
        "booking_id": "BK-1", "po_number": "PO-1",
        "line_items": [{"sku": "CASE-A", "qty_ordered": 40,
                        "qty_received": 38, "unit": "case",
                        "condition_notes": "2 short"}],
    }


def test_all_legacy_pipeline_http_handoffs_are_previews_not_verified_e2e():
    client, db = _client()
    demand = client.post("/api/logistics/reorder/evaluate", json={"skus": [_sku()]})
    assert demand.status_code == 200, demand.text
    assert demand.json()["count"] == 1
    assert demand.json()["source_data_verified"] is False
    assert demand.json()["po_creation_authorized"] is False

    forged = client.post("/api/logistics/po/generate", json={
        "approved_packages": [{"sku": "CASE-A", "approved": True}],
        "supplier_configs": {},
    })
    assert forged.status_code == 409

    reply = client.post("/api/logistics/vendor/parse-response", json={
        "po_number": "PO-1", "supplier_id": "SUP-1",
        "email_subject": "Purchase order confirmed",
        "email_body": "PO-1 confirmed. Shipping next Tuesday.",
    })
    assert reply.status_code == 200, reply.text
    assert reply.json()["message_verified"] is False
    assert reply.json()["pipeline_status_changed"] is False

    freight = client.post("/api/logistics/freight/parse-invoice", json={
        "booking_id": "BK-1", "total_cases": 40,
        "invoice_text": "Ocean Freight $300\nFuel Surcharge $40",
    })
    assert freight.status_code == 200, freight.text
    assert freight.json()["invoice_verified"] is False

    customs = client.post("/api/logistics/customs/parse-broker-email", json={
        "booking_id": "BK-1", "subject": "Entry filed",
        "body": "Entry filed, awaiting inspection.",
    })
    assert customs.status_code == 200, customs.text
    assert customs.json()["customs_event_verified"] is False

    receipt = client.post("/api/logistics/receipt/process", json=_receipt())
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "analysis_only"
    assert receipt.json()["inventory_posted"] is False
    assert receipt.json()["receipt_verified"] is False
    assert receipt.json()["alert"] is None

    costs = client.post("/api/logistics/cost/reconcile", json={
        "current": _cost(12), "previous": _cost(10),
        "selling_price_per_case": 20,
    })
    assert costs.status_code == 200, costs.text
    assert costs.json()["pipeline_status_changed"] is False
    assert costs.json()["landed_cost_verified"] is False

    learning = client.get("/api/logistics/report/monthly")
    assert learning.status_code == 200, learning.text
    assert learning.json()["status"] == "insufficient_verified_history"
    assert learning.json()["supplier_learning_persisted"] is False
    assert "on track" not in learning.json()["recommendation"].lower()

    pipeline = client.get("/api/logistics/pipeline/PO-1")
    assert pipeline.status_code == 200, pipeline.text
    assert pipeline.json()["current_stage"] == "warehouse_received"
    assert pipeline.json()["stage_verified"] is False
    assert pipeline.json()["authoritative_purchasing_state"] is False

    db.rpc.assert_not_called()
    db.table.return_value.upsert.assert_not_called()


def test_empty_reorder_portfolio_is_not_evidence_of_no_reorders():
    client, db = _client()
    response = client.post("/api/logistics/reorder/evaluate", json={"skus": []})
    assert response.status_code == 422
    db.table.assert_not_called()


@pytest.mark.parametrize("sku_change", [
    {"lead_time_days": []},
    {"stock_on_hand": -1},
    {"stock_committed": -2},
    {"stock_in_transit": -5},
    {"cases_per_pallet": 0},
    {"cases_per_40hc_container": 0},
])
def test_reorder_requires_positive_supply_units_and_real_lead_observations(sku_change):
    client, db = _client()
    response = client.post("/api/logistics/reorder/evaluate", json={
        "skus": [_sku() | sku_change],
    })
    assert response.status_code == 422
    db.table.assert_not_called()


@pytest.mark.parametrize("line", [
    {"sku": "CASE-A", "qty_ordered": 40, "qty_received": -1, "unit": "case"},
    {"sku": "CASE-A", "qty_ordered": 40, "qty_received": 42, "unit": "case"},
    {"sku": "CASE-A", "qty_ordered": 40, "qty_received": 1.5, "unit": "case"},
    {"sku": "CASE-A", "qty_ordered": 40, "qty_received": 38, "unit": "bottle"},
    {"sku": "CASE-A", "qty_ordered": 40, "unit": "case"},
    {"sku": "CASE-A", "qty_ordered": 40, "qty_received": 38},
])
def test_unverified_receipt_rejects_invalid_amounts_and_implicit_uom(line):
    client, db = _client()
    response = client.post("/api/logistics/receipt/process", json={
        **_receipt(), "line_items": [line],
    })
    assert response.status_code == 422
    db.table.assert_not_called()


def test_unverified_receipt_rejects_empty_and_duplicate_lines():
    client, db = _client()
    for lines in ([], _receipt()["line_items"] * 2):
        response = client.post("/api/logistics/receipt/process", json={
            **_receipt(), "line_items": lines,
        })
        assert response.status_code == 422
    db.table.assert_not_called()


@pytest.mark.parametrize("bad", [
    [{"date": "2026-10-01", "units": -1}],
    [{"date": "2026-10-01", "units": "NaN"}],
    [{"date": "2026-10-01", "units": 3}, {"date": "2026-10-01", "units": 4}],
    [{"date": "2026-10-03", "units": 3}, {"date": "2026-10-02", "units": 4}],
    [{"date": "bad-date", "units": 3}],
    [{"date": (date.today()+timedelta(days=4)).isoformat(), "units": 3}],
])
def test_learning_rejects_invalid_fake_history_before_any_company_read(bad):
    client, db = _client()
    response = client.post("/api/logistics/learning/seasonal-indices", json={
        "sku": "CASE-A", "daily_sales": bad,
    })
    assert response.status_code == 422
    db.table.assert_not_called()


def test_empty_analytics_engine_does_not_claim_operations_are_on_track():
    result = LearningEngine("test-tenant").monthly_logistics_report([], {}, [])
    assert result["status"] == "insufficient_data"
    assert result["portfolio_landed_cost_change_pct"] is None
    assert "on track" not in result["recommendation"].lower()


@pytest.mark.parametrize("company_rows", [[], [{"id": "another-tenant"}], [
    {"id": "test-tenant"}, {"id": "test-tenant"},
]])
def test_missing_ambiguous_or_cross_tenant_config_fails_closed(company_rows):
    client, db = _client()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=company_rows)
    )
    response = client.post("/api/logistics/reorder/evaluate", json={"skus": [_sku()]})
    assert response.status_code == 503
    assert "company" not in response.text.lower() or "unavailable" in response.text.lower()
    db.rpc.assert_not_called()


def test_company_lookup_outage_does_not_report_no_reorder_or_execute_write():
    client, db = _client()
    db.table.return_value.select.return_value.eq.return_value.execute.side_effect = RuntimeError(
        "private company config password"
    )
    response = client.post("/api/logistics/reorder/evaluate", json={"skus": [_sku()]})
    assert response.status_code == 503
    assert "password" not in response.text
    db.rpc.assert_not_called()

