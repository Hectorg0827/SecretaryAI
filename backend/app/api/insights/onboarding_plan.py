"""New-hire onboarding plan preview built on the private HR task calculations.

The caller enters a department, a start date and any extra tasks; a standard
checklist is laid out around the start date and every task is dated and
classified (overdue / due soon / upcoming / completed) by
``app.intelligence.hr.onboarding``. The new hire is an opaque reference: the
form takes no name, email or other personal data, and nothing is stored,
scheduled or sent. Do not log request values here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.api.insights._common import advisory_response
from app.auth.rbac import require_permission
from app.intelligence.hr.onboarding import (
    HRRequirement,
    HRTaskAccess,
    assess_hr_requirements,
    private_hr_briefing,
)

PREFIX = "/onboarding-plan"
TAG = "insights: people"

router = APIRouter()

NEW_HIRE_REF = "new-hire"          # opaque; the plan is about one future hire
SOURCE_RECORD = "onboarding-form"  # the plan's tasks come from this form
SOURCE_REVISION = "preview"
DUE_SOON_DAYS = 7
MAX_START_OFFSET_DAYS = 365

Department = Literal["sales", "warehouse", "delivery", "office", "management", "other"]
RequirementKind = Literal["onboarding", "training", "license", "policy_ack"]
ItemId = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]

# (item id, kind, days from start, who does it, task)
_Template = tuple[str, str, int, str, str]

_STANDARD: tuple[_Template, ...] = (
    ("paperwork", "onboarding", -3, "Office",
     "Send the offer letter and hiring paperwork (employment eligibility, tax and payroll forms); "
     "check local legal deadlines"),
    ("workspace", "onboarding", -1, "Manager",
     "Prepare the workspace, equipment, keys and uniform if needed"),
    ("accounts", "onboarding", 0, "Owner",
     "Create SecretaryAI and email logins with only the access the role needs"),
    ("welcome", "onboarding", 0, "Manager",
     "Welcome meeting, team introductions and assign an onboarding buddy"),
    ("handbook", "policy_ack", 1, "New hire",
     "Read and acknowledge the employee handbook, safety and data-privacy policies"),
    ("week-1-checkin", "onboarding", 7, "Manager",
     "End-of-first-week check-in: questions, blockers and feedback"),
    ("day-30-review", "onboarding", 30, "Manager",
     "30-day review of goals and progress"),
    ("day-90-review", "onboarding", 90, "Manager",
     "90-day review: confirm the role is working for both sides"),
)

_BY_DEPARTMENT: dict[str, tuple[_Template, ...]] = {
    "sales": (
        ("product-training", "training", 3, "Manager",
         "Product catalogue, pricing and promotions training"),
        ("shadow-calls", "training", 7, "Senior rep",
         "Shadow customer calls and visits; walk through customer records"),
        ("assign-accounts", "onboarding", 14, "Manager",
         "Assign accounts or territory and agree first targets"),
    ),
    "warehouse": (
        ("safety-training", "training", 1, "Warehouse lead",
         "Warehouse safety, lifting and emergency procedures"),
        ("equipment-cert", "license", 14, "Warehouse lead",
         "Confirm equipment certification (e.g. forklift) before operating it"),
        ("warehouse-process", "training", 5, "Warehouse lead",
         "Receiving, picking, packing and stock-count walkthrough"),
    ),
    "delivery": (
        ("driver-license", "license", 0, "Office",
         "Verify the driving licence and any commercial-driving requirements before the first route"),
        ("vehicle-training", "training", 2, "Dispatch",
         "Vehicle checks, routes and proof-of-delivery training"),
        ("ride-along", "training", 5, "Senior driver",
         "Ride-along on established routes"),
    ),
    "office": (
        ("order-entry", "training", 3, "Office lead",
         "Order entry, invoicing and accounting-system walkthrough"),
        ("purchasing-process", "training", 7, "Office lead",
         "Purchasing, receiving and supplier-communication process"),
    ),
    "management": (
        ("approvals-training", "training", 5, "Owner",
         "Approval responsibilities: reviewing AI drafts and purchase orders"),
        ("team-review", "onboarding", 14, "Owner",
         "Review the team, key numbers and open issues"),
    ),
    "other": (),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomTask(_Strict):
    title: Text
    kind: RequirementKind = "onboarding"
    days_from_start: int = Field(strict=True, ge=-60, le=365)
    owner: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]] = None


class CompletedTask(_Strict):
    item_id: ItemId
    completed_on: date


class OnboardingPlanRequest(_Strict):
    role_title: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]] = None
    department: Department
    start_date: date
    include_standard: bool = True
    custom_tasks: list[CustomTask] = Field(default_factory=list, max_length=20)
    completed: list[CompletedTask] = Field(default_factory=list, max_length=50)

    @field_validator("start_date")
    @classmethod
    def start_within_a_year(cls, value: date) -> date:
        today = datetime.now(timezone.utc).date()
        if abs((value - today).days) > MAX_START_OFFSET_DAYS:
            raise ValueError("Start date must be within a year of today")
        return value

    @model_validator(mode="after")
    def unique_completions(self):
        ids = [c.item_id for c in self.completed]
        if len(ids) != len(set(ids)):
            raise ValueError("Each task may be marked complete once")
        return self


def _tasks(body: OnboardingPlanRequest) -> list[dict]:
    templates: list[_Template] = []
    if body.include_standard:
        templates.extend(_STANDARD)
        templates.extend(_BY_DEPARTMENT[body.department])
    tasks = [
        {"item_id": item_id, "kind": kind, "days_from_start": offset,
         "owner": owner, "title": title, "custom": False}
        for item_id, kind, offset, owner, title in templates
    ]
    for index, task in enumerate(body.custom_tasks, start=1):
        tasks.append({
            "item_id": f"custom-{index}", "kind": task.kind,
            "days_from_start": task.days_from_start,
            "owner": task.owner or "Manager", "title": task.title, "custom": True,
        })
    return tasks


@router.post("/preview")
async def onboarding_plan_preview(
    body: OnboardingPlanRequest,
    user: dict = Depends(require_permission("manage_users")),
):
    """Lay out and date a new-hire checklist; nothing is stored or sent."""
    company_id = user["company_id"]
    as_of = datetime.now(timezone.utc).date()
    tasks = _tasks(body)
    if not tasks:
        raise HTTPException(status_code=422, detail="Add at least one task or include the standard checklist")

    by_id = {t["item_id"]: t for t in tasks}
    done = {c.item_id: c.completed_on for c in body.completed}
    unknown = set(done) - set(by_id)
    if unknown:
        raise HTTPException(status_code=422, detail="A completed task is not part of this plan")

    requirements = tuple(
        HRRequirement(
            company_id=company_id, employee_ref=NEW_HIRE_REF,
            requirement_id=t["item_id"], requirement_kind=t["kind"],
            due_on=body.start_date + timedelta(days=t["days_from_start"]),
            completed_on=done.get(t["item_id"]),
            source_record_id=SOURCE_RECORD, source_revision=SOURCE_REVISION,
        )
        for t in tasks
    )
    try:
        statuses = assess_hr_requirements(
            company_id, requirements, as_of=as_of, due_soon_days=DUE_SOON_DAYS,
        )
    except ValueError as exc:
        # e.g. a completion date in the future; never echo request values.
        raise HTTPException(status_code=422, detail="A completion date cannot be in the future") from exc

    # The RBAC dependency above is the policy decision for this one opaque hire.
    briefing = private_hr_briefing(company_id, statuses, access=HRTaskAccess(
        company_id=company_id, permission="manage_hr",
        scoped_employee_refs=frozenset({NEW_HIRE_REF}),
        policy_decision_id="rbac:manage_users",
    ))

    items = []
    for status in briefing.detailed_tasks:
        task = by_id[status.requirement_id]
        items.append({
            "item_id": task["item_id"],
            "title": task["title"],
            "kind": task["kind"],
            "owner": task["owner"],
            "custom": task["custom"],
            "days_from_start": task["days_from_start"],
            "due_on": status.due_on.isoformat(),
            "status": status.status,
            "completed_on": done[task["item_id"]].isoformat() if task["item_id"] in done else None,
        })
    items.sort(key=lambda i: (i["due_on"], i["item_id"]))
    next_steps = [i["title"] for i in items if i["status"] in ("overdue", "due_soon")][:3]

    return advisory_response({
        "company_id": company_id,
        "plan": {
            "role_title": body.role_title or None,
            "department": body.department,
            "start_date": body.start_date.isoformat(),
            "as_of": as_of.isoformat(),
            "due_soon_days": DUE_SOON_DAYS,
            "items": items,
            "next_steps": next_steps,
        },
        "summary": {
            "total": briefing.total,
            "overdue": briefing.overdue,
            "due_soon": briefing.due_soon,
            "completed": briefing.completed,
            "upcoming": briefing.total - briefing.overdue - briefing.due_soon
            - briefing.completed - briefing.source_review_needed,
        },
        "stored": False,
        "sent": False,
    }, source="user_supplied")
