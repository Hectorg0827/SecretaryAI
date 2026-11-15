"""Authenticated Lola executive briefing, read-only and fail-closed."""
from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from app.api.insights._pricing_access import get_pricing_user

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.intelligence.lola.briefing import (
    BriefingUnavailable, build_chief_of_staff_briefing,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/briefing")
async def executive_briefing(
    response: Response,
    as_of: date | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=20),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """No tenant ID or unverifiable evidence accepted from the browser."""
    response.headers["Cache-Control"] = "private, no-store"
    from app.api.logistics import _load_ledger, _viewer

    # Chief-of-Staff cross-shipment briefing is restricted to managers and
    # logistics operators. Sales agents retain their scoped logistics workspace.
    # The access check must happen before any service-role database read.
    viewer = _viewer(user)
    if viewer.tier not in ("full_operations", "logistics"):
        raise HTTPException(403, "Executive logistics briefing is restricted")
    if as_of is not None and as_of > date.today():
        raise HTTPException(422, "A future date cannot establish current operations")
    ledger = _load_ledger(user, db)
    try:
        return build_chief_of_staff_briefing(
            user=user, ledger=ledger, as_of=as_of or date.today(), limit=limit,
        )
    except BriefingUnavailable:
        log.warning("Lola briefing scope unavailable")
        raise HTTPException(503, "Briefing evidence unavailable; no all-clear inferred") from None
    except Exception as exc:
        log.warning("Lola briefing evaluation unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, "Lola briefing could not be verified") from None


@router.get("/pricing/snapshots")
async def pricing_snapshot_candidates(
    response: Response,
    limit: int = Query(default=5, ge=1, le=20),
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    """Discover recorded cost snapshots; prices and accounting not certified."""
    response.headers["Cache-Control"] = "private, no-store"
    from app.intelligence.lola.pricing_bridge import list_cost_snapshot_candidates, PricingEvidenceUnavailable
    try:
        return list_cost_snapshot_candidates(company_id=user["company_id"], db=db, limit=limit)
    except PricingEvidenceUnavailable:
        raise HTTPException(503, "Pricing evidence source unavailable") from None


@router.get("/pricing/snapshots/{snapshot_id}")
async def pricing_snapshot_review(
    snapshot_id: UUID,
    response: Response,
    user: dict = Depends(get_pricing_user),
    db=Depends(get_db),
):
    """Conservative evidence audit, no computed margin or accounting mutations."""
    response.headers["Cache-Control"] = "private, no-store"
    from app.intelligence.lola.pricing_bridge import (
        review_cost_snapshot, PricingEvidenceUnavailable, PricingSnapshotNotFound
    )
    try:
        return review_cost_snapshot(company_id=user["company_id"], snapshot_id=snapshot_id, db=db)
    except PricingSnapshotNotFound:
        raise HTTPException(404, "Cost snapshot not found") from None
    except PricingEvidenceUnavailable:
        raise HTTPException(503, "Pricing evidence source unavailable or inconsistent") from None
