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
from app.config import get_settings
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
    except Exception:
        log.warning("Failed to store push token")
        raise HTTPException(status_code=503, detail="Notification registration unavailable") from None
    return {"registered": True}


@router.delete("/unregister")
async def unregister_push_token(
    token: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Remove a device push token (call on logout)."""
    db.table("device_push_tokens").delete().eq("company_id", user["company_id"]).eq("user_id", user["sub"]).eq("token", token).execute()
    return {"unregistered": True}


# ─── Internal helper (used by other API modules to fire notifications) ─────────

async def send_push_notifications(
    db,
    company_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    user_ids: Optional[list[str]] = None,
) -> bool:
    """
    Return True only when Expo accepts every addressed message.
    Acceptance is not proof of delivery to a device.
    If user_ids is None, sends to all users in the company. An empty list
    means nobody: it must never widen into a company-wide broadcast.
    """
    if not get_settings().secretaryai_push_delivery_enabled:
        return False
    if user_ids is not None and not user_ids:
        return False
    query = db.table("device_push_tokens").select("token").eq("company_id", company_id)
    if user_ids:
        query = query.in_("user_id", user_ids)
    result = query.execute()
    tokens = [row["token"] for row in (result.data or [])]
    if not tokens:
        return False

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

    accepted = True
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for offset in range(0, len(messages), 100):
                batch = messages[offset:offset + 100]
                response = await client.post(
                    EXPO_PUSH_URL,
                    json=batch,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                tickets = payload.get("data") if isinstance(payload, dict) else None
                if (
                    not isinstance(tickets, list)
                    or len(tickets) != len(batch)
                    or payload.get("errors")
                    or any(
                        not isinstance(ticket, dict)
                        or ticket.get("status") != "ok"
                        or not isinstance(ticket.get("id"), str)
                        or not ticket["id"]
                        for ticket in tickets
                    )
                ):
                    accepted = False
        if not accepted:
            log.warning("Push service did not accept all notifications")
        return accepted
    except Exception:
        # Provider errors may contain device tokens or message text.
        log.warning("Push notification submission failed")
        return False


# Roles that run the business: owners and managers ("view_all" in rbac.py).
LEADERSHIP_ROLES = ("owner", "manager")


async def send_push_to_company_leaders(
    db,
    company_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """
    Push only to the company's active owners and managers.

    Returns True only when Expo accepts every addressed message. Disabled
    delivery, absent leaders/devices or rejected submissions return False.
    Never falls back to every user in the company.
    """
    result = (
        db.table("users")
        .select("id")
        .eq("company_id", company_id)
        .in_("role", list(LEADERSHIP_ROLES))
        .eq("is_active", True)
        .execute()
    )
    user_ids = [row["id"] for row in (result.data or []) if row.get("id")]
    if not user_ids:
        return False
    return await send_push_notifications(
        db=db,
        company_id=company_id,
        title=title,
        body=body,
        data=data,
        user_ids=user_ids,
    )
