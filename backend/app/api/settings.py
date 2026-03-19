from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.auth.rbac import get_current_user, require_permission

router = APIRouter()


class CompanySettings(BaseModel):
    timezone: str | None = None
    preferred_language: str | None = None
    morning_briefing_enabled: bool | None = None
    alert_email: str | None = None


@router.get("/")
async def get_settings(user: dict = Depends(get_current_user)):
    return {"settings": {}, "company_id": user["company_id"]}


@router.patch("/")
async def update_settings(
    body: CompanySettings,
    user: dict = Depends(require_permission("manage_users")),
):
    # TODO: Update in Supabase
    return {"status": "updated", "settings": body.model_dump(exclude_none=True)}
