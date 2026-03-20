"""
Push notification endpoints.

Uses the Expo Push Notification service (https://expo.dev/notifications) which
handles APNs (iOS) and FCM (Android) routing automatically — no certificates needed.

SQL to create the device_push_tokens table (run once in Supabase SQL editor):

    CREATE TABLE IF NOT EXISTS device_push_tokens (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        user_id     UUID NOT NULL,
        token       TEXT NOT NULL,
        platform    TEXT NOT NULL CHECK (platform IN ('ios', 'android', 'web')),
        created_at  TIMESTAMPTZ DEFAULT now(),
        UNIQUE (company_id, user_id, token)
    );
    ALTER TABLE device_push_tokens ENABLE ROW LEVEL SECURITY;
    CREATE POLICY "company_isolation" ON device_push_tokens
        FOR ALL USING (company_id = (auth.jwt() ->> 'company_id')::UUID);
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user

log = logging.getLogger(__name__)
router = APIRouter()

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


# ─── Models ───────────────────────────────────────────────────────────────────

class RegisterTokenRequest(BaseModel):
    token: str          # Expo push token, e.g. "ExponentPushToken[xxxxxx]"
    platform: str       # "ios" | "android" | "web"


class SendNotificationRequest(BaseModel):
    title: str
    body: str
    data: Optional[dict] = None
    user_ids: Optional[list[str]] = None  # None = send to all users in company


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register")
async def register_push_token(
    req: RegisterTokenRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Register or refresh a device push token for the current user."""
    if not req.token.startswith("ExponentPushToken["):
        raise HTTPException(status_code=400, detail="Invalid Expo push token format")

    try:
        db.table("device_push_tokens").upsert(
            {
                "company_id": user["company_id"],
                "user_id": user["sub"],
                "token": req.token,
                "platform": req.platform,
            },
            on_conflict="company_id,user_id,token",
        ).execute()
    except Exception as exc:
        log.warning("Failed to store push token: %s", exc)
        # Non-fatal — app still works without notifications
    return {"registered": True}


@router.delete("/unregister")
async def unregister_push_token(
    token: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Remove a device push token (call on logout)."""
    db.table("device_push_tokens").delete().eq("user_id", user["sub"]).eq("token", token).execute()
    return {"unregistered": True}


# ─── Internal helper (used by other API modules to fire notifications) ─────────

async def send_push_notifications(
    db,
    company_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    user_ids: Optional[list[str]] = None,
) -> None:
    """
    Fire-and-forget push notification to all devices of the given users.
    If user_ids is None, sends to all users in the company.
    """
    query = db.table("device_push_tokens").select("token").eq("company_id", company_id)
    if user_ids:
        query = query.in_("user_id", user_ids)
    result = query.execute()
    tokens = [row["token"] for row in (result.data or [])]
    if not tokens:
        return

    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "data": data or {},
            "sound": "default",
        }
        for token in tokens
    ]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
    except Exception as exc:
        log.warning("Push notification delivery failed: %s", exc)
