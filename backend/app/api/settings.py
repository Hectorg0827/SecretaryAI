import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_db
from app.auth.rbac import get_current_user, require_permission
from app.config import get_settings
from app.utils.encryption import encrypt

log = logging.getLogger(__name__)
router = APIRouter()

# Settings columns stored in the `companies` table
_ALLOWED_COLUMNS = {
    "timezone",
    "preferred_language",
    "morning_briefing_enabled",
    "alert_email",
}


class CompanySettings(BaseModel):
    timezone: str | None = None
    preferred_language: str | None = None
    morning_briefing_enabled: bool | None = None
    alert_email: str | None = None


@router.get("/")
async def get_settings_endpoint(
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    try:
        result = (
            db.table("companies")
            .select(", ".join(_ALLOWED_COLUMNS))
            .eq("id", company_id)
            .execute()
        )
        settings_data = result.data[0] if result.data else {}
    except Exception as exc:
        log.error("Settings fetch failed: %s", exc)
        settings_data = {}
    return {"settings": settings_data, "company_id": company_id}


@router.patch("/")
async def update_settings(
    body: CompanySettings,
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in _ALLOWED_COLUMNS}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        db.table("companies").update(updates).eq("id", company_id).execute()
    except Exception as exc:
        log.error("Settings update failed for company %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save settings")
    return {"status": "updated", "settings": updates}


# ─── Integrations ─────────────────────────────────────────────────────────────

@router.get("/integrations")
async def get_integrations(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Return real connection status for all integrations.
    Never exposes tokens or API keys — only boolean connected status and metadata.
    """
    company_id = user["company_id"]
    try:
        result = (
            db.table("companies")
            .select(
                "qb_type, qb_connection_status, qbo_realm_id, qbo_connected_at, "
                "conductor_end_user_id, "
                "google_access_token, google_connected_at"
            )
            .eq("id", company_id)
            .execute()
        )
        row = result.data[0] if result.data else {}
    except Exception as exc:
        log.error("Integrations fetch failed for company %s: %s", company_id, exc)
        row = {}

    qbo_connected = (
        row.get("qb_type") == "online"
        and row.get("qb_connection_status") == "connected"
        and bool(row.get("qbo_realm_id"))
    )
    qbd_connected = bool(row.get("conductor_end_user_id"))
    google_connected = bool(row.get("google_access_token"))

    return {
        "quickbooks_online": {
            "connected": qbo_connected,
            "connected_at": row.get("qbo_connected_at"),
            "realm_id": row.get("qbo_realm_id") if qbo_connected else None,
        },
        "quickbooks_desktop": {
            "connected": qbd_connected,
            "end_user_id": row.get("conductor_end_user_id") if qbd_connected else None,
        },
        "gmail": {
            "connected": google_connected,
            "connected_at": row.get("google_connected_at"),
        },
        # Google Sheets reuses the same Google OAuth token as Gmail
        "google_sheets": {
            "connected": google_connected,
            "connected_at": row.get("google_connected_at"),
        },
    }


class QBDConfig(BaseModel):
    end_user_id: str


@router.post("/integrations/qbd")
async def save_qbd(
    body: QBDConfig,
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    """
    Save the QB Desktop Conductor end-user ID for this company.
    The Conductor API key is an operator secret (env var) — users never enter it.
    """
    end_user_id = body.end_user_id.strip()
    if not end_user_id:
        raise HTTPException(status_code=400, detail="end_user_id is required")

    company_id = user["company_id"]
    try:
        db.table("companies").update({
            "conductor_end_user_id": end_user_id,
            "qb_type": "desktop",
            "qb_connection_status": "connected",
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("QB Desktop save failed for company %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save QB Desktop configuration")

    return {"status": "connected", "end_user_id": end_user_id}


@router.delete("/integrations/qbd")
async def disconnect_qbd(
    user: dict = Depends(require_permission("manage_users")),
    db=Depends(get_db),
):
    """Clear QB Desktop configuration."""
    company_id = user["company_id"]
    try:
        db.table("companies").update({
            "conductor_end_user_id": None,
            "qb_connection_status": "disconnected",
        }).eq("id", company_id).execute()
    except Exception as exc:
        log.error("QB Desktop disconnect failed for company %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to disconnect QB Desktop")

    return {"status": "disconnected"}
