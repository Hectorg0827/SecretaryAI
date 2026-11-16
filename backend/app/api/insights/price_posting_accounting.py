"""Read-only internal accounting observation comparison. No live QBO/QBD."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import get_db
from app.api.insights._pricing_access import get_pricing_user
from app.api.insights.price_posting import BOOK_FIELDS, _rows
from app.intelligence.compliance.accounting_observation_comparison import (
    compare_accounting_observations,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/price-posting-accounting"
TAG = "Insights: accounting observation comparison (not certified)"

ACCOUNTING_FIELDS = (
    "id,company_id,price_book_id,provider,source_item_id,source_revision,"
    "sku,currency,observed_unit_price,unit_basis,units_per_case,"
    "source_document_ref,observed_at,source_origin,observation_state"
)


class AccountingObservationComparisonRequest(BaseModel):
    """Select stored source IDs only; no caller-supplied QB amounts."""
    price_book_ids: list[UUID] = Field(min_length=1, max_length=50)


@router.post("/compare")
async def compare_stored_accounting_observations(
    request: AccountingObservationComparisonRequest,
    response: Response,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    company_id = str(user["company_id"])
    ids = [str(x) for x in request.price_book_ids]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate selected price-book IDs")

    try:
        books = _rows(
            db.table("pricing_price_book")
            .select(BOOK_FIELDS + ",created_by")
            .eq("company_id", company_id).in_("id", ids)
            .limit(len(ids) + 1).execute(),
            "Price-book source",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Price posting accounting comparison book source unavailable")
        raise HTTPException(status_code=503, detail="Price-book evidence unavailable") from exc

    books_by_id = {str(row.get("id") or ""): row for row in books}
    if (len(books) != len(ids) or len(books_by_id) != len(ids)
            or any(x not in books_by_id for x in ids)):
        raise HTTPException(status_code=404, detail="Requested price-book source not found")
    if any(str(b.get("company_id")) != company_id for b in books):
        raise HTTPException(status_code=503, detail="Price-book tenant scope invalid")

    try:
        observations = _rows(
            db.table("pricing_accounting_price_observations")
            .select(ACCOUNTING_FIELDS)
            .eq("company_id", company_id).in_("price_book_id", ids)
            .limit(501).execute(),
            "Accounting observation source",
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Accounting observation source unavailable")
        raise HTTPException(
            status_code=503,
            detail="Accounting observation source unavailable; no comparison made",
        ) from exc
    if len(observations) > 500:
        raise HTTPException(status_code=503, detail="Accounting observations exceed checked scope")

    try:
        return compare_accounting_observations(
            company_id=company_id,
            books=[books_by_id[i] for i in ids],
            observations=observations,
            evaluated_at=datetime.now(timezone.utc),
        )
    except (TypeError, KeyError, ValueError, ArithmeticError) as exc:
        log.warning("Accounting observation comparison source invalid")
        raise HTTPException(
            status_code=503,
            detail="Accounting observation evidence invalid; no comparison made",
        ) from exc
