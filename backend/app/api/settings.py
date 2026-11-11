import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_db
from app.auth.rbac import get_current_user, has_permission, require_permission
from app.config import get_settings
from app.utils.encryption import encrypt
from app.utils.safe import log_safe

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
                "google_access_token, google_connected_at, "
                "ms_access_token, ms_connected_at, ms_account_email"
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
    qbd_connected = (
        bool(row.get("conductor_end_user_id"))
        and row.get("qb_type") == "desktop"
        and row.get("qb_connection_status") == "connected"
    )
    google_connected = bool(row.get("google_access_token"))

    return {
        "quickbooks_online": {
            "connected": qbo_connected,
            "connected_at": row.get("qbo_connected_at"),
            "realm_id": row.get("qbo_realm_id") if qbo_connected else None,
        },
        "quickbooks_desktop": {
            "connected": qbd_connected,
            # The end-user ID identifies this company's books at the QuickBooks
            # bridge — only the role that manages integrations may see it.
            "end_user_id": (
                row.get("conductor_end_user_id")
                if qbd_connected and has_permission(user.get("role"), "manage_integrations")
                else None
            ),
        },
        "gmail": {
            "connected": google_connected,
            "connected_at": row.get("google_connected_at"),
        },
        "outlook": {
            "connected": bool(row.get("ms_access_token")),
            "connected_at": row.get("ms_connected_at"),
            "account": row.get("ms_account_email") if bool(row.get("ms_access_token")) else None,
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
    user: dict = Depends(require_permission("manage_integrations")),
    db=Depends(get_db),
):
    """
    Attach a QB Desktop (Conductor) end-user ID to this company.

    The ID is NOT trusted as typed: all tenants share one operator Conductor
    key, so accepting an arbitrary ID would let one company read another
    company's QuickBooks. The ID is accepted only when Conductor confirms it
    was created for THIS company (sourceId == company_id) and no other company
    already uses it.
    """
    from app.api.setup import conductor_end_user_belongs_to

    end_user_id = body.end_user_id.strip()
    if not end_user_id:
        raise HTTPException(status_code=400, detail="end_user_id is required")

    company_id = user["company_id"]

    if not await conductor_end_user_belongs_to(end_user_id, company_id):
        log.warning(
            "Rejected QB Desktop end-user ID for company %s (user %s): not owned by this company",
            log_safe(company_id), log_safe(user.get("sub")),
        )
        raise HTTPException(
            status_code=403,
            detail="That QuickBooks Desktop connection does not belong to your company. "
                   "Use the desktop app's setup wizard to connect QuickBooks.",
        )

    try:
        clash = (
            db.table("companies").select("id")
            .eq("conductor_end_user_id", end_user_id).neq("id", company_id).execute()
        )
        if clash.data:
            raise HTTPException(status_code=403, detail="That QuickBooks Desktop connection is already in use.")
        db.table("companies").update({
            "conductor_end_user_id": end_user_id,
            "qb_type": "desktop",
            "qb_connection_status": "connected",
        }).eq("id", company_id).execute()
    except HTTPException:
        raise
    except Exception as exc:
        log.error("QB Desktop save failed for company %s: %s", company_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save QB Desktop configuration")

    return {"status": "connected", "end_user_id": end_user_id}


@router.delete("/integrations/qbd")
async def disconnect_qbd(
    user: dict = Depends(require_permission("manage_integrations")),
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
