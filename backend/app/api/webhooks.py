"""
Webhook endpoints — QuickBooks Online and Conductor (QuickBooks Desktop).

A webhook only ever says "something changed for this QuickBooks company". We
verify the sender, work out which of OUR companies that is, and queue a data
refresh for that one company. Nothing in a webhook body is trusted beyond the
identifier, and an identifier we do not recognise is ignored.
"""
import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

REFRESH_TASK = "tasks.data_refresh.refresh_company"
_REFRESH_DEBOUNCE_SECONDS = 60


def _db():
    from app.api.deps import get_db
    return get_db()


def _dispatch(task_name: str, **kwargs) -> bool:
    """Queue a Celery task. Logs but never raises."""
    try:
        from celery_app import celery_app
        celery_app.send_task(task_name, kwargs=kwargs)
        return True
    except Exception as exc:
        log.error("Celery dispatch failed for %s: %s", task_name, exc)
        return False


def _company_for(column: str, value: str) -> str | None:
    """Map an external QuickBooks identifier to our company id."""
    if not value:
        return None
    try:
        result = _db().table("companies").select("id").eq(column, str(value)).execute()
    except Exception as exc:
        log.error("Webhook company lookup failed: %s", exc)
        return None
    rows = result.data or []
    # Exactly one owner, or nothing: never guess between companies.
    return rows[0]["id"] if len(rows) == 1 else None


def _queue_refresh(company_id: str, reason: str) -> bool:
    """Queue one refresh per company per minute, however many events arrive."""
    try:
        from app.auth.jwt import _redis
        if not _redis().set(f"webhook_refresh:{company_id}", "1", nx=True, ex=_REFRESH_DEBOUNCE_SECONDS):
            return False
    except Exception:
        pass  # no debounce available — still refresh
    return _dispatch(REFRESH_TASK, company_id=company_id, reason=reason)


@router.post("/qbo")
async def handle_qbo_webhook(
    request: Request,
    intuit_signature: str = Header(None, alias="intuit-signature"),
):
    """Change notifications from QuickBooks Online (signed by Intuit)."""
    body = await request.body()

    if not _verify_qbo_signature(body, intuit_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")

    queued = 0
    for event in (payload.get("eventNotifications") or []):
        entities = (event.get("dataChangeEvent") or {}).get("entities") or []
        if not any(e.get("name") in ("Invoice", "Item", "Customer", "Payment", "PurchaseOrder") for e in entities):
            continue
        company_id = _company_for("qbo_realm_id", event.get("realmId"))
        if not company_id:
            log.warning("QBO webhook for a realm we do not know — ignored")
            continue
        if _queue_refresh(company_id, "qbo_webhook"):
            queued += 1

    return {"status": "received", "refreshes_queued": queued}


@router.post("/conductor")
async def handle_conductor_webhook(
    request: Request,
    x_conductor_secret: str = Header(None, alias="X-Conductor-Secret"),
):
    """
    Sync notifications from Conductor (QuickBooks Desktop).
    Requires X-Conductor-Secret to equal CONDUCTOR_WEBHOOK_SECRET — a secret
    of its own, not the Conductor API key.
    """
    expected = settings.conductor_webhook_secret
    if not expected or not x_conductor_secret or not hmac.compare_digest(
        expected.encode(), x_conductor_secret.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = payload.get("event")
    company_id = _company_for("conductor_end_user_id", payload.get("endUserId"))
    if not company_id:
        log.warning("Conductor webhook for an end-user we do not know — ignored")
        return {"status": "received"}

    if event_type == "sync.completed":
        _queue_refresh(company_id, "conductor_webhook")

    elif event_type == "sync.failed":
        log.error("Conductor sync.failed for company %s", company_id)
        try:
            from app.utils.audit import write_audit_event
            db = _db()
            db.table("companies").update({"qb_connection_status": "error"}).eq("id", company_id).execute()
            write_audit_event(
                db, company_id=company_id, event_type="quickbooks_sync_failed", actor_id="system",
                capability="quickbooks", metadata={"source": "conductor", "error": str(payload.get("error", ""))[:300]},
            )
        except Exception as exc:
            log.error("Could not record Conductor sync failure: %s", exc)

    return {"status": "received"}


def _verify_qbo_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    # QBO uses HMAC-SHA256 with the webhook verifier token
    verifier = settings.intuit_webhook_verifier_token
    if not verifier:
        # Fail closed — never skip verification, even in dev.
        log.warning("QBO webhook received but INTUIT_WEBHOOK_VERIFIER_TOKEN is not set — rejecting")
        return False
    # Intuit sends the digest BASE64-encoded in `intuit-signature`, not hex.
    expected = base64.b64encode(hmac.new(verifier.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature.strip())
