"""Warehouse work-order previews built strictly from unverified caller snapshots.

These calculations MUST NOT be converted into stock movements without a
verified tenant-scoped ledger snapshot and Chat 1's approval contract.
There are deliberately no repository, network or notification calls.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CountCases = Annotated[int, Field(strict=True, ge=0, le=10000000)]
PositiveCases = Annotated[int, Field(strict=True, gt=0, le=10000000)]
Steps = Annotated[int, Field(strict=True, ge=0, le=1000000)]
Events = Annotated[int, Field(strict=True, ge=0, le=1000000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WarehouseSnapshot(_Strict):
    warehouse_id: str = Field(min_length=1, max_length=150)
    observed_at: datetime
    unit: Literal["case"] = "case"

    @field_validator("observed_at")
    @classmethod
    def aware_and_not_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Warehouse snapshot requires a timezone-aware timestamp")
        if value.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Warehouse observation cannot be in the future")
        return value


class PickOrderLine(_Strict):
    sku: str = Field(min_length=1, max_length=120)
    requested_cases: PositiveCases
    require_expiry: bool = False


class PickBin(_Strict):
    bin_id: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=120)
    lot_code: str = Field(default="", max_length=120)
    on_hand_cases: CountCases
    allocated_cases: CountCases = 0
    quarantined_cases: CountCases = 0
    expiry_date: Optional[date] = None
    travel_steps: Steps = 0

    @model_validator(mode="after")
    def check_observed_balance(self):
        if self.allocated_cases + self.quarantined_cases > self.on_hand_cases:
            raise ValueError("Allocated/quarantined cases exceed observed on-hand")
        return self


class PickPlanRequest(WarehouseSnapshot):
    order_reference: str = Field(min_length=1, max_length=150)
    lines: list[PickOrderLine] = Field(min_length=1, max_length=200)
    bins: list[PickBin] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_lot_bin_entries(self):
        keys = [(b.bin_id, b.sku, b.lot_code) for b in self.bins]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate warehouse bin/SKU/lot observation")
        return self


class CountLine(_Strict):
    sku: str = Field(min_length=1, max_length=120)
    bin_id: str = Field(min_length=1, max_length=120)
    lot_code: str = Field(default="", max_length=120)
    expected_cases: CountCases
    counted_cases: CountCases


class CycleCountRequest(WarehouseSnapshot):
    count_reference: str = Field(min_length=1, max_length=150)
    lines: list[CountLine] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_count_lines(self):
        keys = [(l.bin_id, l.sku, l.lot_code) for l in self.lines]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate count line")
        return self


class CapacityBin(_Strict):
    bin_id: str = Field(min_length=1, max_length=120)
    capacity_cases: CountCases
    occupied_cases: CountCases
    reserved_cases: CountCases = 0
    observed_pick_events: Events = 0
    travel_steps: Steps = 0


class CapacityRequest(WarehouseSnapshot):
    bins: list[CapacityBin] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_bins(self):
        ids = [b.bin_id for b in self.bins]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate capacity bin")
        return self


class SupplierClaimLine(_Strict):
    sku: str = Field(min_length=1, max_length=120)
    lot_code: str = Field(default="", max_length=120)
    expected_cases: CountCases
    accepted_cases: CountCases
    damaged_cases: CountCases
    notes: str = Field(default="", max_length=1000)


class ClaimPreviewRequest(WarehouseSnapshot):
    po_number: str = Field(min_length=1, max_length=100)
    receipt_reference: str = Field(min_length=1, max_length=150)
    lines: list[SupplierClaimLine] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_claim_lines(self):
        keys = [(l.sku, l.lot_code) for l in self.lines]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate SKU/lot claim line")
        return self


def _base(request: WarehouseSnapshot, company_id: str) -> dict:
    age_minutes = max(0.0, (datetime.now(timezone.utc) - request.observed_at).total_seconds() / 60.0)
    return {
        "company_id": company_id,
        "warehouse_id": request.warehouse_id,
        "observed_at": request.observed_at.astimezone(timezone.utc).isoformat(),
        "snapshot_age_minutes": round(age_minutes, 1),
        "snapshot_stale": age_minutes > 60,
        "unit": "case",
        "status": "analysis_only",
        "warehouse_snapshot_verified": False,
        "requires_human_review": True,
        "source_evidence_required": True,
        "source_type": "caller_supplied_unverified",
    }


def propose_picks(request: PickPlanRequest, company_id: str) -> dict:
    """FEFO where available, then shortest walk; never reserve inventory."""
    available = {
        (b.bin_id, b.sku, b.lot_code): b.on_hand_cases - b.allocated_cases - b.quarantined_cases
        for b in request.bins
    }
    today = datetime.now(timezone.utc).date()
    picks: list[dict] = []
    shortages: list[dict] = []
    excluded_expired = 0
    excluded_unknown_expiry = 0

    for line in request.lines:
        remaining = line.requested_cases
        matches = []
        for b in request.bins:
            if b.sku != line.sku:
                continue
            if b.expiry_date is not None and b.expiry_date < today:
                excluded_expired += 1
                continue
            if line.require_expiry and b.expiry_date is None:
                excluded_unknown_expiry += 1
                continue
            matches.append(b)
        matches.sort(key=lambda b: (
            b.expiry_date is None,
            b.expiry_date or date.max,
            b.travel_steps,
            b.bin_id,
        ))
        for b in matches:
            key = (b.bin_id, b.sku, b.lot_code)
            amount = min(remaining, available[key])
            if amount <= 0:
                continue
            picks.append({"sku": line.sku, "bin_id": b.bin_id,
                          "lot_code": b.lot_code, "cases": amount})
            available[key] -= amount
            remaining -= amount
            if remaining == 0:
                break
        if remaining > 0:
            shortages.append({"sku": line.sku, "missing_cases": remaining,
                              "recommended_action": "verify_stock_and_review_order"})

    return {
        **_base(request, company_id),
        "order_reference": request.order_reference,
        "order_verified": False,
        "execution_authorized": False,
        "inventory_posted": False,
        "picks": picks,
        "shortages": shortages,
        "excluded_expired_observations": excluded_expired,
        "excluded_unknown_expiry_observations": excluded_unknown_expiry,
        "expiry_timezone_verified": False,
        "next_action": "verify_order_and_bin_balances_then_request_pick_approval",
    }


def preview_cycle_count(request: CycleCountRequest, company_id: str) -> dict:
    discrepancies = [
        {"sku": line.sku, "bin_id": line.bin_id, "lot_code": line.lot_code,
         "expected_cases": line.expected_cases, "counted_cases": line.counted_cases,
         "variance_cases": line.counted_cases - line.expected_cases,
         "recommended_action": "recount_then_review"}
        for line in request.lines
        if line.expected_cases != line.counted_cases
    ]
    return {
        **_base(request, company_id),
        "count_reference": request.count_reference,
        "line_count": len(request.lines),
        "discrepancies": discrepancies,
        "adjustment_posted": False,
        "independent_approval_required": True,
        "next_action": "verify_count_sheet_recount_discrepancies_and_seek_approval",
    }


def preview_capacity(request: CapacityRequest, company_id: str) -> dict:
    capacity = sum(b.capacity_cases for b in request.bins)
    occupied = sum(b.occupied_cases for b in request.bins)
    reserved = sum(b.reserved_cases for b in request.bins)
    excess = sum(max(0, b.occupied_cases + b.reserved_cases - b.capacity_cases) for b in request.bins)
    pick_events = sum(b.observed_pick_events for b in request.bins)
    sufficient_sample = pick_events >= 30
    move_drafts = [
        {"bin_id": b.bin_id, "reason": "frequently_picked_with_long_walk",
         "recommended_action": "review_relocation_with_warehouse_manager"}
        for b in request.bins
        if sufficient_sample and b.observed_pick_events >= 15 and b.travel_steps >= 20
    ]
    return {
        **_base(request, company_id),
        "total_capacity_cases": capacity,
        "occupied_cases": occupied,
        "reserved_cases": reserved,
        "physical_utilization_pct": round(occupied / capacity * 100, 2) if capacity else None,
        "overcommitted_cases": excess,
        "observed_pick_event_count": pick_events,
        "slotting_confidence": "unverified_observations" if sufficient_sample else "insufficient_data",
        "candidate_moves": move_drafts,
        "moves_executed": False,
        "stock_adjusted": False,
        "next_action": "validate_bin_measurements_and_pick_history_before_moves",
    }


def preview_supplier_claim(request: ClaimPreviewRequest, company_id: str) -> dict:
    issues = []
    for line in request.lines:
        total_seen = line.accepted_cases + line.damaged_cases
        shortage = max(0, line.expected_cases - total_seen)
        overage = max(0, total_seen - line.expected_cases)
        if shortage or overage or line.damaged_cases:
            issues.append({
                "sku": line.sku, "lot_code": line.lot_code,
                "shortage_cases": shortage,
                "overage_cases": overage,
                "damaged_cases": line.damaged_cases,
                "recommended_action": "collect_inspection_evidence_and_independent_receipt_match",
            })
    return {
        **_base(request, company_id),
        "po_number": request.po_number,
        "receipt_reference": request.receipt_reference,
        "po_verified": False,
        "receipt_verified": False,
        "issues": issues,
        "supplier_notified": False,
        "claim_filed": False,
        "financial_write_performed": False,
        "next_action": "verify_po_receipt_photos_and_supplier_terms_before_claim_approval",
    }
