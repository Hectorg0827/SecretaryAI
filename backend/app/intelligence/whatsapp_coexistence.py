"""Meta WhatsApp Business App Coexistence: *offline* security primitives.

No HTTP route, database write, provider network call or outbound message in
this module. Live WhatsApp ingestion requires independent Meta policy clearance,
employee consent, verified Meta ownership and a separate staged acceptance.
The parser exposes only safe routing metadata, NOT messages or derived content.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any


# These are documented Meta WhatsApp Business Coexistence webhook fields.
# History, contact state and account updates are intentionally excluded from
# delivery until the employee consents to and validates each data class.
_ALLOWED_EVENT_FIELDS = frozenset({"messages", "smb_message_echoes"})
_SIGNATURE = re.compile(r"sha256=([0-9a-f]{64})\Z")


def verify_meta_signature(raw_body: bytes, signature: str | None, app_secret: str) -> bool:
    """Authenticate the exact raw Meta webhook bytes using HMAC-SHA256.

    Do not normalize JSON before validation, and do not accept unsigned data.
    """
    if not isinstance(raw_body, bytes) or not isinstance(app_secret, str) or not app_secret:
        return False
    if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
        return False
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class Owner:
    company_id: str
    user_id: str
    phone_number_id: str


@dataclass(frozen=True)
class RoutingEnvelope:
    company_id: str
    user_id: str
    phone_number_id: str
    field: str
    event_count: int
    # No source message text, customer ID or customer phone is exposed here.


def _validated_owner(entry: Any, phone_number_id: str) -> Owner | None:
    if not isinstance(entry, dict) or entry.get("phone_number_id") != phone_number_id:
        return None
    if entry.get("status") != "active":
        return None
    if not all(
        isinstance(entry.get(k), str) and entry[k].strip()
        for k in ("company_id", "user_id", "meta_verified_at",
                  "employee_consented_at", "policy_clearance_id")
    ):
        return None
    return Owner(
        company_id=entry["company_id"],
        user_id=entry["user_id"],
        phone_number_id=phone_number_id,
    )


def resolve_signed_event_routes(
    payload: Any,
    *,
    phone_owners: dict[str, list[dict]],
    signature_verified: bool,
) -> list[RoutingEnvelope]:
    """Determine candidate user ownership with zero message content extraction.

    This MUST be called only after verifying the Meta signature.
    phone_owners is a server-side lookup of verified Meta phone-number IDs;
    never pass a mapping supplied by the webhook caller or a browser.
    Reject duplicate/ambiguous ownership rather than broadcasting to users.

    A signed, consented payload is still **not permission to ingest text**:
    further Meta policy, employee, recipient and source validations are needed.
    """
    if not signature_verified or not isinstance(payload, dict):
        return []
    if payload.get("object") != "whatsapp_business_account":
        return []
    entries = payload.get("entry")
    if not isinstance(entries, list) or len(entries) > 100:
        return []

    results: list[RoutingEnvelope] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list) or len(changes) > 100:
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            field = change.get("field")
            if field not in _ALLOWED_EVENT_FIELDS:
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            phone_number_id = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
            if not isinstance(phone_number_id, str) or not phone_number_id:
                # Never attribute history/echo based solely on business ID.
                continue
            candidates = phone_owners.get(phone_number_id, [])
            if not isinstance(candidates, list) or len(candidates) != 1:
                continue
            owner = _validated_owner(candidates[0], phone_number_id)
            if owner is None:
                continue
            messages = value.get("messages")
            if not isinstance(messages, list) or not 0 < len(messages) <= 100:
                continue
            # Only provide count and verified owner route, no identifiers/body.
            results.append(RoutingEnvelope(
                company_id=owner.company_id, user_id=owner.user_id,
                phone_number_id=owner.phone_number_id,
                field=field, event_count=len(messages),
            ))
    return results



def validated_whatsapp_routing_candidates(
    *,
    raw_body: bytes,
    signature: str | None,
    app_secret: str,
    phone_owners: dict[str, list[dict]],
    staging_pilot_enabled: bool = False,
    written_meta_policy_clearance: bool = False,
) -> list[RoutingEnvelope]:
    """Pre-ingestion only: verify authenticity, size and explicit legal gates.

    This function contains NO persistence, no user message processing,
    no outgoing sends and no tokens. Real data ingestion still requires a
    separate explicitly reviewed and enabled connector implementation.
    """
    if not staging_pilot_enabled or not written_meta_policy_clearance:
        return []
    if not isinstance(raw_body, bytes) or len(raw_body) > 256 * 1024:
        return []
    if not verify_meta_signature(raw_body, signature, app_secret):
        return []
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeError):
        return []
    return resolve_signed_event_routes(
        payload, phone_owners=phone_owners, signature_verified=True
    )
