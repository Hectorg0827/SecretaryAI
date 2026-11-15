"""Authenticated Lola executive briefing, read-only and fail-closed."""
from __future__ import annotations

import logging
from datetime import date

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

    # Check permissions before touching the service-role ledger client.
    _viewer(user)
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
