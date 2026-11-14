"""Tenant-scoped, read-only price book foundation.

Prices stay draft until a separate, authenticated approval/publication path
exists. No price, customer, or QuickBooks writes are performed here.
If the staging-only migration is not yet installed, fail 503, never show
empty rows as an established zero-price book.
"""
from __future__ import annotations

import logging

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import get_db
from app.api.insights._common import advisory_response
from app.api.insights._pricing_access import get_pricing_user

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
    user: dict = Depends(get_pricing_user),
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


@router.get("/sources/assessments/{assessment_id}")
async def pricing_purchase_assessment_source(
    assessment_id: UUID,
    response: Response,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    """Read accepted-case and supplier cost evidence without purchasing rights.

    Prior allocated landed charges in an assessment remain unclassified;
    they cannot be treated as a confirmed component or silently applied twice.
    The returned costs are explicitly provisional, and no writes occur.
    """
    response.headers["Cache-Control"] = "private, no-store"
    company_id = user["company_id"]
    from app.api.logistics import _load_finance_assessment, _read_purchasing_rows
    from app.services.purchasing_finance import value_receipts

    assessment, order, lines = _load_finance_assessment(db, company_id, assessment_id)
    documents = _read_purchasing_rows(
        db.table("warehouse_receipt_documents")
        .select("id")
        .eq("company_id", company_id)
        .eq("po_id", assessment["po_id"])
        .limit(501)
    )
    if len(documents) > 500:
        raise HTTPException(status_code=503, detail="Receipt scope exceeds supported page")
    receipt_lines = []
    if documents:
        receipt_lines = _read_purchasing_rows(
            db.table("warehouse_receipt_lines")
            .select("receipt_id,sku,accepted_qty::text,damaged_qty::text,quarantined_qty::text")
            .eq("company_id", company_id)
            .in_("receipt_id", [d["id"] for d in documents])
            .limit(1001)
        )
        if len(receipt_lines) > 1000:
            raise HTTPException(status_code=503, detail="Receipt line scope exceeds supported page")
    try:
        valuation = value_receipts(assessment, lines, receipt_lines)
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        log.warning("Pricing receipt cost preview unavailable")
        raise HTTPException(
            status_code=503, detail="Purchase assessment cannot be valued safely",
        ) from exc
    return advisory_response({
        "assessment_id": str(assessment_id),
        "po_number": order["po_number"],
        "assertion_status": assessment["assertion_status"],
        "currency": valuation["currency"],
        "landed_cost_total": valuation["landed_cost_total"],
        "lines": [
            {
                "sku": item["sku"],
                "unit_cost": item["unit_cost"],
                "allocated_landed_cost": item["allocated_landed_cost"],
                "cases": item["cases"],
            }
            for item in valuation["lines"]
        ],
        "valuation_verified": False,
        "accounting_reconciled": False,
        "preallocated_landed_cost_not_applied": True,
        "read_consistency": "separate_tenant_scoped_queries_not_atomic_snapshot",
    }, source="connected_data")
