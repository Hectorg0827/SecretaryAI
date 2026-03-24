"""Feed API — proactive insights for the company dashboard."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.deps import get_db
from app.auth.rbac import get_current_user

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def list_feed(
    limit: int = Query(20, ge=1, le=50),
    unread_only: bool = Query(False),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return proactive feed events for the user's company."""
    company_id = user["company_id"]
    q = (
        db.table("feed_events")
        .select("*")
        .eq("company_id", company_id)
        .eq("is_dismissed", False)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if unread_only:
        q = q.eq("is_read", False)
    result = q.execute()
    events = result.data or []
    unread_count = sum(1 for e in events if not e.get("is_read"))
    return {"events": events, "unread_count": unread_count}


@router.post("/{event_id}/read")
async def mark_read(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    db.table("feed_events").update({"is_read": True}).eq("id", event_id).eq("company_id", user["company_id"]).execute()
    return {"status": "ok"}


@router.post("/{event_id}/dismiss")
async def dismiss_event(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    db.table("feed_events").update({"is_dismissed": True}).eq("id", event_id).eq("company_id", user["company_id"]).execute()
    return {"status": "ok"}
