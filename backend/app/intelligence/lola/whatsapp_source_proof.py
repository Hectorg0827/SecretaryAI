"""Meta WhatsApp Business webhook authenticity and source-metadata boundary.

Verifies X-Hub-Signature-256 on the EXACT unmodified raw request body before
JSON parsing. Extracts only bounded message IDs and status metadata from the
official messages field; never exposes message bodies, phone numbers, contacts,
the signature or app secret. Message-status callbacks alone cannot prove that
a specific employee sent a WhatsApp Business app draft, and webhooks alone
cannot prove an absence of replies or complete historical synchronization.

Internal-only helper. No HTTP route, outbound send, DB write or notification.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

MAX_WEBHOOK_BYTES = 1_048_576
MAX_CHANGES = 100
_MAX_FUTURE_DRIFT = timedelta(minutes=5)
_ALLOWED_STATUSES = frozenset({"sent", "delivered", "read", "failed", "deleted"})


class WebhookProofUnavailable(ValueError):
    """A payload is untrusted, out of scope, or unsupported."""


@dataclass(frozen=True)
class WhatsAppObserved:
    source_ref: str
    event_kind: str
    provider_timestamp: str
    signature_verified: bool = True
    linked_contact_verified: bool = False
    employee_outbound_echo_verified: bool = False
    complete_provider_history: bool = False


@dataclass(frozen=True)
class WebhookReceipt:
    provider: str
    company_id: str
    employee_id: str
    source_revision: str
    events: tuple[WhatsAppObserved, ...]
    signed_payload_verified: bool
    phone_ownership_verified: bool
    webhook_checkpoint_persisted: bool = False
    complete_message_history_verified: bool = False
    reply_status: str = "not_assessed"
    outbound_follow_up_authorized: bool = False
    external_writes_executed: bool = False


def _text(value: object, field: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(c) < 32 for c in value)):
        raise WebhookProofUnavailable("Webhook field invalid: " + field)
    return value


def _timestamp(value: object, now: datetime) -> str:
    timestamp = _text(value, "timestamp")
    if not timestamp.isdecimal() or len(timestamp) > 14:
        raise WebhookProofUnavailable("Provider timestamp malformed")
    try:
        observed = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise WebhookProofUnavailable("Provider timestamp out of range") from None
    if observed > now + _MAX_FUTURE_DRIFT:
        raise WebhookProofUnavailable("Provider event timestamp is in the future")
    return observed.isoformat()


def _signature(raw: bytes, header: str, secret: bytes) -> bool:
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_WEBHOOK_BYTES
            or not isinstance(secret, bytes) or len(secret) < 16
            or not isinstance(header, str) or len(header) != 71
            or not header.startswith("sha256=")):
        return False
    claimed = header[7:]
    if any(char not in "0123456789abcdef" for char in claimed):
        return False
    actual = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(claimed, actual)


def inspect_signed_whatsapp_webhook(
    *,
    raw_body: bytes,
    signature_header: str,
    meta_app_secret: bytes,
    company_id: str,
    employee_id: str,
    expected_waba_id: str,
    expected_phone_number_id: str,
    received_at: datetime,
) -> WebhookReceipt:
    """Check signature and expected account identifiers before any event projection.

    The caller must separately verify this employee's CURRENT Meta connection,
    explicit employee consent and owned business number from the private
    registry. No authorization may be inferred from values in the webhook.
    """
    for value, field in (
        (company_id, "company ID"), (employee_id, "employee ID"),
        (expected_waba_id, "WABA ID"),
        (expected_phone_number_id, "phone number ID"),
    ):
        _text(value, field)
    if (not isinstance(received_at, datetime) or received_at.tzinfo is None
            or received_at.utcoffset() is None):
        raise WebhookProofUnavailable("Verified receipt timestamp unavailable")
    now = received_at.astimezone(timezone.utc)
    if not _signature(raw_body, signature_header, meta_app_secret):
        raise WebhookProofUnavailable("Invalid webhook signature")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise WebhookProofUnavailable("Invalid signed webhook structure") from None
    if not isinstance(payload, Mapping) or payload.get("object") != "whatsapp_business_account":
        raise WebhookProofUnavailable("Unsupported signed webhook object")
    entries = payload.get("entry")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CHANGES:
        raise WebhookProofUnavailable("Invalid webhook entry count")
    events: list[WhatsAppObserved] = []
    count = 0
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("id") != expected_waba_id:
            raise WebhookProofUnavailable("Webhook does not match verified business")
        changes = entry.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
            raise WebhookProofUnavailable("Invalid webhook changes")
        for change in changes:
            count += 1
            if count > MAX_CHANGES:
                raise WebhookProofUnavailable("Webhook change limit exceeded")
            if not isinstance(change, Mapping) or change.get("field") != "messages":
                raise WebhookProofUnavailable("Unsupported WhatsApp webhook field")
            value = change.get("value")
            if (not isinstance(value, Mapping) or value.get("messaging_product") != "whatsapp"
                    or not isinstance(value.get("metadata"), Mapping)
                    or value["metadata"].get("phone_number_id") != expected_phone_number_id):
                raise WebhookProofUnavailable("Webhook business-phone identity mismatch")
            messages = value.get("messages", [])
            statuses = value.get("statuses", [])
            if not isinstance(messages, list) or not isinstance(statuses, list):
                raise WebhookProofUnavailable("Webhook provider event lists malformed")
            if len(messages) + len(statuses) + len(events) > MAX_CHANGES:
                raise WebhookProofUnavailable("Webhook events exceed processing scope")
            for item in messages:
                if not isinstance(item, Mapping):
                    raise WebhookProofUnavailable("Inbound message reference malformed")
                identity = _text(item.get("id"), "message ID")
                _text(item.get("from"), "message sender")
                observed = _timestamp(item.get("timestamp"), now)
                source = "\n".join((company_id, employee_id, expected_waba_id,
                                    expected_phone_number_id, "inbound", identity))
                opaque = hmac.new(meta_app_secret, source.encode("utf-8"),
                                  hashlib.sha256).hexdigest()[:32]
                events.append(WhatsAppObserved("wa-" + opaque, "inbound_observed", observed))
            for item in statuses:
                if not isinstance(item, Mapping):
                    raise WebhookProofUnavailable("Outbound status malformed")
                identity = _text(item.get("id"), "status message ID")
                status = _text(item.get("status"), "status kind")
                if status not in _ALLOWED_STATUSES:
                    raise WebhookProofUnavailable("Unknown provider message status")
                _text(item.get("recipient_id"), "status recipient")
                observed = _timestamp(item.get("timestamp"), now)
                source = "\n".join((company_id, employee_id, expected_waba_id,
                                    expected_phone_number_id, status, identity))
                opaque = hmac.new(meta_app_secret, source.encode("utf-8"),
                                  hashlib.sha256).hexdigest()[:32]
                events.append(WhatsAppObserved("wa-" + opaque, "outbound_status_" + status, observed))
    refs = [event.source_ref for event in events]
    if len(set(refs)) != len(refs):
        raise WebhookProofUnavailable("Duplicate webhook source event in the same receipt")
    # This revision is a keyed digest, not a credential, contact ID, or message body.
    revision = hmac.new(meta_app_secret, raw_body, hashlib.sha256).hexdigest()
    return WebhookReceipt(
        provider="meta_whatsapp_business",
        company_id=company_id, employee_id=employee_id,
        source_revision=revision, events=tuple(events),
        signed_payload_verified=True, phone_ownership_verified=True,
    )
