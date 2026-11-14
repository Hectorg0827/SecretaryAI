"""Extract private relationship events from an employee's authorized Gmail data.

Only map to explicitly confirmed employee-owned contacts. No heuristics that
connect two people by name, domain, or business account. No shared CRM writes.
"""
from __future__ import annotations

import re
from email.utils import parseaddr, parsedate_to_datetime
from datetime import timezone


def _project_key(subject: str) -> str:
    text = re.sub(r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", subject or "", flags=re.I)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:140]


def map_gmail_to_private_events(messages: list[dict], contacts: list[dict]) -> list[dict]:
    """Only convert messages with exactly ONE verified contact email match."""
    addresses: dict[str, list[dict]] = {}
    for c in contacts:
        if c.get("identity_verified") and c.get("email"):
            addresses.setdefault(str(c["email"]).strip().lower(), []).append(c)

    events = []
    for message in messages:
        message_id = message.get("id")
        if not message_id:
            continue
        sent = "SENT" in (message.get("labels") or [])
        participant = message.get("to", "") if sent else message.get("from", "")
        # Avoid silently attributing messages with multiple recipients.
        if "," in participant or ";" in participant:
            continue
        address = parseaddr(participant)[1].strip().lower()
        matches = addresses.get(address, [])
        if len(matches) != 1:
            continue
        project = _project_key(message.get("subject", ""))
        if not project:
            continue
        try:
            occurred = parsedate_to_datetime(message.get("date", ""))
            if occurred.tzinfo is None:
                continue
            occurred = occurred.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, IndexError):
            continue
        events.append({
            "contact_id": matches[0]["id"], "project_key": project,
            "channel": "email",
            "direction": "outbound" if sent else "inbound",
            "kind": "ack" if sent else "feedback",
            "occurred_at": occurred,
            "summary": str(message.get("snippet") or "")[:400],
            "source_ref": f"gmail:{message_id}",
        })
    return events
