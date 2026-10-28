"""Private HR onboarding/renewal task calculations; no personnel decisions.

Never expose this module directly as a route. A Gate A-authenticated policy
decision must validate HRTaskAccess before constructing a scoped read model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class HRRequirement:
    company_id: str
    employee_ref: str  # opaque internal reference; no name or other PII
    requirement_id: str
    requirement_kind: str  # onboarding | training | license | policy_ack
    due_on: date
    completed_on: date | None = None
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class HRTaskStatus:
    company_id: str
    employee_ref: str
    requirement_id: str
    status: str
    due_on: date
    source_documented: bool
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class HRBriefing:
    total: int
    overdue: int
    due_soon: int
    completed: int
    source_review_needed: int
    detailed_tasks: tuple[HRTaskStatus, ...] = ()


@dataclass(frozen=True)
class HRTaskAccess:
    """A proposed internal policy envelope, NOT an authentication token."""

    company_id: str
    permission: str
    scoped_employee_refs: frozenset[str]
    policy_decision_id: str


def assess_hr_requirements(
    company_id: str,
    requirements: tuple[HRRequirement, ...],
    *,
    as_of: date,
    due_soon_days: int = 14,
) -> tuple[HRTaskStatus, ...]:
    if not company_id or due_soon_days < 0:
        raise ValueError("Valid tenant and due-soon window required")
    seen: set[tuple[str, str]] = set()
    result = []
    for item in requirements:
        if item.company_id != company_id or not item.employee_ref or not item.requirement_id:
            raise ValueError("Mismatched tenant or missing opaque HR identity")
        key = (item.employee_ref, item.requirement_id)
        if key in seen:
            raise ValueError("Duplicate HR requirement identity")
        seen.add(key)
        if item.completed_on and item.completed_on > as_of:
            raise ValueError("Future completion cannot be treated as verified today")
        documented = bool(item.source_record_id and item.source_revision)
        if not documented:
            status = "source_review_required"
        elif item.completed_on is not None:
            status = "completed"
        elif item.due_on < as_of:
            status = "overdue"
        elif item.due_on <= as_of + timedelta(days=due_soon_days):
            status = "due_soon"
        else:
            status = "upcoming"
        result.append(HRTaskStatus(company_id, item.employee_ref, item.requirement_id, status,
                                   item.due_on, documented,
                                   item.source_record_id, item.source_revision))
    return tuple(result)


def private_hr_briefing(
    company_id: str,
    tasks: tuple[HRTaskStatus, ...],
    *,
    access: HRTaskAccess | None,
) -> HRBriefing:
    """Scope details; caller must independently validate the policy decision ID."""
    if not company_id or access is None or access.company_id != company_id:
        raise PermissionError("HR tenant policy decision required")
    if access.permission not in {"view_hr", "manage_hr"} or not access.policy_decision_id:
        raise PermissionError("Authenticated HR scope required")
    # Employee references alone are not globally unique. Reject a mixed-tenant
    # snapshot rather than counting records that happen to reuse a permitted
    # employee reference in a different company.
    if any(item.company_id != company_id for item in tasks):
        raise PermissionError("Mixed-tenant HR snapshot is not authorized")
    valid_statuses = {"completed", "overdue", "due_soon", "upcoming", "source_review_required"}
    if any(item.status not in valid_statuses or
           item.source_documented != bool(item.source_record_id and item.source_revision) or
           (not item.source_documented and item.status != "source_review_required")
           for item in tasks):
        raise ValueError("Unrecognized or inconsistent HR status")
    # Scope *before* aggregates to prevent counts of other employees leaking.
    visible = tuple(item for item in tasks if item.employee_ref in access.scoped_employee_refs)
    return HRBriefing(
        total=len(visible),
        overdue=sum(t.status == "overdue" for t in visible),
        due_soon=sum(t.status == "due_soon" for t in visible),
        completed=sum(t.status == "completed" for t in visible),
        source_review_needed=sum(t.status == "source_review_required" for t in visible),
        detailed_tasks=visible,
    )
