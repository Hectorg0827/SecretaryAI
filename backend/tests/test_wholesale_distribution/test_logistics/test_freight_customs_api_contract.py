"""Stage 4–6 HTTP regressions: invoice and customs text are unverified previews."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user
from app.intelligence.wholesale_distribution.logistics.freight import FreightCostParser, TransitTracker
from app.intelligence.wholesale_distribution.logistics.pipeline import FreightBooking, FreightCostComponent


def _client(role="manager"):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": "tenant-1", "port_free_time_days": 5}])
    )
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "employee-1", "company_id": "tenant-1", "role": role,
    }
    app.dependency_overrides[logistics.get_db] = lambda: db
    return TestClient(app), db


def test_freight_invoice_parses_all_labeled_lines_without_double_counting():
    client, db = _client()
    response = client.post("/api/logistics/freight/parse-invoice", json={
        "booking_id": "BK-100", "total_cases": 1000,
        "invoice_text": (
            "Invoice BK-100\n"
            "Ocean Freight $2,500.00\n"
            "Fuel Surcharge (BAF) $350.00\n"
            "Origin Charges (THC) $200.00\n"
            "Destination THC $125.00"
        ),
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["components"] == {
        "ocean_freight": 2500.0,
        "fuel_surcharge": 350.0,
        "origin_charges": 200.0,
        "port_charges": 125.0,
    }
    assert body["total_cost"] == 3175.0
    assert body["status"] == "analysis_only"
    assert body["invoice_verified"] is False
    assert body["booking_verified"] is False
    assert body["accounting_posted"] is False
    assert body["requires_human_review"] is True
    db.rpc.assert_not_called()
    db.table.return_value.upsert.assert_not_called()


@pytest.mark.parametrize("cases,text", [
    (0, "Ocean Freight $300.00"),
    (-2, "Ocean Freight $300.00"),
    (100, ""),
])
def test_freight_invoice_rejects_invalid_denominator_or_missing_invoice(cases, text):
    client, db = _client()
    response = client.post("/api/logistics/freight/parse-invoice", json={
        "booking_id": "BK-100", "total_cases": cases, "invoice_text": text,
    })
    assert response.status_code == 422
    db.table.assert_not_called()


def test_freight_invoice_cannot_silently_relabel_euros_as_usd():
    client, db = _client()
    response = client.post("/api/logistics/freight/parse-invoice", json={
        "booking_id": "BK-100", "total_cases": 100,
        "invoice_text": "Ocean Freight €500.00",
    })
    assert response.status_code == 422
    db.table.assert_not_called()


def test_ambiguous_invoice_line_does_not_guess_amount():
    client, db = _client()
    response = client.post("/api/logistics/freight/parse-invoice", json={
        "booking_id": "BK-100", "total_cases": 100,
        "invoice_text": "Ocean Freight $200.00 plus adjustment $30.00",
    })
    assert response.status_code == 200
    assert response.json()["components"] == {}
    assert response.json()["requires_human_review"] is True
    db.rpc.assert_not_called()


def test_freight_allocation_accumulates_repeated_component_lines():
    parts = [
        FreightCostComponent(component="ocean_freight", amount_usd=200),
        FreightCostComponent(component="ocean_freight", amount_usd=50),
    ]
    allocated = FreightCostParser().allocate_to_skus(parts, {"SKU-A": 5, "SKU-B": 5})
    assert allocated["SKU-A"]["ocean_freight"] == 25.0
    assert allocated["SKU-B"]["ocean_freight"] == 25.0
    with pytest.raises(ValueError, match="positive"):
        FreightCostParser().allocate_to_skus(parts, {"SKU-A": -3})


def test_unverified_customs_hold_is_candidate_only_without_operational_alert():
    client, db = _client()
    response = client.post("/api/logistics/customs/hold", json={
        "booking_id": "BK-100", "hold_reason": "Missing certificate of origin",
        "days_at_port": 7,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidate_milestone"] == "hold_placed"
    assert body["status"] == "analysis_only"
    assert body["customs_event_verified"] is False
    assert body["alert"] is None
    assert body["requires_human_review"] is True
    assert body["pipeline_status_changed"] is False
    db.rpc.assert_not_called()
    db.table.return_value.upsert.assert_not_called()


@pytest.mark.parametrize("days", [-1, -50])
def test_customs_hold_rejects_negative_port_days(days):
    client, db = _client()
    response = client.post("/api/logistics/customs/hold", json={
        "booking_id": "BK-100", "hold_reason": "Exam", "days_at_port": days,
    })
    assert response.status_code == 422
    db.table.assert_not_called()


@pytest.mark.parametrize("arrival,today", [
    ("not-a-date", "2026-10-26"),
    ("2026-10-27", "2026-10-26"),
    ("2026-10-20", "bad-date"),
])
def test_demurrage_risk_rejects_invalid_or_future_port_clock(arrival, today):
    client, db = _client()
    response = client.post("/api/logistics/customs/demurrage-risk", json={
        "booking_id": "BK-100", "arrival_date": arrival, "today": today,
    })
    assert response.status_code == 422
    db.table.assert_not_called()


def test_demurrage_risk_is_estimate_not_verified_accrual():
    client, db = _client()
    response = client.post("/api/logistics/customs/demurrage-risk", json={
        "booking_id": "BK-100", "arrival_date": "2026-10-20",
        "today": "2026-10-26",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days_at_port"] == 6
    assert body["current_demurrage_cost"] == 150.0
    assert body["status"] == "analysis_only"
    assert body["port_arrival_verified"] is False
    assert body["invoiced_demurrage_verified"] is False
    assert body["alert"] is None
    assert body["pipeline_status_changed"] is False
    assert body["requires_human_review"] is True
    db.rpc.assert_not_called()


def test_broker_message_is_candidate_not_confirmed_release():
    client, db = _client()
    response = client.post("/api/logistics/customs/parse-broker-email", json={
        "booking_id": "BK-100", "subject": "Customs released",
        "body": "Container released from customs",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidate_milestone"] == "released"
    assert body["status"] == "analysis_only"
    assert body["message_verified"] is False
    assert body["booking_verified"] is False
    assert body["pipeline_status_changed"] is False
    assert body["requires_human_review"] is True
    db.rpc.assert_not_called()


def test_negated_broker_release_does_not_become_release_candidate():
    client, db = _client()
    response = client.post("/api/logistics/customs/parse-broker-email", json={
        "booking_id": "BK-100", "subject": "Clearance status",
        "body": "The shipment is on hold and has not yet been released.",
    })
    assert response.status_code == 200, response.text
    assert response.json()["candidate_milestone"] is None
    assert response.json()["requires_human_review"] is True
    db.rpc.assert_not_called()


def test_pending_duties_must_not_be_classified_as_paid():
    client, db = _client()
    response = client.post("/api/logistics/customs/parse-broker-email", json={
        "booking_id": "BK-100", "subject": "Duty payment pending",
        "body": "Duty payment is due. Nothing has been paid.",
    })
    assert response.status_code == 200, response.text
    assert response.json()["candidate_milestone"] is None
    db.rpc.assert_not_called()


def test_default_vessel_adapter_must_not_invent_live_location():
    event = TransitTracker(company_id="tenant-1").check_shipment(
        FreightBooking(booking_ref="BK-100", container_numbers=["ABCD1234567"]),
        sku_profiles=[], avg_daily_demand_by_sku={},
    )
    assert event.vessel_lat is None
    assert event.vessel_lon is None
    assert event.status_description == "unknown"
