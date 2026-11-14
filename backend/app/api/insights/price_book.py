"""Tenant-scoped, read-only price book foundation.

Prices stay draft until a separate, authenticated approval/publication path
exists. No price, customer, or QuickBooks writes are performed here.
If the staging-only migration is not yet installed, fail 503, never show
empty rows as an established zero-price book.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_db
from app.api.insights._common import advisory_response
from app.auth.rbac import require_permission

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/price-book"
TAG = "Insights: price book"

FIELDS = (
    "id,sku,package_label,market_code,channel,customer_ref,currency,"
    "list_price_per_case,effective_from,effective_to,revision,status,"
    "cost_snapshot_id,evidence_ref,approved_at"
)


@router.get("")
async def list_price_book(
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(require_permission("view_financials")),
    db=Depends(get_db),
):
    """Read only this company, with bounded pagination and no silent fallback."""
    company_id = user["company_id"]
    try:
        response = (
            db.table("pricing_price_book")
            .select(FIELDS)
            .eq("company_id", company_id)
            .order("effective_from", desc=True)
            .range(offset, offset + limit)
            .execute()
        )
        rows = response.data
    except Exception as exc:
        log.warning("Price-book source read unavailable")
        raise HTTPException(
            status_code=503,
            detail="Price-book source unavailable; pricing evidence was not assessed",
        ) from exc
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) for row in rows
    ):
        raise HTTPException(
            status_code=503,
            detail="Price-book source unavailable; pricing evidence was not assessed",
        )
    # Defensive projection; never allow a broad select or incidental supplier
    # columns to be exposed if the table is extended.
    columns = set(FIELDS.split(","))
    projected = [
        {field: value for field, value in row.items() if field in columns}
        for row in rows[:limit]
    ]
    return advisory_response({
        "company_id": company_id,
        "items": projected,
        "count_returned": len(projected),
        "next_offset": offset + limit if len(rows) > limit else None,
        "book_state": "unpopulated" if offset == 0 and not rows else "records_present",
        "price_changes_allowed": False,
        "pricing_approved_by_this_endpoint": False,
        "cost_source_certified": False,
    }, source="connected_data")
