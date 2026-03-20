import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_db
from app.auth.rbac import get_current_user, require_permission

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
async def get_settings(
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
