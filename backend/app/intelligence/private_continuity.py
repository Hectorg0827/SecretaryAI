"""Private, deterministic cross-channel commitment tracking for Lola.

No mailbox or WhatsApp network access here. Identity is resolved upstream to a
verified, employee-owned contact_id. Never infer identity from matching names.
Only events already authorized for ONE user may be supplied to this function.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


FOLLOW_UP_KINDS = frozenset({"draft_sent", "quote_sent", "followup_sent"})


def canonical_project_key(value: str) -> str:
    """Use the same conservative project key across email and manual channels."""
    import re
    subject = re.sub(r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", value or "", flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", subject.casefold()).strip("-")[:140]



def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Event timestamp is required")
    if parsed.tzinfo is None:
        raise ValueError("Event timestamp must include its timezone")
    return parsed.astimezone(timezone.utc)


def pending_followups(
    events: list[dict],
    *,
    now: datetime | None = None,
    delay: timedelta = timedelta(hours=24),
) -> list[dict]:
    """Suggest unanswered outgoing commitments after the configured delay.

    Events are prefiltered to ONE company and ONE employee by the caller.
    Group by verified contact AND explicit project key; a same-name contact or
    a message about another project must not close a commitment. Any inbound
    response to that project after the outgoing draft closes the pending item,
    even if it arrives via a different connected channel.
    """
    current = _timestamp(now or datetime.now(timezone.utc))
    if delay.total_seconds() < 0:
        raise ValueError("Delay cannot be negative")
    groups: dict[tuple[str, str], list[tuple[datetime, dict]]] = defaultdict(list)
    for event in events:
        contact_id = event.get("contact_id")
        project_key = canonical_project_key(str(event.get("project_key") or ""))
        if not contact_id or not project_key:
            continue  # Unverified identity/project: never manufacture a match.
        try:
            at = _timestamp(event.get("occurred_at"))
        except (TypeError, ValueError):
            continue
        if at <= current:
            groups[(str(contact_id), str(project_key))].append((at, event))

    result = []
    for (contact_id, project_key), timeline in groups.items():
        # Deterministic ordering even when two provider events share a timestamp.
        timeline.sort(key=lambda x: (x[0], str(x[1].get("id", ""))))
        awaiting: tuple[datetime, dict] | None = None
        for at, event in timeline:
            kind = event.get("kind")
            direction = event.get("direction")
            if kind in FOLLOW_UP_KINDS and direction == "outbound":
                awaiting = (at, event)
            elif awaiting and (
                direction == "inbound" and at >= awaiting[0]
                or kind == "closed"
            ):
                awaiting = None
        if not awaiting:
            continue
        at, outbound = awaiting
        due_at = at + delay
        if due_at > current:
            continue
        result.append({
            "event_id": outbound["id"],
            "contact_id": contact_id,
            "project_key": project_key,
            "last_channel": outbound["channel"],
            "last_outbound_at": at.isoformat(),
            "due_at": due_at.isoformat(),
            "summary": outbound.get("summary", ""),
            "reason": "No reply recorded in the available activity after the last draft or quotation",
        })
    result.sort(key=lambda item: (item["due_at"], item["contact_id"], item["project_key"]))
    return result
