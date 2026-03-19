"""
Webhook handlers for real-time change notifications from:
- QuickBooks Online (Intuit)
- Conductor (QB Desktop sync events)

These handlers are called from app/api/webhooks.py and trigger
background data refresh tasks.
"""
import logging
from typing import Callable, Awaitable

log = logging.getLogger(__name__)

# Type for async refresh callbacks
RefreshCallback = Callable[[str, str], Awaitable[None]]


class QBOWebhookHandler:
    """
    Processes Intuit QuickBooks Online webhook payloads.
    Intuit sends notifications immediately when data changes.
    """

    def __init__(
        self,
        on_invoice_change: RefreshCallback,
        on_customer_change: RefreshCallback,
        on_item_change: RefreshCallback,
    ):
        self._on_invoice = on_invoice_change
        self._on_customer = on_customer_change
        self._on_item = on_item_change

    async def handle(self, payload: dict) -> None:
        """Process a QBO webhook notification payload."""
        for notification in payload.get("eventNotifications", []):
            realm_id = notification.get("realmId", "")  # = company QB realm

            for entity_event in notification.get("dataChangeEvent", {}).get("entities", []):
                entity_name = entity_event.get("name")
                entity_id = entity_event.get("id", "")
                operation = entity_event.get("operation", "")  # Create, Update, Delete, Void

                log.info(
                    "QBO webhook: %s %s (realm=%s, op=%s)",
                    entity_name, entity_id, realm_id, operation,
                )

                try:
                    if entity_name == "Invoice":
                        await self._on_invoice(realm_id, entity_id)
                    elif entity_name == "Customer":
                        await self._on_customer(realm_id, entity_id)
                    elif entity_name in ("Item", "InventoryItem"):
                        await self._on_item(realm_id, entity_id)
                except Exception as e:
                    log.error(
                        "Error handling QBO webhook %s/%s: %s",
                        entity_name, entity_id, e,
                    )


class ConductorWebhookHandler:
    """
    Processes Conductor webhook events for QB Desktop sync lifecycle.
    """

    def __init__(
        self,
        on_sync_completed: Callable[[str], Awaitable[None]],
        on_sync_failed: Callable[[str, str], Awaitable[None]],
    ):
        self._on_completed = on_sync_completed
        self._on_failed = on_sync_failed

    async def handle(self, payload: dict) -> None:
        """Process a Conductor event notification."""
        event_type = payload.get("event", "")
        end_user_id = payload.get("endUserId", "")

        log.info("Conductor webhook: %s (endUser=%s)", event_type, end_user_id)

        try:
            if event_type == "sync.completed":
                await self._on_completed(end_user_id)
            elif event_type == "sync.failed":
                error_msg = payload.get("error", "Unknown error")
                await self._on_failed(end_user_id, error_msg)
            elif event_type == "auth.connected":
                log.info("Conductor: end user %s connected QB Desktop", end_user_id)
            elif event_type == "auth.disconnected":
                log.warning("Conductor: end user %s disconnected QB Desktop", end_user_id)
        except Exception as e:
            log.error("Error handling Conductor webhook %s: %s", event_type, e)
