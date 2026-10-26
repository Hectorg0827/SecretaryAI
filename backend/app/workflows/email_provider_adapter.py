"""Gated provider boundary for future approved email delivery.

No worker or route calls this module by default. The feature requires two
explicit server-side flags and a verified provider acknowledgment. An HTTP
success or accepted send is not proof of recipient delivery.
"""
import os
from typing import Any


class LiveEmailDeliveryDisabled(RuntimeError):
    pass


class AmbiguousEmailAcknowledgement(RuntimeError):
    """Mail may have been accepted; never automatically send it again."""


async def send_approved_email_via_provider(
    adapter: Any, *, to: str, subject: str, body: str,
) -> dict:
    if (os.getenv("SECRETARYAI_EMAIL_DELIVERY_MODE") != "live"
            or os.getenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED") != "1"):
        raise LiveEmailDeliveryDisabled(
            "Live approved-email delivery has not been authorized"
        )
    if not isinstance(to, str) or not to.strip() or not isinstance(body, str) or not body:
        raise ValueError("Approved email lacks recipient or body")

    # May have a side effect before raising. The caller must reconcile errors.
    receipt = await adapter.send_email_reply(to=to, subject=subject, body=body)
    if not isinstance(receipt, dict) or receipt.get("status") != "sent":
        raise AmbiguousEmailAcknowledgement(
            "Provider returned no explicit accepted-send acknowledgement"
        )
    # Treat 'sent' as accepted by the provider, not inbox delivery.
    return {"status": "accepted_by_provider",
            "provider_message_id": receipt.get("message_id")}
