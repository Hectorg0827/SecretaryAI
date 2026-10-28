"""Onboarding plan preview: dated checklist from user input; nothing stored or sent."""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import insights
from app.auth.rbac import get_current_user

URL = "/api/insights/onboarding-plan/preview"


def _today():
    return datetime.now(timezone.utc).date()


def _client(role="owner", company_id="tenant-a"):
    app = FastAPI()
    app.include_router(insights.router, prefix="/api/insights")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "user-a", "company_id": company_id, "role": role,
    }
    return TestClient(app)


def _body(**overrides):
    today = _today()
    body = {
        "role_title": "Warehouse associate",
        "department": "warehouse",
        "start_date": (today - timedelta(days=2)).isoformat(),
        "custom_tasks": [{"title": "Order safety boots", "days_from_start": 4, "owner": "Office"}],
        "completed": [{"item_id": "paperwork", "completed_on": (today - timedelta(days=4)).isoformat()}],
    }
    body.update(overrides)
    return body


def test_warehouse_plan_is_dated_and_classified():
    today = _today()
    response = _client().post(URL, json=_body())
    assert response.status_code == 200, response.text
    body = response.json()
    plan = body["plan"]
    items = {i["item_id"]: i for i in plan["items"]}

    # 8 standard + 3 warehouse + 1 custom task.
    assert body["summary"] == {"total": 12, "overdue": 5, "due_soon": 3, "completed": 1, "upcoming": 3}
    assert items["paperwork"]["status"] == "completed"
    assert items["paperwork"]["due_on"] == (today - timedelta(days=5)).isoformat()
    assert items["paperwork"]["completed_on"] == (today - timedelta(days=4)).isoformat()
    assert {k for k, i in items.items() if i["status"] == "overdue"} == {
        "workspace", "accounts", "welcome", "handbook", "safety-training",
    }
    assert {k for k, i in items.items() if i["status"] == "due_soon"} == {
        "warehouse-process", "week-1-checkin", "custom-1",
    }
    assert items["equipment-cert"]["kind"] == "license"
    assert items["equipment-cert"]["status"] == "upcoming"
    assert items["custom-1"]["title"] == "Order safety boots"
    assert items["custom-1"]["owner"] == "Office" and items["custom-1"]["custom"] is True
    assert [i["due_on"] for i in plan["items"]] == sorted(i["due_on"] for i in plan["items"])
    assert len(plan["next_steps"]) == 3
    assert plan["role_title"] == "Warehouse associate"
    assert plan["start_date"] == (today - timedelta(days=2)).isoformat()


def test_custom_only_plan_without_standard_checklist():
    start = _today() + timedelta(days=30)
    body = _client().post(URL, json=_body(
        department="other", include_standard=False, completed=[], start_date=start.isoformat(),
        custom_tasks=[{"title": "Set up delivery van", "kind": "onboarding", "days_from_start": 0}],
    )).json()
    [item] = body["plan"]["items"]
    assert item["status"] == "upcoming" and item["due_on"] == start.isoformat()
    assert body["summary"]["total"] == 1


def test_response_is_advisory_and_persists_nothing():
    body = _client().post(URL, json=_body()).json()
    assert body["status"] == "analysis_only"
    assert body["source"] == "user_supplied"
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False
    assert body["stored"] is False and body["sent"] is False


def test_endpoint_has_no_database_dependency():
    from app.api.deps import get_db
    from app.api.insights import onboarding_plan

    [route] = onboarding_plan.router.routes
    calls = {dep.call for dep in route.dependant.dependencies}
    assert get_db not in calls


def test_form_values_are_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    _client().post(URL, json=_body(role_title="Synthetic-Secret-Title"))
    _client().post(URL, json=_body(completed=[{"item_id": "paperwork", "completed_on": "2999-01-01"}]))
    assert "Synthetic-Secret-Title" not in caplog.text
    assert "Order safety boots" not in caplog.text


@pytest.mark.parametrize("bad", [
    {"department": "space"},
    {"start_date": (datetime.now(timezone.utc).date() + timedelta(days=500)).isoformat()},
    {"start_date": "not-a-date"},
    {"employee_name": "Jane Doe"},                       # no personal data fields accepted
    {"company_id": "tenant-b"},
    {"custom_tasks": [{"title": "x", "days_from_start": 400}]},
    {"custom_tasks": [{"title": "", "days_from_start": 1}]},
    {"custom_tasks": [{"title": f"t{i}", "days_from_start": 1} for i in range(21)]},
    {"completed": [{"item_id": "not-in-plan", "completed_on": "2026-01-01"}]},
    {"completed": [{"item_id": "paperwork", "completed_on": "2999-01-01"}]},   # future completion
    {"completed": [{"item_id": "paperwork", "completed_on": "2026-01-01"}] * 2},
    {"include_standard": False, "custom_tasks": []},
])
def test_invalid_input_is_422(bad):
    assert _client().post(URL, json=_body(**bad)).status_code == 422


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_manage_users_are_refused(role):
    assert _client(role=role).post(URL, json=_body()).status_code == 403


def test_manager_is_admitted_through_view_all():
    # rbac.require_permission treats view_all (owner, manager) as a super-permission.
    assert _client(role="manager").post(URL, json=_body()).status_code == 200


def test_tenant_comes_from_the_token():
    # The plan is computed under the caller's tenant; a body tenant is rejected above.
    response = _client(company_id="tenant-q").post(URL, json=_body())
    assert response.status_code == 200
    assert response.json()["company_id"] == "tenant-q"
