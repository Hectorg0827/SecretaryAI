"""Signed QuickBooks events, resolved to one tenant and a real refresh task.

Webhooks never send mail or mutate accounting data. A failed enqueue returns
503 so the sender can retry. Unknown external IDs are deliberately ignored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()
REFRESH_TASK = "tasks.dashboard_refresh.refresh_dashboard_snapshot"
REFRESH_QUEUE = "dashboard_refresh"
_QBO_REFRESH_ENTITIES = frozenset({
    "Account", "Bill", "BillPayment", "CreditMemo", "Customer", "Invoice",
    "Item", "Payment", "PurchaseOrder", "Vendor", "VendorCredit",
})
_DEBOUNCE_SECONDS = 60
_RELEASE_IF_OWNED = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"


def _db():
    from app.api.deps import get_db
    return get_db()


def _redis():
    import redis
    return redis.from_url(settings.redis_url, decode_responses=True)


def _company_for(column: str, value: object) -> str | None:
    if column not in {"qbo_realm_id", "conductor_end_user_id"}:
        raise ValueError("Unsupported external identifier")
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        return None
    try:
        source_type = "online" if column == "qbo_realm_id" else "desktop"
        rows = (_db().table("companies").select("id,qb_type")
                .eq(column, value).eq("qb_type", source_type)
                .limit(2).execute().data)
    except Exception:
        log.error("Webhook company lookup unavailable")
        raise HTTPException(status_code=503, detail="Webhook lookup temporarily unavailable") from None
    return str(rows[0]["id"]) if len(rows or []) == 1 and rows[0].get("id") else None


def _dispatch(company_id: str) -> None:
    """Import the registered task object; never send an unregistered name."""
    try:
        from tasks.dashboard_refresh import refresh_dashboard_snapshot
        if refresh_dashboard_snapshot.name != REFRESH_TASK:
            raise RuntimeError("Unexpected worker registration")
        # Route webhook work away from the default queue, which may contain
        # unrelated email, billing, or other action-producing tasks.
        job = refresh_dashboard_snapshot.apply_async(
            kwargs={"company_id": company_id}, queue=REFRESH_QUEUE,
        )
        if not getattr(job, "id", None):
            raise RuntimeError("No worker job ID")
    except Exception:
        log.error("Webhook dashboard refresh dispatch unavailable")
        raise HTTPException(status_code=503, detail="Refresh worker temporarily unavailable") from None


def _queue_refresh(company_id: str) -> bool:
    """Coalesce bursts, releasing our Redis claim if dispatch fails."""
    marker = secrets.token_hex(16)
    key = f"webhook_refresh:{company_id}"
    try:
        cache = _redis()
        claimed = cache.set(key, marker, nx=True, ex=_DEBOUNCE_SECONDS)
    except Exception:
        raise HTTPException(status_code=503, detail="Webhook queue temporarily unavailable") from None
    if not claimed:
        return False
    try:
        _dispatch(company_id)
    except HTTPException:
        try:
            cache.eval(_RELEASE_IF_OWNED, 1, key, marker)
        except Exception:
            log.warning("Webhook debounce claim could not be released")
        raise
    return True


async def _json_payload(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            return payload
    except (ValueError, TypeError):
        pass
    raise HTTPException(status_code=400, detail="Invalid webhook payload")


@router.post("/qbo")
async def handle_qbo_webhook(
    request: Request,
    intuit_signature: str | None = Header(None, alias="intuit-signature"),
):
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    if not _verify_qbo_signature(body, intuit_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    payload = await _json_payload(request)
    events = payload.get("eventNotifications") or []
    if not isinstance(events, list) or len(events) > 100:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    queued = 0
    for event in events:
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        change = event.get("dataChangeEvent") or {}
        if not isinstance(change, dict):
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        entities = change.get("entities") or []
        if not isinstance(entities, list) or len(entities) > 500:
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        if not any(isinstance(item, dict) and item.get("name") in _QBO_REFRESH_ENTITIES
                   for item in entities):
            continue
        company_id = _company_for("qbo_realm_id", event.get("realmId"))
        if company_id and _queue_refresh(company_id):
            queued += 1
    return {"status": "received", "refreshes_queued": queued}


@router.post("/conductor")
async def handle_conductor_webhook(
    request: Request,
    x_conductor_secret: str | None = Header(None, alias="X-Conductor-Secret"),
):
    expected = settings.conductor_webhook_secret
    if not expected or not x_conductor_secret or not hmac.compare_digest(expected, x_conductor_secret):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret")
    payload = await _json_payload(request)
    company_id = _company_for("conductor_end_user_id", payload.get("endUserId"))
    if not company_id:
        return {"status": "received", "refreshes_queued": 0}
    if payload.get("event") == "sync.completed":
        return {"status": "received", "refreshes_queued": int(_queue_refresh(company_id))}
    if payload.get("event") == "sync.failed":
        from app.utils.audit import write_audit_event
        db = _db()
        event_id = write_audit_event(
            db, company_id=company_id, event_type="quickbooks_sync_failed",
            actor_id="system", action_class="read", capability="quickbooks",
            path_used="api", metadata={"source": "conductor"},
        )
        if not event_id:
            raise HTTPException(status_code=503, detail="Webhook audit temporarily unavailable")
        try:
            result = db.table("companies").update({"qb_connection_status": "error"}).eq(
                "id", company_id
            ).eq("conductor_end_user_id", payload.get("endUserId")).execute()
        except Exception:
            raise HTTPException(status_code=503, detail="Webhook status update temporarily unavailable") from None
        if len(getattr(result, "data", None) or []) != 1:
            raise HTTPException(status_code=503, detail="Webhook company state changed")
    return {"status": "received", "refreshes_queued": 0}


def _verify_qbo_signature(body: bytes, signature: str | None) -> bool:
    if not signature or not settings.intuit_webhook_verifier_token:
        return False
    expected = base64.b64encode(hmac.new(
        settings.intuit_webhook_verifier_token.encode(), body, hashlib.sha256,
    ).digest()).decode()
    return hmac.compare_digest(expected, signature.strip())
