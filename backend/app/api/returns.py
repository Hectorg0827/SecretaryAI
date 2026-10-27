"""Tenant- and role-scoped return review previews, with no state transitions.

Return, sale, lot and inspection references are caller claims. Until Chat 1's
approval/ledger contract and Chat 2's authenticated source adapter exist,
this endpoint MUST NOT post stock, approve disposition, issue credit or file
supplier claims.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.returns_mgmt import (
    ReturnRecord,
    ReturnsManagementEngine,
)


router = APIRouter()


class ReturnReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_reference: str = Field(min_length=1, max_length=150)
    order_reference: str = Field(min_length=1, max_length=150)
    warehouse_id: str = Field(min_length=1, max_length=150)
    sku: str = Field(min_length=1, max_length=120)
    customer_id: str = Field(min_length=1, max_length=150)
    supplier_id: str = Field(min_length=1, max_length=150)
    lot_code: str = Field(default="", max_length=120)
    requested_cases: int = Field(strict=True, gt=0, le=10000000)
    unit: Literal["case"]
    product_type: Literal["beverage", "food", "nonperishable", "other"]
    return_reason: str = Field(min_length=1, max_length=1500)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def valid_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Return snapshot requires timezone-aware observed_at")
        if value.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Return observation must not be in the future")
        return value


@router.post("/preview")
async def preview_return_review(
    request: ReturnReviewRequest,
    user: dict = Depends(require_permission("view_orders")),
):
    """Route a return to review/quarantine; never execute a disposition."""
    engine = ReturnsManagementEngine(company_id=user["company_id"])
    record = ReturnRecord(
        return_id=request.return_reference,
        customer_id=request.customer_id,
        supplier_id=request.supplier_id,
        sku=request.sku,
        units=request.requested_cases,
        unit_cost=0,  # unknown; never interpret as an actual zero-cost return
        return_reason=request.return_reason,
        lot_number=request.lot_code or None,
    )
    category = engine.classify_return(record)
    snapshot_age = max(0.0, (datetime.now(timezone.utc) - request.observed_at)
                       .total_seconds() / 60)
    return {
        "company_id": user["company_id"],
        "return_reference": request.return_reference,
        "order_reference": request.order_reference,
        "warehouse_id": request.warehouse_id,
        "sku": request.sku,
        "lot_code": request.lot_code,
        "requested_cases": request.requested_cases,
        "unit": "case",
        "category_candidate": category.value,
        "classification_verified": False,
        "proposed_disposition": "hold_for_inspection",
        "inspection_routing": "quarantine_review_and_independent_quality_inspection",
        "status": "analysis_only",
        "source_type": "caller_supplied_unverified",
        "observed_at": request.observed_at.astimezone(timezone.utc).isoformat(),
        "snapshot_stale": snapshot_age > 60,
        "snapshot_age_minutes": round(snapshot_age, 1),
        "return_verified": False,
        "order_verified": False,
        "lot_verified": False,
        "inspection_verified": False,
        "independent_approval_verified": False,
        "disposition_authorized": False,
        "destroy_authorized": False,
        "inventory_posted": False,
        "credit_issued": False,
        "supplier_claim_filed": False,
        "financial_write_performed": False,
        "requires_human_review": True,
        "pipeline": [
            {"stage": "return_request", "state": "unverified_caller_preview"},
            {"stage": "order_and_receipt_match", "state": "not_verified"},
            {"stage": "physical_quarantine", "state": "not_verified"},
            {"stage": "independent_inspection", "state": "not_verified"},
            {"stage": "disposition_approval", "state": "not_authorized"},
            {"stage": "stock_or_credit_posting", "state": "not_performed"},
            {"stage": "supplier_claim_and_audit", "state": "not_performed"},
        ],
        "next_action": "verify_order_lot_and_physical_quarantine_before_independent_inspection",
    }
