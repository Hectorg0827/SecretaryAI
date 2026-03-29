"""
Audit event writer — thin wrapper around the audit_events table (Phase 1 schema).

All privileged actions (approvals, logins, connector registrations, policy decisions)
should call write_audit_event() for a durable, append-only audit trail.

The function is intentionally non-fatal: if the write fails (e.g. during DB outage)
it logs a warning and continues. Audit writes must never break the happy path.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


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
) -> Optional[str]:
    """
    Write a single row to audit_events. Returns the event ID or None on failure.

    Parameters
    ----------
    db            : Supabase client (service-role)
    company_id    : tenant identifier
    event_type    : e.g. 'draft_approved', 'draft_rejected', 'user_login',
                    'connector_registered', 'workflow_started', 'policy_blocked'
    actor_id      : user_id or system identifier
    action_class  : 'read' | 'draft' | 'commit' | 'destructive'
    capability    : e.g. 'customers', 'inventory'
    path_used     : 'api' | 'browser' | 'file' | 'computer_use'
    before_snapshot / after_snapshot : state diff (non-PII summary only)
    policy_rule_id: FK to policy_rules if a rule triggered this event
    approved_by   : user_id of the approver (for approval events)
    metadata      : freeform extra context
    """
    event_id = str(uuid.uuid4())
    try:
        row: dict[str, Any] = {
            "id": event_id,
            "company_id": company_id,
            "event_type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if actor_id is not None:
            row["actor_id"] = actor_id
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
        if policy_rule_id is not None:
            row["policy_rule_id"] = policy_rule_id
        if approved_by is not None:
            row["approved_by"] = approved_by
        if metadata is not None:
            row["metadata"] = metadata

        db.table("audit_events").insert(row).execute()
        return event_id

    except Exception as exc:
        log.warning(
            "audit_events write failed (non-fatal): event_type=%s company=%s error=%s",
            event_type,
            company_id,
            exc,
        )
        return None
