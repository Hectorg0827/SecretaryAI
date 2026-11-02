"""Sync health insight: read-only, tenant-scoped, explicit about missing checks.

All tenants, products and results are synthetic. The database is an in-memory
fake that applies the same filters a Supabase query would; nothing external
is contacted and nothing is written.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app

URL = "/api/insights/sync-health"


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class FakeQuery:
    def __init__(self, db, rows):
        self._db = db
        self._rows = list(rows)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self._db.eq_calls.append((field, value))
        self._rows = [r for r in self._rows if r.get(field) == value]
        return self

    def order(self, field, desc=False):
        self._rows.sort(key=lambda r: r.get(field) or "", reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        if self._db.fail:
            raise ConnectionError("synthetic database offline")
        return SimpleNamespace(data=self._rows)


class FakeDB:
    def __init__(self, rows=(), fail=False):
        self.rows = list(rows)
        self.fail = fail
        self.tables: list[str] = []
        self.eq_calls: list[tuple] = []

    def table(self, name):
        self.tables.append(name)
        return FakeQuery(self, self.rows)


def _row(company_id, hours_ago, status="healthy", checks_run=2, discrepancies=(), **extra):
    checked_at = _iso(hours_ago)
    row = {
        "id": f"{company_id}-{hours_ago}",
        "company_id": company_id,
        "check_type": "inventory_reconciliation",
        "status": status,
        "checked_at": checked_at,
        "details": {
            "company_id": company_id,
            "checked_at": checked_at,
            "checks_run": checks_run,
            "status": status,
            "discrepancies": list(discrepancies),
        },
    }
    row.update(extra)
    return row


MISMATCH = {
    "type": "inventory_reconciliation",
    "field": "Synthetic Malta 12oz",
    "severity": "critical",
    "message": "Synthetic Malta 12oz: QB shows 100 units, warehouse shows 60 units (40.0% variance)",
    "variance_pct": 40.0,
}
OTHER_TENANT_MISMATCH = {
    "type": "inventory_reconciliation",
    "field": "Other Tenant Secret Product",
    "severity": "critical",
    "message": "Other Tenant Secret Product: QB shows 9 units, warehouse shows 1 units",
    "variance_pct": 88.9,
}


@pytest.fixture
def client_for():
    def make(role="owner", db=None, company_id="tenant-a"):
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-1", "company_id": company_id, "role": role, "jti": "j",
        }
        app.dependency_overrides[get_db] = lambda: db if db is not None else FakeDB()
        return TestClient(app)

    yield make
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _assert_advisory(body):
    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False
    assert body["generated_at"]


def test_module_is_auto_mounted_under_insights():
    from fastapi.routing import iter_route_contexts

    from app.api.insights import MOUNTED

    assert "sync_health" in MOUNTED
    # FastAPI keeps included routers as trees; walk the effective routes.
    paths = {getattr(ctx, "path", "") for ctx in iter_route_contexts(app.routes)}
    assert URL in paths


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_latest_results_are_returned_for_own_company_only(client_for, role):
    db = FakeDB([
        _row("tenant-a", 30, status="healthy"),
        _row("tenant-a", 2, status="warning", discrepancies=[MISMATCH]),
        # Newer rows from another tenant must never leak or win "latest".
        _row("tenant-b", 1, status="warning", discrepancies=[OTHER_TENANT_MISMATCH]),
        _row("tenant-b", 0.5, status="healthy"),
    ])
    response = client_for(role=role, db=db).get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_advisory(body)

    assert db.tables == ["sync_checks"]
    assert ("company_id", "tenant-a") in db.eq_calls
    assert body["state"] == "mismatches_found"
    assert body["has_run"] is True
    assert body["stale"] is False
    assert len(body["checks"]) == 1
    latest = body["checks"][0]
    assert latest["label"] == "Stock counts: QuickBooks vs warehouse system"
    assert latest["systems_compared"] == ["QuickBooks", "Warehouse system"]
    assert latest["mismatch_count"] == 1
    assert latest["mismatches"][0]["item"] == "Synthetic Malta 12oz"
    assert latest["mismatches"][0]["severity"] == "critical"
    assert latest["mismatches"][0]["difference_pct"] == 40.0
    assert body["last_checked_at"] == latest["checked_at"]
    assert [h["state"] for h in body["history"]] == ["mismatches_found", "in_sync"]

    text = response.text
    assert "tenant-b" not in text
    assert "Other Tenant Secret Product" not in text


def test_rows_from_another_company_are_dropped_even_if_the_query_returns_them(client_for):
    class LeakyDB(FakeDB):
        def table(self, name):
            query = super().table(name)
            query.eq = lambda field, value: query  # ignores the tenant filter
            return query

    db = LeakyDB([_row("tenant-b", 1, status="warning", discrepancies=[OTHER_TENANT_MISMATCH])])
    response = client_for(db=db).get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "never_run"
    assert "Other Tenant Secret Product" not in response.text


def test_no_checks_yet_is_an_explicit_never_run_state_not_healthy(client_for):
    db = FakeDB([_row("tenant-b", 1)])  # only another tenant has run checks
    response = client_for(db=db).get(URL)
    assert response.status_code == 200
    body = response.json()
    _assert_advisory(body)
    assert body["state"] == "never_run"
    assert body["has_run"] is False
    assert body["last_checked_at"] is None
    assert body["checks"] == [] and body["history"] == []


def test_agreeing_systems_are_in_sync(client_for):
    response = client_for(db=FakeDB([_row("tenant-a", 1, checks_run=2)])).get(URL)
    body = response.json()
    assert body["state"] == "in_sync"
    assert body["checks"][0]["mismatch_count"] == 0


def test_only_quickbooks_read_is_not_reported_as_in_sync(client_for):
    # The scheduler stores "healthy" when no warehouse data was available to
    # compare; that is absence of evidence, not agreement.
    response = client_for(db=FakeDB([_row("tenant-a", 1, checks_run=1)])).get(URL)
    body = response.json()
    assert body["state"] == "not_compared"
    assert body["checks"][0]["recorded_status"] == "healthy"


@pytest.mark.parametrize("status,expected", [
    ("error", "check_failed"),
    ("critical", "mismatches_found"),
    ("unknown", "unknown"),
    ("something-new", "unknown"),
])
def test_recorded_statuses_map_to_explicit_states(client_for, status, expected):
    response = client_for(db=FakeDB([_row("tenant-a", 1, status=status, checks_run=0)])).get(URL)
    assert response.json()["state"] == expected


def test_old_last_check_is_flagged_stale(client_for):
    response = client_for(db=FakeDB([_row("tenant-a", 48)])).get(URL)
    body = response.json()
    assert body["stale"] is True
    assert body["stale_after_hours"] == 12


def test_latest_result_per_check_type(client_for):
    db = FakeDB([
        _row("tenant-a", 1, status="healthy"),
        _row("tenant-a", 3, status="warning", check_type="revenue_reconciliation",
             discrepancies=[{"field": "Revenue (current period)", "severity": "warning",
                             "message": "Revenue mismatch", "variance_pct": 7.5}]),
        _row("tenant-a", 6, status="warning", discrepancies=[MISMATCH]),
    ])
    body = client_for(db=db).get(URL).json()
    by_type = {c["check_type"]: c for c in body["checks"]}
    assert by_type["inventory_reconciliation"]["state"] == "in_sync"
    assert by_type["revenue_reconciliation"]["state"] == "mismatches_found"
    assert body["state"] == "mismatches_found"
    assert len(body["history"]) == 3


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_view_all_are_forbidden(client_for, role):
    db = FakeDB([_row("tenant-a", 1)])
    response = client_for(role=role, db=db).get(URL)
    assert response.status_code == 403
    assert db.tables == []


def test_database_failure_is_503_not_an_empty_or_healthy_result(client_for):
    response = client_for(db=FakeDB([_row("tenant-a", 1)], fail=True)).get(URL)
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_malformed_query_result_is_503(client_for):
    class BrokenDB(FakeDB):
        def table(self, name):
            query = super().table(name)
            query.execute = lambda: SimpleNamespace(data=None)
            return query

    response = client_for(db=BrokenDB()).get(URL)
    assert response.status_code == 503


def test_module_logs_use_constant_messages_only():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/api/insights/sync_health.py").read_text()
    for call in ast.walk(ast.parse(source)):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "log"
        ):
            assert len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
