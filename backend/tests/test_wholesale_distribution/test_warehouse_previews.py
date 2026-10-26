"""Warehouse HTTP proposals: role-scoped, bounded, deterministic and non-posting."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _client(role="back_office", company_id="tenant-a"):
    app = FastAPI()
    # Exercise the real main.py registration path through logistics.router.
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "staff-a", "company_id": company_id, "role": role,
    }
    return TestClient(app)


def _pick_body():
    return {
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "order_reference": "ORDER-1",
        "lines": [{"sku": "SKU-A", "requested_cases": 9}],
        "bins": [
            {"bin_id": "B-1", "sku": "SKU-A", "lot_code": "L-1",
             "on_hand_cases": 6, "allocated_cases": 2, "quarantined_cases": 0,
             "expiry_date": "2027-02-01", "travel_steps": 15},
            {"bin_id": "B-2", "sku": "SKU-A", "lot_code": "L-2",
             "on_hand_cases": 10, "allocated_cases": 1, "quarantined_cases": 2,
             "expiry_date": "2027-09-01", "travel_steps": 3},
        ],
    }


def test_pick_preview_fefo_allocations_and_no_stock_posting():
    client = _client()
    response = client.post("/api/logistics/warehouse/pick-plan", json=_pick_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company_id"] == "tenant-a"
    assert body["status"] == "analysis_only"
    assert body["order_verified"] is False
    assert body["warehouse_snapshot_verified"] is False
    assert body["inventory_posted"] is False
    assert body["execution_authorized"] is False
    assert body["picks"] == [
        {"sku": "SKU-A", "bin_id": "B-1", "lot_code": "L-1", "cases": 4},
        {"sku": "SKU-A", "bin_id": "B-2", "lot_code": "L-2", "cases": 5},
    ]
    assert body["shortages"] == []
    assert body["requires_human_review"] is True


def test_pick_preview_reuses_capacity_once_and_reports_shortage():
    body = _pick_body()
    body["lines"] = [
        {"sku": "SKU-A", "requested_cases": 7},
        {"sku": "SKU-A", "requested_cases": 7},
    ]
    response = _client().post("/api/logistics/warehouse/pick-plan", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert sum(row["cases"] for row in result["picks"]) == 11
    assert sum(row["missing_cases"] for row in result["shortages"]) == 3


def test_pick_preview_rejects_duplicate_bins_and_overallocated_stock():
    client = _client()
    body = _pick_body()
    body["bins"].append(body["bins"][0].copy())
    assert client.post("/api/logistics/warehouse/pick-plan", json=body).status_code == 422

    body = _pick_body()
    body["bins"][0]["allocated_cases"] = 7
    assert client.post("/api/logistics/warehouse/pick-plan", json=body).status_code == 422


def test_pick_preview_excludes_expired_lots_and_requires_expiry_when_requested():
    body = _pick_body()
    body["lines"] = [{"sku": "SKU-A", "requested_cases": 3, "require_expiry": True}]
    body["bins"][0]["expiry_date"] = "2020-01-01"
    body["bins"][1]["expiry_date"] = None
    response = _client().post("/api/logistics/warehouse/pick-plan", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["picks"] == []
    assert response.json()["shortages"][0]["missing_cases"] == 3


def test_missing_or_stale_snapshot_cannot_look_execution_ready():
    body = _pick_body()
    body.pop("observed_at")
    assert _client().post("/api/logistics/warehouse/pick-plan", json=body).status_code == 422
    body["observed_at"] = (_now() - timedelta(days=2)).isoformat()
    response = _client().post("/api/logistics/warehouse/pick-plan", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["snapshot_stale"] is True
    assert response.json()["execution_authorized"] is False


@pytest.mark.parametrize("timestamp", [
    "2026-10-26T12:00:00",  # naive / no timezone
    (_now() + timedelta(days=1)).isoformat(),
])
def test_warehouse_snapshot_must_have_bounded_timezone_aware_timestamp(timestamp):
    body = _pick_body()
    body["observed_at"] = timestamp
    assert _client().post("/api/logistics/warehouse/pick-plan", json=body).status_code == 422


def test_case_units_and_integer_quantities_are_not_implicitly_converted():
    body = _pick_body()
    body["unit"] = "bottle"
    assert _client().post("/api/logistics/warehouse/pick-plan", json=body).status_code == 422
    body = _pick_body()
    body["lines"][0]["requested_cases"] = 1.5
    assert _client().post("/api/logistics/warehouse/pick-plan", json=body).status_code == 422


def test_cycle_count_discrepancy_is_recount_draft_not_stock_adjustment():
    response = _client().post("/api/logistics/warehouse/cycle-count", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "count_reference": "COUNT-1",
        "lines": [
            {"sku": "SKU-A", "bin_id": "B-1", "lot_code": "L-1",
             "expected_cases": 12, "counted_cases": 9},
            {"sku": "SKU-B", "bin_id": "B-2", "lot_code": "L-2",
             "expected_cases": 6, "counted_cases": 6},
        ],
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["adjustment_posted"] is False
    assert data["independent_approval_required"] is True
    assert data["discrepancies"][0]["variance_cases"] == -3
    assert data["discrepancies"][0]["recommended_action"] == "recount_then_review"
    assert data["line_count"] == 2


def test_cycle_count_rejects_duplicates_and_negative_counts():
    body = {
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "count_reference": "COUNT-1",
        "lines": [{"sku": "SKU-A", "bin_id": "B-1", "lot_code": "L-1",
                   "expected_cases": 9, "counted_cases": 9}],
    }
    body["lines"].append(body["lines"][0].copy())
    assert _client().post("/api/logistics/warehouse/cycle-count", json=body).status_code == 422
    body["lines"].pop()
    body["lines"][0]["counted_cases"] = -1
    assert _client().post("/api/logistics/warehouse/cycle-count", json=body).status_code == 422


def test_clean_cycle_count_still_cannot_post_stock():
    response = _client().post("/api/logistics/warehouse/cycle-count", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "count_reference": "COUNT-1", "lines": [{
            "sku": "SKU-A", "bin_id": "B-1", "expected_cases": 10, "counted_cases": 10,
        }],
    })
    assert response.status_code == 200, response.text
    assert response.json()["discrepancies"] == []
    assert response.json()["adjustment_posted"] is False


def test_capacity_flags_overcommit_and_insufficient_slotting_sample():
    response = _client().post("/api/logistics/warehouse/capacity", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "bins": [
            {"bin_id": "B-1", "capacity_cases": 100,
             "occupied_cases": 95, "reserved_cases": 10,
             "observed_pick_events": 4, "travel_steps": 30},
            {"bin_id": "B-2", "capacity_cases": 100,
             "occupied_cases": 20, "reserved_cases": 0,
             "observed_pick_events": 1, "travel_steps": 2},
        ],
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["physical_utilization_pct"] == 57.5
    assert data["overcommitted_cases"] == 5
    assert data["slotting_confidence"] == "insufficient_data"
    assert data["moves_executed"] is False
    assert data["stock_adjusted"] is False


def test_empty_capacity_is_unknown_and_slotting_proposal_needs_sample():
    response = _client().post("/api/logistics/warehouse/capacity", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "bins": [{"bin_id": "B-1", "capacity_cases": 0, "occupied_cases": 0}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["physical_utilization_pct"] is None
    assert response.json()["candidate_moves"] == []
    assert response.json()["slotting_confidence"] == "insufficient_data"


def test_sufficient_slotting_observations_create_review_only_move():
    response = _client().post("/api/logistics/warehouse/capacity", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "bins": [{"bin_id": "B-1", "capacity_cases": 100,
                  "occupied_cases": 40, "observed_pick_events": 33, "travel_steps": 25}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["slotting_confidence"] == "unverified_observations"
    assert response.json()["candidate_moves"][0]["bin_id"] == "B-1"
    assert response.json()["moves_executed"] is False


def test_supplier_claims_are_drafts_not_sent_or_booked():
    response = _client().post("/api/logistics/warehouse/claim-preview", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "po_number": "PO-1", "receipt_reference": "RCPT-1",
        "lines": [{"sku": "SKU-A", "lot_code": "L-1", "expected_cases": 20,
                   "accepted_cases": 16, "damaged_cases": 2,
                   "notes": "Broken units"}],
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["issues"][0]["shortage_cases"] == 2
    assert data["issues"][0]["damaged_cases"] == 2
    assert data["supplier_notified"] is False
    assert data["claim_filed"] is False
    assert data["financial_write_performed"] is False


def test_supplier_overreceipt_and_duplicate_claims_are_not_silently_accepted():
    body = {
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "po_number": "PO-1", "receipt_reference": "RCPT-1",
        "lines": [{"sku": "SKU-A", "lot_code": "L-1", "expected_cases": 10,
                   "accepted_cases": 12, "damaged_cases": 0}],
    }
    response = _client().post("/api/logistics/warehouse/claim-preview", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["issues"][0]["overage_cases"] == 2
    body["lines"].append(body["lines"][0].copy())
    assert _client().post("/api/logistics/warehouse/claim-preview", json=body).status_code == 422


@pytest.mark.parametrize("route,body", [
    ("pick-plan", _pick_body()),
    ("cycle-count", {"warehouse_id": "WH-1", "observed_at": _now().isoformat(),
                      "count_reference": "COUNT-1", "lines": [{"sku": "SKU-A",
                      "bin_id": "B-1", "expected_cases": 1, "counted_cases": 1}]}),
    ("claim-preview", {"warehouse_id": "WH-1", "observed_at": _now().isoformat(),
                       "po_number": "PO-1", "receipt_reference": "RCPT-1",
                       "lines": [{"sku": "SKU-A", "expected_cases": 1,
                                  "accepted_cases": 1, "damaged_cases": 0}]}),
])
def test_sales_rep_cannot_access_warehouse_work_orders(route, body):
    response = _client(role="sales_rep").post(f"/api/logistics/warehouse/{route}", json=body)
    assert response.status_code == 403


def test_tenant_id_is_not_caller_overrideable():
    body = _pick_body()
    body["company_id"] = "tenant-b"
    assert _client(company_id="tenant-a").post(
        "/api/logistics/warehouse/pick-plan", json=body
    ).status_code == 422


def test_viewer_role_denied_even_for_space_preview():
    response = _client(role="viewer").post("/api/logistics/warehouse/capacity", json={
        "warehouse_id": "WH-1", "observed_at": _now().isoformat(),
        "bins": [{"bin_id": "B-1", "capacity_cases": 10, "occupied_cases": 1}],
    })
    assert response.status_code == 403
