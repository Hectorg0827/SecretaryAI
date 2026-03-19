"""
Agent API — heartbeat and status endpoints for the Tauri desktop agent.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user

router = APIRouter()


class HeartbeatRequest(BaseModel):
    company_id: str
    agent_version: str
    platform: str
    timestamp: str


@router.post("/heartbeat")
async def receive_heartbeat(
    payload: HeartbeatRequest,
    db=Depends(get_db),
):
    """
    Upsert the desktop agent's last-seen record.
    Called by the Tauri app every 5 minutes.
    """
    now = datetime.now(timezone.utc).isoformat()
    db.table("agent_heartbeats").upsert(
        {
            "company_id": payload.company_id,
            "last_seen": now,
            "agent_version": payload.agent_version,
            "platform": payload.platform,
            "updated_at": now,
        },
        on_conflict="company_id",
    ).execute()
    return {"status": "ok", "server_time": now}


@router.get("/status")
async def get_agent_status(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Return the latest heartbeat row for the authenticated company.
    The frontend uses this to show 'agent connected / disconnected'.
    """
    result = (
        db.table("agent_heartbeats")
        .select("last_seen, agent_version, platform, updated_at")
        .eq("company_id", user["company_id"])
        .execute()
    )
    rows = result.data or []
    if not rows:
        return {"connected": False, "last_seen": None}

    row = rows[0]
    last_seen_str = row.get("last_seen")
    connected = False
    if last_seen_str:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - last_seen_dt
            # Consider connected if last heartbeat was within 12 minutes (2.4× the 5-min interval)
            connected = delta.total_seconds() < 720
        except ValueError:
            pass

    return {
        "connected": connected,
        "last_seen": last_seen_str,
        "agent_version": row.get("agent_version"),
        "platform": row.get("platform"),
    }
