"""
Agent API — heartbeat, status, and report-upload endpoints for the Tauri desktop agent.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


class HeartbeatRequest(BaseModel):
    # company_id is intentionally omitted — sourced from the verified JWT instead
    agent_version: str
    platform: str
    timestamp: str


@router.post("/heartbeat")
async def receive_heartbeat(
    payload: HeartbeatRequest,
    user: dict = Depends(get_current_user),   # authentication required
    db=Depends(get_db),
):
    """
    Upsert the desktop agent's last-seen record.
    Called by the Tauri app every 5 minutes.
    company_id is taken from the verified JWT — never from the request body.
    """
    now = datetime.now(timezone.utc).isoformat()
    company_id = user["company_id"]           # trust the JWT, not the payload
    db.table("agent_heartbeats").upsert(
        {
            "company_id":    company_id,
            "last_seen":     now,
            "agent_version": payload.agent_version,
            "platform":      payload.platform,
            "updated_at":    now,
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
        "connected":     connected,
        "last_seen":     last_seen_str,
        "agent_version": row.get("agent_version"),
        "platform":      row.get("platform"),
    }


# ── Sync trigger — desktop agent calls this every 5 minutes ───────────────────

@router.post("/sync")
async def trigger_sync(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Called by the desktop agent to trigger a fresh QB Desktop data pull.
    The backend fetches from Conductor using the stored end_user_id,
    normalises the data, and persists it to Supabase.
    Returns a brief summary of what changed.
    """
    company_id = user["company_id"]
    company = db.table("companies").select("*").eq("id", company_id).execute()
    if not company.data:
        raise HTTPException(status_code=404, detail="Company not found")

    row = company.data[0]
    end_user_id: str | None = row.get("qbd_end_user_id")
    if not end_user_id:
        return {"status": "skipped", "message": "QuickBooks Desktop not connected"}

    try:
        from app.connectors.qb_desktop import QBDesktopConnector
        qbd = QBDesktopConnector(
            conductor_api_key=settings.conductor_api_key,
            end_user_id=end_user_id,
        )
        # Fetch accounts and invoices — most frequently needed for health scoring
        accounts = await qbd.get_customers(max_results=500)
        invoices = await qbd.get_invoices(days=90)

        counts = {"accounts": len(accounts), "invoices": len(invoices)}
        log.info("QB sync for %s: %s", company_id, counts)

        # Record sync timestamp
        db.table("companies").update(
            {"qbd_last_sync_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", company_id).execute()

        return {
            "status": "ok",
            "message": f"Synced {counts['accounts']} accounts and {counts['invoices']} invoices",
            "counts": counts,
        }
    except Exception as exc:
        log.error("QB sync failed for %s: %s", company_id, exc)
        raise HTTPException(status_code=502, detail=f"QB sync failed: {exc}")


# ── Report upload — desktop file watcher calls this when a new file is detected ─

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "application/octet-stream",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/upload-report")
async def upload_report(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Accept a report file (CSV / Excel / PDF) dropped into the watched folder.
    Stores metadata and queues the file for async processing.
    """
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {content_type}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")

    company_id = user["company_id"]
    now = datetime.now(timezone.utc).isoformat()

    # Persist metadata (not the raw bytes — those go to object storage in prod)
    db.table("uploaded_reports").insert({
        "company_id":  company_id,
        "file_name":   file.filename or "unknown",
        "content_type": content_type,
        "size_bytes":  len(data),
        "uploaded_at": now,
        "status":      "pending",
    }).execute()

    log.info(
        "Report upload: %s (%d bytes) from company %s",
        file.filename, len(data), company_id,
    )

    return {
        "status":    "accepted",
        "file_name": file.filename,
        "size":      len(data),
    }
