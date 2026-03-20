"""
Webhook endpoints — QBO webhooks and Conductor event callbacks.
Webhooks trigger background Celery tasks to refresh data asynchronously.
"""
import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()


def _dispatch(task_name: str, **kwargs) -> None:
    """Fire-and-forget Celery task dispatch. Logs but never raises."""
    try:
        from celery_app import celery_app
        celery_app.send_task(task_name, kwargs=kwargs)
    except Exception as exc:
        log.warning("Celery dispatch failed for %s: %s", task_name, exc)


@router.post("/qbo")
async def handle_qbo_webhook(
    request: Request,
    intuit_signature: str = Header(None, alias="intuit-signature"),
):
    """
    Receive real-time change notifications from QuickBooks Online.
    QBO sends a notification whenever data changes (invoices, customers, items).
    """
    body = await request.body()

    # Verify webhook signature
    if not _verify_qbo_signature(body, intuit_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()

    for event in payload.get("eventNotifications", []):
        company_id = event.get("realmId")
        for entity_event in event.get("dataChangeEvent", {}).get("entities", []):
            entity_name = entity_event.get("name")
            entity_id = entity_event.get("id")
            operation = entity_event.get("operation")  # Create, Update, Delete, Merge, Void

            if entity_name == "Invoice":
                _dispatch("tasks.qb.sync_orders", company_realm_id=company_id)
                log.info("QBO Invoice %s/%s → sync_orders dispatched", company_id, entity_id)
            elif entity_name == "Item":
                _dispatch("tasks.qb.sync_inventory", company_realm_id=company_id)
                log.info("QBO Item %s/%s → sync_inventory dispatched", company_id, entity_id)
            elif entity_name == "Customer":
                _dispatch("tasks.qb.sync_customers", company_realm_id=company_id)
                log.info("QBO Customer %s/%s → sync_customers dispatched", company_id, entity_id)

    return {"status": "received"}


@router.post("/conductor")
async def handle_conductor_webhook(
    request: Request,
    x_conductor_secret: str = Header(None, alias="X-Conductor-Secret"),
):
    """
    Receive event notifications from Conductor (QB Desktop sync events).
    Requires X-Conductor-Secret header matching settings.conductor_api_key.
    """
    expected = settings.conductor_api_key
    if not expected or not x_conductor_secret or not hmac.compare_digest(expected, x_conductor_secret):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret")

    payload = await request.json()
    event_type = payload.get("event")

    if event_type == "sync.completed":
        company_id = payload.get("endUserId")
        _dispatch("tasks.qb.sync_all", company_id=company_id)
        log.info("Conductor sync.completed for %s → sync_all dispatched", company_id)

    elif event_type == "sync.failed":
        company_id = payload.get("endUserId")
        error_msg = payload.get("error", "Unknown sync error")
        log.error("Conductor sync.failed for %s: %s", company_id, error_msg)
        # Dispatch a notification task so the user is alerted in-app
        _dispatch("tasks.notifications.sync_failed_alert", company_id=company_id, error=error_msg)

    return {"status": "received"}


def _verify_qbo_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    # QBO uses HMAC-SHA256 with the webhook verifier token
    verifier = getattr(settings, "intuit_webhook_verifier_token", "")
    if not verifier:
        # Fail closed — never skip verification, even in dev.
        # Set INTUIT_WEBHOOK_VERIFIER_TOKEN in .env to enable QBO webhooks.
        log.warning("QBO webhook received but INTUIT_WEBHOOK_VERIFIER_TOKEN is not set — rejecting")
        return False
    expected = hmac.new(verifier.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
