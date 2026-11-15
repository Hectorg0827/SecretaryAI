"""Read-only recorded-case timeline from existing employee-private Lola events.

This is a historical observation list, never provider message synchronization.
Existing manual labels and "gmail_api"/"whatsapp_api" origins do not prove that
a provider was authenticated, all pages were fetched, or an employee confirmed
a cross-channel contact/project mapping. Consequently NO "no reply" result,
reminder authorization, or downstream execution can be produced here.

Every access is scoped to the authenticated employee AND tenant. Do not select
the summary, source_ref, email, phone or private contact display_name.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.intelligence.lola.identity_registry import _actor

CONTACTS = "lola_private_contacts"
EVENTS = "lola_private_events"
_CONTACT_FIELDS = "id,company_id,user_id"
_EVENT_FIELDS = (
    "id,company_id,user_id,contact_id,project_key,channel,direction,"
    "kind,occurred_at,origin,created_at"
)
_CHANNELS = frozenset({"email", "whatsapp", "note", "document"})
_DIRECTIONS = frozenset({"inbound", "outbound"})
_KINDS = frozenset({
    "request", "ack", "draft_sent", "quote_sent",
    "followup_sent", "feedback", "closed", "other",
})
_ORIGINS = frozenset({"manual", "gmail_api", "whatsapp_api", "document"})
PAGE_LIMIT = 50


class TimelineUnavailable(RuntimeError):
    """Source unavailable, unverifiable data or inconsistent read."""


class TimelineNotFound(LookupError):
    """The employee has no private access to the specified contact."""


def _read(query, *, tenant: str, employee: str, maximum: int) -> list[dict]:
    try:
        response = query.execute()
    except Exception:
        raise TimelineUnavailable("Private events unavailable") from None
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or len(rows) > maximum:
        raise TimelineUnavailable("Private events source returned an invalid page")
    if any(
        not isinstance(row, dict)
        or str(row.get("company_id")) != tenant
        or str(row.get("user_id")) != employee
        for row in rows
    ):
        raise TimelineUnavailable("Private source returned a cross-employee record")
    return rows


def _time(value: object) -> str:
    if not isinstance(value, str) or len(value) > 48:
        raise TimelineUnavailable("Recorded event time unavailable")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise TimelineUnavailable("Recorded event time invalid") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise TimelineUnavailable("Recorded event lacks a trusted time zone")
    if dt.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise TimelineUnavailable("Recorded event timestamp is in the future")
    return dt.astimezone(timezone.utc).isoformat()


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise TimelineUnavailable("Invalid recorded event identity")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise TimelineUnavailable("Invalid recorded event identity") from None


def _safe_project_key(project_key: str) -> str:
    if (not isinstance(project_key, str) or not project_key.strip()
            or len(project_key) > 160 or any(ord(char) < 32 for char in project_key)):
        raise ValueError("A nonempty bounded project identifier is required")
    return project_key


def _observed(row: dict, *, tenant: str, employee: str,
              contact_id: UUID, project_key: str) -> dict:
    if (str(row.get("contact_id")) != str(contact_id)
            or row.get("project_key") != project_key):
        raise TimelineUnavailable("Recorded event belongs to another private case")
    identifier = _uuid(row.get("id"))
    channel, direction = row.get("channel"), row.get("direction")
    kind, origin = row.get("kind"), row.get("origin")
    if (not isinstance(channel, str) or channel not in _CHANNELS
            or not isinstance(direction, str) or direction not in _DIRECTIONS
            or not isinstance(kind, str) or kind not in _KINDS
            or not isinstance(origin, str) or origin not in _ORIGINS):
        raise TimelineUnavailable("Recorded event has unsupported source metadata")
    # A mislabeled provider/channel pair is not used even as a historical
    # provider-labeled event. A label alone never grants provider authority.
    if (origin == "gmail_api" and channel != "email") or (
        origin == "whatsapp_api" and channel != "whatsapp"
    ):
        raise TimelineUnavailable("Recorded event origin/channel conflict")
    source_class = (
        "provider_labeled_but_unverified_completeness"
        if origin in ("gmail_api", "whatsapp_api")
        else "manual_or_document_record_not_provider_verified"
    )
    material = "\n".join((tenant, employee, str(contact_id), identifier))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return {
        "event_ref": "recorded-" + digest[:24],
        "channel": channel,
        "direction": direction,
        "kind": kind,
        "occurred_at": _time(row.get("occurred_at")),
        "source_class": source_class,
        "provider_sync_verified": False,
        "message_delivery_verified": False,
        "work_item_identity_verified": False,
    }


def list_private_case_events(
    *, user: dict, db, contact_id: UUID, project_key: str,
    limit: int = 20, offset: int = 0,
) -> dict:
    tenant, employee = _actor(user, db)
    if not isinstance(contact_id, UUID):
        raise ValueError("Contact ID must be a UUID")
    case = _safe_project_key(project_key)
    if (type(limit) is not int or not 1 <= limit <= PAGE_LIMIT
            or type(offset) is not int or not 0 <= offset <= 5000):
        raise ValueError("Invalid page size or offset")
    # Verify the contact is employee-owned, including when no events exist.
    try:
        cq = (db.table(CONTACTS).select(_CONTACT_FIELDS)
              .eq("company_id", tenant).eq("user_id", employee)
              .eq("id", str(contact_id)).limit(2))
    except Exception:
        raise TimelineUnavailable("Private contact source unavailable") from None
    contacts = _read(cq, tenant=tenant, employee=employee, maximum=2)
    if not contacts:
        raise TimelineNotFound("Private contact unavailable")
    if len(contacts) != 1 or str(contacts[0].get("id")) != str(contact_id):
        raise TimelineUnavailable("Ambiguous private contact identity")
    try:
        query = (db.table(EVENTS).select(_EVENT_FIELDS)
                 .eq("company_id", tenant).eq("user_id", employee)
                 .eq("contact_id", str(contact_id)).eq("project_key", case)
                 .order("occurred_at", desc=True).order("id", desc=True)
                 .range(offset, offset + limit))
    except Exception:
        raise TimelineUnavailable("Private event query unavailable") from None
    rows = _read(query, tenant=tenant, employee=employee, maximum=limit + 1)
    seen: set[str] = set()
    observations: list[dict] = []
    for row in rows:
        event = _observed(
            row, tenant=tenant, employee=employee,
            contact_id=contact_id, project_key=case,
        )
        ref = event["event_ref"]
        if ref in seen:
            raise TimelineUnavailable("Duplicate private event identity")
        seen.add(ref)
        observations.append(event)
    return {
        "source": "existing_employee_private_recorded_events",
        "state": "recorded_events" if observations else "no_events_recorded_in_this_page",
        "events": observations[:limit],
        "next_offset": offset + limit if len(observations) > limit else None,
        "history_completeness": "unknown",
        "pagination_consistency": "best_effort_offset_not_verified_sync",
        "provider_coverage_verified": False,
        "cross_channel_link_verified": False,
        "reply_status": "not_assessed",
        "eligible_for_follow_up_engine": False,
        "messages_sent": False,
        "external_writes_executed": False,
        "coverage_note": (
            "These are employee-owned recorded events, not a complete provider "
            "mailbox or WhatsApp history. No response or silence conclusion "
            "is possible without separate provider-authenticated checkpoints."
        ),
    }
