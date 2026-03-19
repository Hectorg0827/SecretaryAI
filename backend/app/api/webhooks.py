"""
Webhook endpoints — QBO webhooks and Conductor event callbacks.
"""
import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings

settings = get_settings()
router = APIRouter()


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
                # Refresh account and sales data
                pass  # TODO: trigger background refresh
            elif entity_name == "Item":
                # Inventory changed — re-evaluate stock alerts
                pass  # TODO: trigger inventory check
            elif entity_name == "Customer":
                # Customer updated — refresh account record
                pass  # TODO: trigger account refresh

    return {"status": "received"}


@router.post("/conductor")
async def handle_conductor_webhook(request: Request):
    """
    Receive event notifications from Conductor (QB Desktop sync events).
    """
    payload = await request.json()
    event_type = payload.get("event")

    if event_type == "sync.completed":
        # QB Desktop sync finished — process updated data
        company_id = payload.get("endUserId")
        pass  # TODO: trigger data refresh pipeline

    elif event_type == "sync.failed":
        # Alert the user that QB Desktop sync broke
        pass  # TODO: send sync break alert

    return {"status": "received"}


def _verify_qbo_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    # QBO uses HMAC-SHA256 with the webhook verifier token
    verifier = settings.intuit_webhook_verifier_token if hasattr(settings, "intuit_webhook_verifier_token") else ""
    if not verifier:
        return True  # Skip verification in dev if token not configured
    expected = hmac.new(verifier.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
