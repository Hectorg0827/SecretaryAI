"""Staging-only Meta WhatsApp Business Platform webhook boundary.

This acknowledges valid signed test callbacks and verifies server-side owner
routing metadata ONLY. No WhatsApp message text, identifiers, media or metadata
are persisted, logged, injected into Lola or forwarded to any user. This is NOT
a live messaging connector; provisioning/subscription is still external.

The webhook is not bearer-authenticated because Meta cannot send the user's
Lola JWT. Instead: staging/policy gates, nonempty verify-token for challenge,
and raw-body X-Hub-Signature-256 HMAC for callbacks.
"""
from __future__ import annotations

import hmac
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import get_settings
from app.api.deps import get_db
from app.intelligence.whatsapp_coexistence import (
    verify_meta_signature, validated_whatsapp_routing_candidates,
)

router = APIRouter()
_MAX_BYTES = 256 * 1024
_GRAPH_ID = re.compile(r"^[0-9]{5,35}$")


def _pilot_allowed(config) -> bool:
    """Require every independent approval; default OFF, even on staging."""
    return bool(
        config.secretaryai_deployment_env.strip().lower() == "staging"
        and config.lola_private_intelligence_enabled
        and config.lola_meta_webhooks_enabled
        and config.lola_meta_app_review_approved
        and config.lola_meta_ai_policy_approved
        and config.lola_meta_waba_privacy_approved
        and config.lola_meta_policy_clearance_reference.strip()
        and config.lola_meta_app_secret
        and config.lola_meta_webhook_verify_token
    )


@router.get("/callback")
def verify_callback(
    mode: str = Query(default="", alias="hub.mode"),
    verify_token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta one-time GET challenge; never use browser-supplied data as consent."""
    cfg = get_settings()
    if not _pilot_allowed(cfg):
        raise HTTPException(status_code=404, detail="Not found")
    if not (
        mode == "subscribe"
        and verify_token
        and hmac.compare_digest(verify_token, cfg.lola_meta_webhook_verify_token)
        and challenge and len(challenge) <= 256
    ):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return PlainTextResponse(challenge)


def _signed_phone_ids(payload: object) -> set[str]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return set()
    entries = payload.get("entry")
    if not isinstance(entries, list) or len(entries) > 100:
        return set()
    result: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list) or len(changes) > 100:
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            if change.get("field") not in ("messages", "smb_message_echoes"):
                continue
            value = change.get("value")
            meta = value.get("metadata") if isinstance(value, dict) else None
            phone_id = meta.get("phone_number_id") if isinstance(meta, dict) else None
            if isinstance(phone_id, str) and _GRAPH_ID.fullmatch(phone_id):
                result.add(phone_id)
    # No public endpoint must load an unbounded list of employee phone owners.
    return result if len(result) <= 25 else set()


@router.post("/callback")
async def receive_callback(request: Request, db=Depends(get_db)):
    """Validate only. Return no message metadata, never process real chats."""
    cfg = get_settings()
    if not _pilot_allowed(cfg):
        raise HTTPException(status_code=404, detail="Not found")

    if request.headers.get("content-length"):
        try:
            if int(request.headers["content-length"]) > _MAX_BYTES:
                raise HTTPException(status_code=413, detail="Payload too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Malformed content length") from None

    raw_body = await request.body()
    if len(raw_body) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    signature = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(raw_body, signature, cfg.lola_meta_app_secret):
        raise HTTPException(status_code=403, detail="Invalid Meta webhook signature")
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Invalid Meta webhook") from None

    ids = _signed_phone_ids(payload)
    owners: dict[str, list[dict]] = {phone_id: [] for phone_id in ids}
    if ids:
        try:
            rows = (db.table("lola_employee_whatsapp_connections")
                .select("company_id,user_id,phone_number_id,status,meta_verified_at,"
                        "employee_consented_at,employee_revoked_at,policy_clearance_id")
                .in_("phone_number_id", sorted(ids))
                .eq("status", "active")
                .execute()).data or []
        except Exception:
            raise HTTPException(status_code=503, detail="Owner verification unavailable") from None
        for row in rows:
            phone_id = row.get("phone_number_id")
            if phone_id in owners and not row.get("employee_revoked_at"):
                owners[phone_id].append(row)

    # Routing inspection only; the returned candidate list is never shared,
    # persisted, logged or dispatched to Lola. This is an acceptance endpoint,
    # not authorization to read WhatsApp messages or do AI inference.
    validated_whatsapp_routing_candidates(
        raw_body=raw_body,
        signature=signature,
        app_secret=cfg.lola_meta_app_secret,
        phone_owners=owners,
        staging_pilot_enabled=True,
        written_meta_policy_clearance=True,
    )
    return {"acknowledged": True, "messages_processed": 0, "messages_stored": 0}
