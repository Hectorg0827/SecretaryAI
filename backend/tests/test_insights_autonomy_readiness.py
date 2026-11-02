"""Autonomy readiness insight: read-only, tenant-scoped, fail-closed."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import insights
from app.api.deps import get_db
from app.api.insights.autonomy_readiness import analyse_history
from app.auth.rbac import get_current_user

URL = "/api/insights/autonomy-readiness"
_WRITES = ("insert", "update", "upsert", "delete", "rpc")


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, []

    def select(self, columns, **_):
        self.db.selected.append((self.table, columns))
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        self.db.eq_filters.append((self.table, field, value))
        return self

    def gte(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        if self.db.fail:
            raise ConnectionError("database down")
        rows = [
            r for r in self.db.rows
            if not isinstance(r, dict) or all(r.get(f, v) == v for f, v in self.filters)
        ]
        return SimpleNamespace(data=None if self.db.null_data else rows)

    def __getattr__(self, name):
        if name in _WRITES:
            def write(*_, **__):
                self.db.writes.append((self.table, name))
                return self
            return write
        raise AttributeError(name)


class FakeDB:
    """Supabase stand-in that applies eq filters and records any write."""

    def __init__(self, rows=(), fail=False, null_data=False):
        self.rows, self.fail, self.null_data = list(rows), fail, null_data
        self.tables, self.selected, self.eq_filters, self.writes = [], [], [], []

    def table(self, name):
        self.tables.append(name)
        return _Query(self, name)

    def rpc(self, *_, **__):
        self.writes.append(("rpc", "rpc"))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


def _drafts(action_type, status, n, company="tenant-a", created_by="ai", content=None):
    return [
        {"company_id": company, "created_by": created_by, "action_type": action_type,
         "status": status, "content": dict(content or {}), "created_at": "2026-09-01T00:00:00Z"}
        for _ in range(n)
    ]


def _history():
    rows = []
    # Customer emails: 36 as-is, 3 edited, 1 rejected (40 reviewed), 2 pending, 1 expired.
    rows += _drafts("draft_customer_email", "approved", 36)
    rows += _drafts("draft_customer_email", "edited_and_approved", 3)
    rows += _drafts("draft_customer_email", "rejected", 1)
    rows += _drafts("draft_customer_email", "pending", 2)
    rows += _drafts("draft_customer_email", "expired", 1)
    # Purchase orders with recorded amounts under the $5,000 rail: 24/25 as-is.
    rows += _drafts("draft_purchase_order", "approved", 23, content={"amount": 1200})
    rows += _drafts("draft_purchase_order", "approved", 1, content={"total": "4800.00"})
    rows += _drafts("draft_purchase_order", "rejected", 1, content={"amount": 300})
    # Email replies: 59 as-is + 1 edited → strong enough for full autonomy.
    rows += _drafts("email_reply", "approved", 59)
    rows += _drafts("email_reply", "edited_and_approved", 1)
    # Rep assignments mostly rejected → inform only.
    rows += _drafts("draft_rep_assignment", "approved", 4)
    rows += _drafts("draft_rep_assignment", "edited_and_approved", 2)
    rows += _drafts("draft_rep_assignment", "rejected", 9)
    # Reliable, but no guardrails exist for this type → keep approval.
    rows += _drafts("cu_data_entry", "approved", 12)
    # Too little history.
    rows += _drafts("generate_external_report", "approved", 3)
    # Bad rows are skipped and counted, never a 500.
    rows += [
        {"company_id": "tenant-a", "created_by": "ai", "action_type": "draft_customer_email", "status": "weird"},
        {"company_id": "tenant-a", "created_by": "ai", "action_type": None, "status": "approved"},
        {"company_id": "tenant-a", "created_by": "ai", "action_type": "email_reply", "status": "approved",
         "content": {"amount": "not-a-number"}},
    ]
    # Other tenants' drafts and human-created drafts must not count.
    rows += _drafts("draft_customer_email", "rejected", 50, company="tenant-b")
    rows += _drafts("draft_customer_email", "rejected", 50, created_by="user-7")
    return rows


def _client(db, role="owner", company_id="tenant-a"):
    app = FastAPI()
    app.include_router(insights.router, prefix="/api/insights")
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "user-a", "company_id": company_id, "role": role,
    }
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _by_type(body):
    return {row["action_type"]: row for row in body["action_types"]}


def test_reliability_and_recommended_tier_per_action_type():
    db = FakeDB(_history())
    response = _client(db).get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    rows = _by_type(body)

    email = rows["draft_customer_email"]
    assert (email["decided"], email["approved_as_is"], email["approved_with_edits"],
            email["rejected"], email["pending"], email["expired_unreviewed"]) == (40, 36, 3, 1, 2, 1)
    assert email["as_is_rate"] == 0.9
    assert email["accepted_rate"] == 0.975
    assert email["reliability_score"] == pytest.approx(0.7695, abs=1e-4)
    assert email["current_tier"] == 2
    assert email["recommended_tier"] == 3 and email["recommended_level"] == "Act within limits"
    assert email["assessment"] == "ready"
    assert "50+" in email["reason"]

    po = rows["draft_purchase_order"]
    assert po["decided"] == 25 and po["approved_as_is"] == 24
    assert po["reliability_score"] == pytest.approx(0.8046, abs=1e-4)
    assert po["recommended_tier"] == 3

    replies = rows["email_reply"]
    # The malformed amount row is still a valid approval of this type.
    assert replies["decided"] == 61
    assert replies["recommended_tier"] == 4 and replies["recommended_level"] == "Autonomous"

    reps = rows["draft_rep_assignment"]
    assert reps["accepted_rate"] == 0.4
    assert reps["recommended_tier"] == 1 and reps["assessment"] == "inform_only"

    entry = rows["cu_data_entry"]
    assert entry["as_is_rate"] == 1.0
    assert entry["recommended_tier"] == 2 and entry["assessment"] == "keep_approval"

    report = rows["generate_external_report"]
    assert report["assessment"] == "insufficient_history"
    assert report["recommended_tier"] is None and report["reliability_score"] is None

    assert body["rows_skipped"] == 2
    assert body["rows_read"] == 161
    assert body["ready_count"] == 3
    assert body["insufficient_history"] is False
    assert body["company_id"] == "tenant-a"


def test_response_is_stamped_advisory():
    body = _client(FakeDB(_history())).get(URL).json()
    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False
    assert body["settings_changed"] is False


def test_endpoint_changes_nothing():
    db = FakeDB(_history())
    for _ in range(2):
        assert _client(db).get(URL).status_code == 200
    assert db.writes == []
    assert set(db.tables) == {"drafts"}
    assert all(table == "drafts" for table, _ in db.selected)


def test_tenant_scope_comes_from_the_token():
    db = FakeDB(_history())
    response = _client(db, company_id="tenant-b").get(URL, params={"company_id": "tenant-a"})
    assert response.status_code == 200
    body = response.json()
    assert body["company_id"] == "tenant-b"
    assert ("drafts", "company_id", "tenant-b") in db.eq_filters
    assert ("drafts", "created_by", "ai") in db.eq_filters
    # Only tenant-b's 50 rejected emails are visible, not tenant-a's history.
    assert [r["action_type"] for r in body["action_types"]] == ["draft_customer_email"]
    assert body["action_types"][0]["rejected"] == 50


def test_too_little_history_says_so_instead_of_inventing_numbers():
    db = FakeDB(_drafts("draft_customer_email", "approved", 4))
    body = _client(db).get(URL).json()
    assert body["insufficient_history"] is True
    [row] = body["action_types"]
    assert row["assessment"] == "insufficient_history"
    assert row["recommended_tier"] is None

    empty = _client(FakeDB([])).get(URL).json()
    assert empty["action_types"] == [] and empty["insufficient_history"] is True


@pytest.mark.parametrize("db", [FakeDB(fail=True), FakeDB(null_data=True)])
def test_source_failure_is_503_not_an_empty_result(db):
    response = _client(db).get(URL)
    assert response.status_code == 503
    assert "Approval history" in response.json()["detail"]


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_approve_actions_are_refused(role):
    db = FakeDB(_history())
    assert _client(db, role=role).get(URL).status_code == 403
    assert db.tables == []


def test_manager_is_admitted():
    assert _client(FakeDB(_history()), role="manager").get(URL).status_code == 200


def test_purchase_order_guardrails_cap_the_recommendation():
    over = analyse_history(_drafts("draft_purchase_order", "approved", 30, content={"amount": 7500}))
    [row] = over["action_types"]
    assert row["recommended_tier"] == 2
    assert "outside the automatic-action limits" in row["reason"]

    unknown = analyse_history(_drafts("draft_purchase_order", "approved", 30))
    [row] = unknown["action_types"]
    assert row["recommended_tier"] == 2
    assert "don't record an amount" in row["reason"]


def test_unpriced_purchase_orders_are_not_hidden_by_one_priced_order():
    rows = (_drafts("draft_purchase_order", "approved", 99)
            + _drafts("draft_purchase_order", "approved", 1, content={"amount": 100}))
    [row] = analyse_history(rows)["action_types"]
    assert row["proposals_without_amount"] == 99
    assert row["recommended_tier"] == 2 and row["assessment"] == "keep_approval"
    assert "don't record an amount" in row["reason"]


def test_mounted_on_the_paid_insights_prefix():
    from fastapi.routing import iter_route_contexts
    from app.main import app

    paths = {getattr(ctx, "path", "") for ctx in iter_route_contexts(app.routes)}
    assert URL in paths
    assert "/api/insights/governance-check/preview" in paths
    assert "/api/insights/onboarding-plan/preview" in paths


@pytest.mark.parametrize("module_path", [
    "app/api/insights/autonomy_readiness.py",
    "app/api/insights/governance_check.py",
    "app/api/insights/onboarding_plan.py",
])
def test_insight_modules_log_only_constant_messages(module_path):
    source = (Path(__file__).resolve().parents[1] / module_path).read_text(encoding="utf-8")
    for call in ast.walk(ast.parse(source)):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id in {"log", "logger", "logging"}):
            assert len(call.args) == 1 and not call.keywords, module_path
            assert isinstance(call.args[0], ast.Constant), module_path
