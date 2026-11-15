"""Authenticated Lola executive briefing, read-only and fail-closed."""
from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from app.api.insights._pricing_access import get_pricing_user

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

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


# ── Stage 3: operator-triggered, internal-only review persistence ─────────────
# Review acknowledgement is deliberately NOT connected to action execution.


class AcknowledgeReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def _review_error(exc: Exception) -> HTTPException:
    from app.intelligence.lola.review_journal import (
        ReviewConflict, ReviewJournalDenied, ReviewJournalUnavailable,
        ReviewRecordNotFound,
    )
    if isinstance(exc, ReviewJournalDenied):
        return HTTPException(403, "Executive review permission denied")
    if isinstance(exc, ReviewRecordNotFound):
        return HTTPException(404, "Review event not found")
    if isinstance(exc, ReviewConflict):
        return HTTPException(409, "Review source or state changed; refresh journal")
    if isinstance(exc, ValueError):
        return HTTPException(422, "Invalid review request")
    if isinstance(exc, ReviewJournalUnavailable):
        log.warning("Lola review journal unavailable (%s)", type(exc).__name__)
        return HTTPException(503, "Review journal unavailable; no persistence assumed")
    log.error("Unexpected Lola review journal failure (%s)", type(exc).__name__)
    return HTTPException(503, "Review journal cannot be verified")


@router.get("/reviews")
async def list_lola_reviews(
    response: Response,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=100000),
    pending_only: bool = Query(False),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    from app.intelligence.lola.review_journal import list_reviews
    try:
        return list_reviews(user=user, db=db, limit=limit, offset=offset, pending_only=pending_only)
    except Exception as exc:
        raise _review_error(exc) from None


@router.post("/reviews/capture")
async def capture_lola_reviews(
    response: Response,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Explicit human scan; records internal evidence markers only, sends nothing."""
    response.headers["Cache-Control"] = "private, no-store"
    from app.api.logistics import _load_ledger
    from app.intelligence.lola.review_journal import capture_reviews, require_active_leader
    try:
        # Do not read the service-role logistics ledger for a stale or denied employee.
        require_active_leader(user, db)
        ledger = _load_ledger(user, db)
        return capture_reviews(user=user, db=db, ledger=ledger)
    except HTTPException:
        raise
    except Exception as exc:
        raise _review_error(exc) from None


@router.post("/reviews/{review_id}/acknowledge")
async def acknowledge_lola_review(
    review_id: UUID,
    body: AcknowledgeReviewRequest,
    response: Response,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    from app.intelligence.lola.review_journal import acknowledge_review
    try:
        return acknowledge_review(
            user=user, db=db, review_id=review_id,
            expected_revision=body.expected_source_revision,
        )
    except Exception as exc:
        raise _review_error(exc) from None



@router.get("/continuity/readiness")
async def private_communication_readiness(
    response: Response,
    contact_id: UUID | None = Query(default=None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Read existing employee-private connection metadata; no message bodies.

    This is an inventory of recorded source readiness, not authority for a
    cross-channel follow-up. It never claims conversation linkage or reply
    completeness. Owner's company role alone does not grant access to another
    employee's private records.
    """
    response.headers["Cache-Control"] = "private, no-store"
    from app.intelligence.lola.existing_private_readiness import (
        inspect_existing_readiness,
    )
    from app.intelligence.lola.identity_registry import (
        IdentityAccessDenied, IdentitySourceUnavailable,
    )
    try:
        return inspect_existing_readiness(
            user=user, db=db, contact_id=contact_id,
        )
    except IdentityAccessDenied:
        raise HTTPException(403, "Employee private communication access denied") from None
    except IdentitySourceUnavailable:
        raise HTTPException(503, "Communication readiness sources unavailable") from None
    except ValueError:
        raise HTTPException(422, "Invalid private contact reference") from None
    except Exception as exc:
        log.warning("Lola private source status unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, "Communication readiness could not be verified") from None
