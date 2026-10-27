"""Returns decision safety: unverified evidence cannot finalize a return."""

from datetime import datetime, timedelta, timezone
import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logistics
from app.auth.rbac import get_current_user
from app.intelligence.wholesale_distribution.returns_mgmt import (
    DispositionDecision,
    ReturnCategory,
    ReturnRecord,
    ReturnsManagementEngine,
)


def _record(return_id="R-1", reason="defective", **overrides):
    kwargs = dict(
        return_id=return_id, customer_id="C-1", supplier_id="S-1",
        sku="SKU-A", units=5, unit_cost=20.0, return_reason=reason,
        category=ReturnCategory.DEFECTIVE, lot_number="LOT-1",
        po_number="PO-1", return_date="2026-10-01",
    )
    kwargs.update(overrides)
    return ReturnRecord(**kwargs)


def _client(role="back_office", company="tenant-a"):
    app = FastAPI()
    app.include_router(logistics.router, prefix="/api/logistics")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "user-a", "company_id": company, "role": role,
    }
    return TestClient(app)


def _request():
    return {
        "return_reference": "R-1", "order_reference": "ORDER-1",
        "warehouse_id": "WH-1", "sku": "SKU-A", "customer_id": "C-1",
        "supplier_id": "S-1", "lot_code": "LOT-1", "requested_cases": 5,
        "unit": "case", "product_type": "beverage",
        "return_reason": "Unopened excess cases from an over-order",
        "observed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def test_unclassified_reason_does_not_default_to_restockable_overstock():
    engine = ReturnsManagementEngine("tenant-a")
    record = _record(reason="Please call me about this return", category=None)
    assert engine.classify_return(record) == ReturnCategory.UNCLASSIFIED
    assert engine.recommend_disposition(record) == DispositionDecision.HOLD_FOR_INSPECTION


def test_carrier_acronym_inside_another_word_is_not_damage_evidence():
    engine = ReturnsManagementEngine("tenant-a")
    assert engine.classify_return(_record(reason="Customer upsell question", category=None)) == ReturnCategory.UNCLASSIFIED


def test_returns_reject_duplicate_ids_negative_units_nonfinite_values_and_bad_revenue():
    engine = ReturnsManagementEngine("tenant-a")
    with pytest.raises(ValueError, match="duplicate|Duplicate"):
        engine.compute_metrics([_record(), _record()], total_revenue=1000)
    with pytest.raises(ValueError):
        engine.compute_metrics([_record(units=-1)], total_revenue=1000)
    with pytest.raises(ValueError):
        engine.compute_metrics([_record(unit_cost=math.nan)], total_revenue=1000)
    with pytest.raises(ValueError):
        engine.compute_metrics([_record()], total_revenue=-100)


def test_unknown_revenue_exposure_does_not_report_zero_return_rate():
    metric = ReturnsManagementEngine("tenant-a").compute_metrics(
        [_record()], total_revenue=0,
    ).to_dict()
    assert metric["return_rate_pct"] is None
    assert metric["return_rate_basis_verified"] is False
    assert metric["status"] == "analysis_only"


def test_returns_financial_recovery_is_not_credited_as_observed():
    record = _record(disposition=DispositionDecision.RESTOCK)
    metrics = ReturnsManagementEngine("tenant-a").compute_metrics(
        [record], total_revenue=1000,
    ).to_dict()
    assert metrics["financial_impact"]["actual_recovery_verified"] is False
    assert metrics["financial_impact"]["actual_recovery_amount"] is None
    assert metrics["requires_human_review"] is True
    assert record.to_dict()["credit_status_verified"] is False


def test_caller_credit_and_claim_flags_are_claims_not_verified_events():
    record = _record(credit_issued=True, supplier_claim_filed=True)
    snapshot = record.to_dict()
    assert snapshot["credit_issued_claimed"] is True
    assert snapshot["supplier_claim_filed_claimed"] is True
    assert "credit_issued" not in snapshot
    assert "supplier_claim_filed" not in snapshot
    assert snapshot["source_evidence_verified"] is False


def test_uninspected_quality_concern_cannot_be_claimed_as_confirmed_defect():
    engine = ReturnsManagementEngine("tenant-a")
    result = engine.generate_supplier_claim(
        supplier_id="S-1", supplier_name="Test Supplier",
        returns=[_record(reason="possible off smell", category=ReturnCategory.QUALITY_CONCERN)],
        total_units_received=100, period_start="2026-10-01",
        period_end="2026-10-31",
    )
    assert result is None


@pytest.mark.parametrize("received,threshold", [
    (0, 1), (-1, 1), (100, -1), (100, 101), (100, math.nan),
])
def test_claim_needs_valid_receipts_and_threshold(received, threshold):
    engine = ReturnsManagementEngine("tenant-a")
    with pytest.raises(ValueError):
        engine.generate_supplier_claim(
            "S-1", "Test Supplier", [_record()], received,
            "2026-10-01", "2026-10-31", threshold,
        )


def test_claim_refuses_unverified_duplicate_or_outside_period_observations():
    engine = ReturnsManagementEngine("tenant-a")
    for records in (
        [_record(), _record()],
        [_record(return_date="2026-09-20")],
    ):
        with pytest.raises(ValueError):
            engine.generate_supplier_claim(
                "S-1", "Test Supplier", records, 100,
                "2026-10-01", "2026-10-31",
            )


def test_claim_cannot_exceed_receipts_or_assume_missing_units_are_zero():
    engine = ReturnsManagementEngine("tenant-a")
    with pytest.raises(ValueError):
        engine.generate_supplier_claim(
            "S-1", "Test Supplier", [_record(units=110)], 100,
            "2026-10-01", "2026-10-31",
        )


def test_supplier_claim_is_stable_unsent_preview_requiring_external_proof():
    engine = ReturnsManagementEngine("tenant-a")
    args = dict(
        supplier_id="S-1", supplier_name="Test Supplier",
        returns=[_record()], total_units_received=100,
        period_start="2026-10-01", period_end="2026-10-31",
    )
    a = engine.generate_supplier_claim(**args).to_dict()
    b = engine.generate_supplier_claim(**args).to_dict()
    assert a["claim_id"] == b["claim_id"]
    assert a["status"] == "analysis_only"
    assert a["claim_filed"] is False
    assert a["financial_write_performed"] is False
    assert a["source_evidence_verified"] is False
    assert a["requires_human_review"] is True
    changed = args | {"returns": [_record(unit_cost=23.0)]}
    assert engine.generate_supplier_claim(**changed).claim_id != a["claim_id"]


def test_return_preview_is_role_scoped_quarantine_not_approved_posting():
    response = _client().post("/api/logistics/returns/preview", json=_request())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["company_id"] == "tenant-a"
    assert data["status"] == "analysis_only"
    assert data["return_verified"] is False
    assert data["inspection_verified"] is False
    assert data["disposition_authorized"] is False
    assert data["inventory_posted"] is False
    assert data["credit_issued"] is False
    assert data["supplier_claim_filed"] is False
    assert data["requires_human_review"] is True
    assert data["proposed_disposition"] == "hold_for_inspection"
    assert _client(role="viewer").post(
        "/api/logistics/returns/preview", json=_request(),
    ).status_code == 403


@pytest.mark.parametrize("change", [
    {"unit": "bottle"}, {"requested_cases": 0}, {"requested_cases": 1.5},
    {"observed_at": "2026-10-01T09:00:00"},
    {"observed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"company_id": "spoof-tenant"},
])
def test_return_preview_rejects_bad_units_dates_and_company_spoofing(change):
    response = _client().post(
        "/api/logistics/returns/preview", json=_request() | change,
    )
    assert response.status_code == 422


def test_expired_or_undetermined_returns_are_quarantined_not_destroyed():
    client = _client()
    for reason in ("Expired cases", "Please call me about this return"):
        response = client.post(
            "/api/logistics/returns/preview",
            json=_request() | {"return_reason": reason},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["proposed_disposition"] == "hold_for_inspection"
        assert data["destroy_authorized"] is False


@pytest.mark.parametrize("reason,expected", [
    ("Product defective", ReturnCategory.DEFECTIVE),
    ("Item failed during use", ReturnCategory.DEFECTIVE),
    ("Customer cancelled order", ReturnCategory.CUSTOMER_CANCEL),
    ("Customer canceled order", ReturnCategory.CUSTOMER_CANCEL),
])
def test_common_reason_inflections_preserve_whole_word_classification(reason, expected):
    engine = ReturnsManagementEngine("tenant-a")
    record = _record(reason=reason, category=None)
    assert engine.classify_return(record) == expected


def test_inflected_defective_return_remains_eligible_for_unsent_supplier_claim():
    engine = ReturnsManagementEngine("tenant-a")
    record = _record(reason="Product defective", category=None)
    record.category = engine.classify_return(record)
    claim = engine.generate_supplier_claim(
        "S-1", "Test Supplier", [record], 100, "2026-10-01", "2026-10-31",
    )
    assert claim is not None
    assert claim.defective_units == 5
    assert claim.to_dict()["claim_filed"] is False
