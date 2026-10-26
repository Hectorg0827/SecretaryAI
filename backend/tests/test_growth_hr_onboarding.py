"""Opaque HR renewal task and scoping tests, without real personnel data."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.intelligence.hr.onboarding import (
    HRRequirement, HRTaskAccess, assess_hr_requirements, private_hr_briefing,
)


AS_OF = date(2026, 10, 26)


def _task(employee="emp-a", kind="training", due=None, **kwargs):
    task = HRRequirement(
        company_id="synthetic-tenant", employee_ref=employee,
        requirement_id=f"req-{employee}-{kind}", requirement_kind=kind,
        due_on=due or AS_OF + timedelta(days=5),
        source_record_id="synthetic-hr", source_revision="v1",
    )
    return replace(task, **kwargs)


def _access(**kwargs):
    value = HRTaskAccess("synthetic-tenant", "view_hr", frozenset({"emp-a"}), "policy-decision")
    return replace(value, **kwargs)


def test_documented_hr_task_statuses_are_deterministic():
    tasks = (
        _task(due=AS_OF - timedelta(days=1)),
        _task(kind="policy_ack"),
        _task(kind="license", due=AS_OF + timedelta(days=100)),
        _task(kind="onboarding", completed_on=AS_OF),
        _task(kind="source_missing", source_revision=""),
    )
    result = assess_hr_requirements("synthetic-tenant", tasks, as_of=AS_OF)
    assert [x.status for x in result] == ["overdue", "due_soon", "upcoming", "completed", "source_review_required"]
    assert all(x.employee_ref == "emp-a" for x in result)


def test_hr_aggregates_are_limited_to_policy_scoped_employee_refs():
    tasks = (
        _task(employee="emp-a", due=AS_OF - timedelta(days=1)),
        _task(employee="emp-b", due=AS_OF - timedelta(days=1)),
        _task(employee="emp-a", kind="training2", completed_on=AS_OF),
    )
    evaluated = assess_hr_requirements("synthetic-tenant", tasks, as_of=AS_OF)
    briefing = private_hr_briefing("synthetic-tenant", evaluated, access=_access())
    assert briefing.total == 2
    assert briefing.overdue == 1
    assert briefing.completed == 1
    assert all(t.employee_ref == "emp-a" for t in briefing.detailed_tasks)


def test_missing_wrong_tenant_or_unapproved_hr_permission_is_denied():
    tasks = assess_hr_requirements("synthetic-tenant", (_task(),), as_of=AS_OF)
    for grant in (None, _access(company_id="other"), _access(permission="view_inventory"),
                  _access(policy_decision_id="")):
        with pytest.raises(PermissionError):
            private_hr_briefing("synthetic-tenant", tasks, access=grant)


def test_cross_tenant_hr_task_cannot_be_assessed_together():
    with pytest.raises(ValueError):
        assess_hr_requirements("synthetic-tenant", (_task(company_id="other"),), as_of=AS_OF)


def test_future_completion_and_duplicate_identity_fail_closed():
    with pytest.raises(ValueError):
        assess_hr_requirements("synthetic-tenant", (_task(completed_on=AS_OF + timedelta(days=1)),), as_of=AS_OF)
    with pytest.raises(ValueError):
        assess_hr_requirements("synthetic-tenant", (_task(), _task()), as_of=AS_OF)


def test_no_scope_produces_no_employee_count_leak():
    tasks = assess_hr_requirements("synthetic-tenant", (_task(),), as_of=AS_OF)
    briefing = private_hr_briefing("synthetic-tenant", tasks,
                                   access=_access(scoped_employee_refs=frozenset()))
    assert briefing.total == 0
    assert briefing.detailed_tasks == ()
