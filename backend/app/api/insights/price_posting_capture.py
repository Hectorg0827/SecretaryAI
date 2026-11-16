"""Owner/manager-only QBO/QBD capture prerequisite preview; no provider IO.

This endpoint does not load credentials, instantiate QuickBooks adapters,
write to Supabase, or enable the separately gated accounting capture workflow.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import get_db
from app.api.insights._pricing_access import get_pricing_user
from app.api.insights.price_posting import BOOK_FIELDS, _rows
from app.intelligence.compliance.accounting_capture_readiness import (
    compile_capture_mapping_readiness,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/price-posting-capture"
TAG = "Insights: accounting capture prerequisites only"

BINDING_FIELDS = (
    "id,company_id,price_book_id,provider,source_item_id,source_revision,"
    "source_sku_hint,claimed_currency,candidate_unit_basis,"
    "candidate_units_per_case,candidate_document_ref,identity_state"
)


class CaptureReadinessRequest(BaseModel):
    provider: Literal["qbo", "qbd"]
    effective_date: date
    price_book_ids: list[UUID] = Field(min_length=1, max_length=50)


@router.post("/readiness")
async def capture_prerequisites(
    request: CaptureReadinessRequest,
    response: Response,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    # Item IDs and candidate pack mappings must NOT be exposed to pricing-only
    # roles simply because they can see public price-book comparisons.
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="Capture mapping review requires owner or manager")
    company_id = str(user["company_id"])
    ids = [str(x) for x in request.price_book_ids]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="Duplicate price-book IDs")

    try:
        rows = _rows(
            db.table("pricing_price_book")
            .select(BOOK_FIELDS)
            .eq("company_id", company_id)
            .in_("id", ids).limit(len(ids) + 1).execute(),
            "Price-book capture source",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Capture plan price-book source unavailable")
        raise HTTPException(status_code=503, detail="Price-book mapping source unavailable") from exc

    indexed = {str(row.get("id") or ""): row for row in rows}
    if len(rows) != len(ids) or len(indexed) != len(ids) or any(x not in indexed for x in ids):
        raise HTTPException(status_code=404, detail="Requested price-book source not found")
    if any(str(row.get("company_id")) != company_id for row in rows):
        raise HTTPException(status_code=503, detail="Capture price-book tenant scope invalid")

    try:
        bindings = _rows(
            db.table("pricing_accounting_capture_bindings")
            .select(BINDING_FIELDS)
            .eq("company_id", company_id)
            .eq("provider", request.provider)
            .in_("price_book_id", ids)
            .limit(501).execute(),
            "Candidate item-ID bindings",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Capture plan binding source unavailable")
        raise HTTPException(status_code=503, detail="Candidate item mapping source unavailable") from exc
    if len(bindings) > 500:
        raise HTTPException(status_code=503, detail="Capture binding page exceeds supported scope")
    try:
        return compile_capture_mapping_readiness(
            company_id=company_id, provider=request.provider,
            books=[indexed[x] for x in ids], bindings=bindings,
            effective_date=request.effective_date,
        )
    except (ValueError, TypeError, KeyError) as exc:
        log.warning("Capture plan source invalid")
        raise HTTPException(status_code=503, detail="Capture source cannot be verified") from exc
