"""
Audit event writer — the single way anything is written to `audit_events`.

Every privileged or automatic action (logins, approvals, emails sent,
integrations connected, policy decisions, scheduled sends) calls
write_audit_event(). The table is append-only at the database level.

Two modes:

* required=False (default) — for events where the action has ALREADY happened
  or is harmless (a login, a read). A failed write is logged at ERROR and the
  request continues.
* required=True — for actions with outside effects (sending an email, writing
  to QuickBooks, approving a draft). Call it BEFORE the action. If the audit
  row cannot be written, AuditWriteError is raised and the caller must not
  perform the action: nothing with side effects may happen unrecorded.

The row shape here must match the `audit_events` table
(supabase/migrations). scripts/check_schema_usage.py verifies that in CI.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.utils.safe import log_safe

log = logging.getLogger(__name__)

VALID_ACTION_CLASSES = {"read", "draft", "commit", "destructive"}
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


class AuditWriteError(RuntimeError):
    """A required audit record could not be written — the action must not run."""


def _is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def write_audit_event(
    db,
    *,
    company_id: str,
    event_type: str,
    actor_id: Optional[str] = None,
    action_class: Optional[str] = None,
    capability: Optional[str] = None,
    path_used: Optional[str] = None,
    before_snapshot: Optional[dict] = None,
    after_snapshot: Optional[dict] = None,
    policy_rule_id: Optional[str] = None,
    approved_by: Optional[str] = None,
    metadata: Optional[dict] = None,
    description: Optional[str] = None,
    required: bool = False,
) -> Optional[str]:
    """
    Write one row to audit_events. Returns the event ID, or None if a
    non-required write failed.

    Parameters
    ----------
    db            : Supabase client (service-role)
    company_id    : tenant identifier
    event_type    : e.g. 'draft_approved', 'email_sent', 'user_login',
                    'integration_connected', 'policy_blocked'
    actor_id      : user id, or a system actor ('ai', 'system', 'scheduler')
    action_class  : 'read' | 'draft' | 'commit' | 'destructive'
    capability    : e.g. 'email', 'purchase_orders', 'quickbooks'
    path_used     : 'api' | 'browser' | 'file' | 'computer_use'
    before_snapshot / after_snapshot : state diff (non-PII summary only)
    policy_rule_id: FK to policy_rules if a rule triggered this event
    approved_by   : user id of the approver (for approval events)
    metadata      : freeform extra context (never secrets or message bodies)
    description   : one-line human-readable summary
    required      : raise AuditWriteError instead of continuing on failure
    """
    event_id = str(uuid.uuid4())
    meta: dict[str, Any] = dict(metadata or {})

    # action_class is constrained in the database. Anything else (callers used
    # to pass an action *type* here, which made the insert fail) is kept as
    # metadata instead of breaking the write.
    if action_class is not None and action_class not in VALID_ACTION_CLASSES:
        meta.setdefault("action_type", action_class)
        action_class = None

    row: dict[str, Any] = {
        "id": event_id,
        "company_id": company_id,
        "event_type": event_type,
        "description": description or event_type.replace("_", " "),
        "metadata": meta,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if actor_id is not None:
        row["actor_id"] = str(actor_id)
        if _is_uuid(str(actor_id)):
            row["user_id"] = str(actor_id)
    if action_class is not None:
        row["action_class"] = action_class
    if capability is not None:
        row["capability"] = capability
    if path_used is not None:
        row["path_used"] = path_used
    if before_snapshot is not None:
        row["before_snapshot"] = before_snapshot
    if after_snapshot is not None:
        row["after_snapshot"] = after_snapshot
    if policy_rule_id is not None and _is_uuid(str(policy_rule_id)):
        row["policy_rule_id"] = str(policy_rule_id)
    if approved_by is not None:
        row["approved_by"] = str(approved_by)

    try:
        db.table("audit_events").insert(row).execute()
        return event_id
    except Exception as exc:
        # ERROR, not warning: a missing audit record is an incident.
        log.error(
            "AUDIT WRITE FAILED event_type=%s company=%s required=%s error=%s",
            log_safe(event_type), log_safe(company_id), required, log_safe(exc),
        )
        if required:
            raise AuditWriteError(
                f"Could not record audit event '{event_type}'"
            ) from exc
        return None
