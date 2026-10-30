"""Connection-problem pushes and the weekly report's in-app delivery.

No external service is called: Expo push goes through a patched
httpx.AsyncClient, the AI writer and the business-data reads are mocked, and
the database is a small in-memory stand-in for the Supabase query builder.
"""
from __future__ import annotations

import ast
import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tasks.connector_health as connector_health
import tasks.weekly_report as weekly_task
from app.api import briefing
from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.scheduler import weekly_report as weekly
from app.scheduler.dashboard_snapshot import SnapshotSourceUnavailable

FORGED = "\nFORGED_LOG_EVENT"
SUNDAY = date(2026, 11, 1)


# ─── In-memory Supabase stand-in ──────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.conflict = "select", None, None
        self.filters = []

    def select(self, *_cols):
        return self

    def eq(self, col, value):
        self.filters.append((col, "eq", value))
        return self

    def in_(self, col, values):
        self.filters.append((col, "in", list(values)))
        return self

    def lt(self, col, value):
        self.filters.append((col, "lt", value))
        return self

    def update(self, row):
        self.op, self.payload = "update", dict(row)
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", dict(row)
        self.conflict = [c.strip() for c in on_conflict.split(",")]
        return self

    def _match(self, row):
        for col, kind, value in self.filters:
            have = row.get(col)
            if kind == "eq" and have != value:
                return False
            if kind == "in" and have not in value:
                return False
            if kind == "lt" and not (have is not None and have < value):
                return False
        return True

    def execute(self):
        self.db.queries.append((self.name, self.op, list(self.filters)))
        if self.name in self.db.down:
            raise RuntimeError("store down" + FORGED)
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "upsert":
            key = [self.payload.get(c) for c in self.conflict]
            for row in rows:
                if [row.get(c) for c in self.conflict] == key:
                    row.update(self.payload)
                    return _Result([dict(row)])
            rows.append(dict(self.payload))
            return _Result([dict(self.payload)])
        matched = [row for row in rows if self._match(row)]
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
        return _Result([dict(row) for row in matched])


class FakeDB:
    def __init__(self, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.queries = []
        self.down = set()

    def table(self, name):
        return _Query(self, name)


USERS = [
    {"id": "owner-a", "company_id": "co-a", "role": "owner", "is_active": True},
    {"id": "manager-a", "company_id": "co-a", "role": "manager", "is_active": True},
    {"id": "rep-a", "company_id": "co-a", "role": "sales_rep", "is_active": True},
    {"id": "old-manager-a", "company_id": "co-a", "role": "manager", "is_active": False},
    {"id": "owner-b", "company_id": "co-b", "role": "owner", "is_active": True},
]
TOKENS = [
    {"company_id": "co-a", "user_id": "owner-a", "token": "ExponentPushToken[owner-a]"},
    {"company_id": "co-a", "user_id": "manager-a", "token": "ExponentPushToken[manager-a]"},
    {"company_id": "co-a", "user_id": "rep-a", "token": "ExponentPushToken[rep-a]"},
    {"company_id": "co-a", "user_id": "old-manager-a", "token": "ExponentPushToken[old]"},
    {"company_id": "co-b", "user_id": "owner-b", "token": "ExponentPushToken[owner-b]"},
]


@contextmanager
def expo_capture(fail=False):
    """Patch the Expo HTTP call made by the real send_push_notifications."""
    sent = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            if fail:
                raise RuntimeError("expo down" + FORGED)
            sent.append({"url": url, "messages": json})
            return MagicMock(status_code=200)

    with patch("app.api.notifications.httpx.AsyncClient", _Client):
        yield sent


@contextmanager
def lock_free(module):
    @contextmanager
    def _lock(*_a, **_k):
        yield True
    with patch.object(module, "task_lock", _lock):
        yield


def stale_db(*connectors):
    # Silent for ten minutes: past the stale threshold, not yet an error.
    heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    rows = [{"status": "connected", "last_heartbeat": heartbeat, **c} for c in connectors]
    return FakeDB(connector_registrations=rows, users=USERS, device_push_tokens=TOKENS)


# ─── Connection-problem alerts ────────────────────────────────────────────────

def test_stale_connector_push_reaches_only_this_companys_owners_and_managers():
    db = stale_db({
        "id": "reg-1", "company_id": "co-a", "connector_type": "qb_desktop",
        "connector_id": "host-1" + FORGED,
    })
    with lock_free(connector_health), expo_capture() as sent, \
            patch.object(connector_health, "get_supabase", return_value=db):
        summary = connector_health.check_all()

    assert summary["newly_stale"] == 1
    assert db.tables["connector_registrations"][0]["status"] == "stale"
    assert len(sent) == 1
    messages = sent[0]["messages"]
    assert sorted(m["to"] for m in messages) == [
        "ExponentPushToken[manager-a]", "ExponentPushToken[owner-a]",
    ]
    for message in messages:
        assert message["title"] == "Connection problem"
        assert message["body"].startswith("QuickBooks Desktop stopped responding.")
        assert "host-1" not in message["body"] and "FORGED" not in message["body"]
        assert message["data"] == {"type": "connector_stale", "count": 1}


def test_stale_push_calls_the_real_push_function_once_per_company():
    db = stale_db(
        {"id": "r1", "company_id": "co-a", "connector_type": "qb_desktop", "connector_id": "x"},
        {"id": "r2", "company_id": "co-a", "connector_type": "made-up" + FORGED, "connector_id": "y"},
        {"id": "r3", "company_id": "co-b", "connector_type": "made-up" + FORGED, "connector_id": "z"},
    )
    with lock_free(connector_health), \
            patch("app.api.notifications.send_push_notifications", new=AsyncMock()) as push, \
            patch.object(connector_health, "get_supabase", return_value=db):
        summary = connector_health.check_all()

    assert summary["newly_stale"] == 3
    calls = {c.kwargs["company_id"]: c.kwargs for c in push.await_args_list}
    assert set(calls) == {"co-a", "co-b"}
    assert calls["co-a"]["user_ids"] == ["owner-a", "manager-a"]
    assert calls["co-b"]["user_ids"] == ["owner-b"]
    assert calls["co-a"]["body"].startswith("2 business connections stopped responding.")
    assert calls["co-b"]["body"].startswith("A business connection stopped responding.")
    for kwargs in calls.values():
        assert kwargs["db"] is db
        assert "FORGED" not in kwargs["body"] and "made-up" not in kwargs["body"]


def test_stale_marking_survives_push_failure_and_logs_constant_warning(caplog):
    db = stale_db({"id": "r1", "company_id": "co-a", "connector_type": "google", "connector_id": "x"})
    with lock_free(connector_health), \
            patch("app.api.notifications.send_push_notifications",
                  new=AsyncMock(side_effect=RuntimeError("push" + FORGED))), \
            patch.object(connector_health, "get_supabase", return_value=db), \
            caplog.at_level(logging.DEBUG):
        summary = connector_health.check_all()

    assert summary["newly_stale"] == 1
    assert db.tables["connector_registrations"][0]["status"] == "stale"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(r.getMessage() == "Connector health: connection problem push failed"
               for r in warnings)
    assert not any("FORGED" in r.getMessage() for r in caplog.records)


def test_expo_outage_does_not_fail_the_health_check():
    db = stale_db({"id": "r1", "company_id": "co-a", "connector_type": "shopify", "connector_id": "x"})
    with lock_free(connector_health), expo_capture(fail=True), \
            patch.object(connector_health, "get_supabase", return_value=db):
        summary = connector_health.check_all()
    assert summary["newly_stale"] == 1


def test_company_without_active_leaders_gets_no_broadcast():
    users = [u for u in USERS if u["company_id"] != "co-a" or u["role"] == "sales_rep"]
    db = FakeDB(
        connector_registrations=[{
            "id": "r1", "company_id": "co-a", "connector_type": "qb_online",
            "connector_id": "x", "status": "connected",
            "last_heartbeat": "2020-01-01T00:00:00+00:00",
        }],
        users=users, device_push_tokens=TOKENS,
    )
    with lock_free(connector_health), expo_capture() as sent, \
            patch.object(connector_health, "get_supabase", return_value=db):
        connector_health.check_all()
    assert sent == []


def test_connector_that_reconnected_since_the_read_is_not_reported():
    db = stale_db({"id": "r1", "company_id": "co-a", "connector_type": "qb_desktop", "connector_id": "x"})
    original_update = _Query.update

    def heartbeat_first(query, row):
        db.tables["connector_registrations"][0]["status"] = "connected-again"
        return original_update(query, row)

    with patch.object(_Query, "update", heartbeat_first), \
            patch("app.api.notifications.send_push_notifications", new=AsyncMock()) as push:
        now = datetime.now(timezone.utc)
        count = connector_health._mark_stale(db, now.isoformat(), now)
    assert db.queries[0][0] == "connector_registrations"  # the stale read found it
    assert count == 0
    assert db.tables["connector_registrations"][0]["status"] == "connected-again"
    push.assert_not_awaited()


def test_status_query_outage_fails_the_run_instead_of_reporting_zero(caplog):
    db = stale_db()
    db.down.add("connector_registrations")
    with lock_free(connector_health), \
            patch.object(connector_health, "get_supabase", return_value=db), \
            caplog.at_level(logging.DEBUG):
        with pytest.raises(connector_health.ConnectorHealthCheckFailed):
            connector_health.check_all()
    assert not any("FORGED" in r.getMessage() for r in caplog.records)


def test_empty_recipient_list_never_widens_to_company_broadcast():
    from app.api.notifications import send_push_notifications
    import asyncio

    db = FakeDB(device_push_tokens=TOKENS)
    with expo_capture() as sent:
        asyncio.run(send_push_notifications(db, "co-a", "t", "b", user_ids=[]))
    assert sent == [] and db.queries == []


# ─── Weekly report ────────────────────────────────────────────────────────────

DASHBOARD = {
    "source_quality": "partial", "source_read_status": "completed",
    "accounts": {"healthy": 6, "slowing": 2, "at_risk": 1, "dormant": 1, "unknown": 0, "total": 10},
    "inventory_alerts": [
        {"product_name": "Rye 750", "stock_status": "critical"},
        {"product_name": "Gin 1L", "stock_status": "low"},
    ],
    "inventory_unassessed": 3,
    "sales_30d": 9000.0,
}
INVOICES = [
    {"date": "2026-10-26", "total": 1200, "customer_name": "Corner Bar"},   # this week
    {"date": "2026-10-31", "total": 300, "customer_name": "Hotel Grand"},   # this week
    {"date": "2026-10-20", "total": 800, "customer_name": "Corner Bar"},    # prior week
    {"date": "2026-11-01", "total": 999, "customer_name": "Today Only"},    # today: excluded
]


def adapter_with(invoices=INVOICES):
    adapter = MagicMock()
    adapter.get_orders_last_n_days = AsyncMock(return_value=invoices)
    return adapter


def run_for(company, db, *, dashboard=DASHBOARD, writer_text="Revenue rose to $1,500.",
            adapter=None):
    adapter = adapter or adapter_with()
    payload = (AsyncMock(side_effect=dashboard) if isinstance(dashboard, Exception)
               else AsyncMock(return_value=dashboard))
    writer = AsyncMock(return_value=writer_text)
    with patch.object(weekly, "compute_dashboard_payload", new=payload), \
            patch.object(weekly_task, "generate_weekly_report", new=writer), \
            patch("app.api.notifications.send_push_notifications", new=AsyncMock()) as push:
        outcome = weekly_task.run_company_weekly_report(
            company, db=db, adapter_factory=lambda _c: adapter, today=SUNDAY,
        )
    return outcome, writer, push


def snapshots(db, company_id):
    return {r["report_type"]: r for r in db.tables.get("report_snapshots", [])
            if r["company_id"] == company_id}


def test_weekly_figures_cover_the_seven_complete_days_and_prior_week():
    import asyncio

    start, end = weekly.report_period(SUNDAY)
    assert (start, end) == (date(2026, 10, 25), date(2026, 10, 31))
    with patch.object(weekly, "compute_dashboard_payload", new=AsyncMock(return_value=DASHBOARD)):
        collected = asyncio.run(weekly.collect_weekly_figures(
            adapter_with(), period_start=start, period_end=end))
    f = collected["figures"]
    assert (f["sales_this_week"], f["invoices_this_week"]) == (1500.0, 2)
    assert (f["sales_prior_week"], f["invoices_prior_week"]) == (800.0, 1)
    assert collected["top_accounts"] == [("Corner Bar", 1200.0), ("Hotel Grand", 300.0)]
    text = weekly.figures_to_text(collected, period_start=start, period_end=end)
    assert "$1,500.00 across 2 invoices" in text and "Rye 750" in text


def test_weekly_report_is_stored_for_its_company_and_leaders_are_pushed():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    outcome, writer, push = run_for({"id": "co-a", "name": "Alpha"}, db)

    assert outcome == {"status": "stored", "notified": True}
    stored = snapshots(db, "co-a")
    report = stored["weekly_report"]["payload"]
    assert report["status"] == "ready"
    assert report["report_text"] == "Revenue rose to $1,500."
    assert (report["period_start"], report["period_end"]) == ("2026-10-25", "2026-10-31")
    assert report["figures"]["sales_this_week"] == 1500.0
    assert stored["weekly_report_run"]["payload"]["status"] == "succeeded"
    assert snapshots(db, "co-b") == {}

    writer.assert_awaited_once()
    assert writer.await_args.kwargs["company_id"] == "co-a"
    assert "Corner Bar" in writer.await_args.kwargs["data_summary"]
    push.assert_awaited_once()
    kwargs = push.await_args.kwargs
    assert kwargs["company_id"] == "co-a"
    assert kwargs["user_ids"] == ["owner-a", "manager-a"]
    assert kwargs["title"] == "Your weekly report is ready"
    assert kwargs["data"] == {"type": "weekly_report_ready"}


@pytest.mark.parametrize("outage", [
    SnapshotSourceUnavailable("sales_30d"),
    RuntimeError("QuickBooks down" + FORGED),
])
def test_data_outage_is_a_failed_run_never_a_report_or_announcement(outage, caplog):
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    with caplog.at_level(logging.DEBUG):
        outcome, writer, push = run_for({"id": "co-a", "name": "Alpha"}, db, dashboard=outage)

    assert outcome == {"status": "failed", "reason": "source_unavailable"}
    stored = snapshots(db, "co-a")
    assert "weekly_report" not in stored
    assert stored["weekly_report_run"]["payload"] == {
        "status": "failed", "reason": "source_unavailable",
        "period_start": "2026-10-25", "period_end": "2026-10-31",
    }
    writer.assert_not_awaited()
    push.assert_not_awaited()
    assert "Data unavailable for weekly report." not in str(db.tables)
    assert not any("FORGED" in r.getMessage() for r in caplog.records)


def test_untrustworthy_invoice_is_an_outage_not_a_zero_week():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    bad = adapter_with([{"date": "2026-10-26", "total": None, "customer_name": "X"}])
    outcome, writer, push = run_for({"id": "co-a"}, db, adapter=bad)
    assert outcome["status"] == "failed"
    assert "weekly_report" not in snapshots(db, "co-a")
    writer.assert_not_awaited()
    push.assert_not_awaited()


def test_outage_keeps_last_good_report_and_marks_the_new_run_failed():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    run_for({"id": "co-a"}, db)
    run_for({"id": "co-a"}, db, dashboard=RuntimeError("down"))
    stored = snapshots(db, "co-a")
    assert stored["weekly_report"]["payload"]["report_text"] == "Revenue rose to $1,500."
    assert stored["weekly_report_run"]["payload"]["status"] == "failed"


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_writer_output_is_not_stored_or_announced(text):
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    outcome, _writer, push = run_for({"id": "co-a"}, db, writer_text=text)
    assert outcome == {"status": "failed", "reason": "generation_failed"}
    assert "weekly_report" not in snapshots(db, "co-a")
    push.assert_not_awaited()


def test_report_storage_failure_is_not_announced():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    db.down.add("report_snapshots")
    outcome, _writer, push = run_for({"id": "co-a"}, db)
    assert outcome == {"status": "failed", "reason": "storage_failed"}
    push.assert_not_awaited()


def test_push_failure_does_not_undo_a_stored_report():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    with patch.object(weekly, "compute_dashboard_payload", new=AsyncMock(return_value=DASHBOARD)), \
            patch.object(weekly_task, "generate_weekly_report", new=AsyncMock(return_value="ok")), \
            expo_capture(fail=True):
        outcome = weekly_task.run_company_weekly_report(
            {"id": "co-a"}, db=db, adapter_factory=lambda _c: adapter_with(), today=SUNDAY)
    assert outcome["status"] == "stored"
    assert snapshots(db, "co-a")["weekly_report"]["payload"]["report_text"] == "ok"


def test_weekly_task_runs_every_company_and_one_outage_does_not_stop_others():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    companies = [{"id": "co-a", "name": "Alpha"}, {"id": "co-b", "name": "Beta"}]
    adapters = {"co-a": adapter_with(), "co-b": adapter_with()}
    adapters["co-a"].get_orders_last_n_days = AsyncMock(side_effect=RuntimeError("down"))
    with lock_free(weekly_task), \
            patch.object(weekly_task, "get_supabase", return_value=db), \
            patch.object(weekly_task, "get_active_companies", return_value=companies), \
            patch.object(weekly_task, "build_adapter", side_effect=lambda c: adapters[c["id"]]), \
            patch.object(weekly, "compute_dashboard_payload", new=AsyncMock(return_value=DASHBOARD)), \
            patch.object(weekly_task, "generate_weekly_report", new=AsyncMock(return_value="Beta week")), \
            expo_capture() as sent:
        summary = weekly_task.send_weekly_report_all()

    assert summary == {"stored": 1, "failed": 1, "notified": 1}
    assert "weekly_report" not in snapshots(db, "co-a")
    assert snapshots(db, "co-b")["weekly_report"]["payload"]["report_text"] == "Beta week"
    assert [m["to"] for m in sent[0]["messages"]] == ["ExponentPushToken[owner-b]"]


def test_weekly_task_never_touches_email():
    source = Path(weekly_task.__file__).read_text(encoding="utf-8")
    source += Path(weekly.__file__).read_text(encoding="utf-8")
    for forbidden in ("sendgrid", "SendGrid", "send_email", "Mail(", "smtp"):
        assert forbidden not in source


# ─── Weekly report API ────────────────────────────────────────────────────────

def stored_reports_db():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    run_for({"id": "co-a"}, db, writer_text="Alpha private report")
    run_for({"id": "co-b"}, db, writer_text="Beta private report")
    return db


def client_for(role, db, company_id="co-a"):
    app = FastAPI()
    app.include_router(briefing.router, prefix="/api/briefing")
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": company_id, "role": role, "sub": "fixture-user",
    }
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_owner_and_manager_read_only_their_own_companys_report(role):
    db = stored_reports_db()
    response = client_for(role, db).get("/api/briefing/weekly")
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["report_text"] == "Alpha private report"
    assert body["report"]["period_end"] == "2026-10-31"
    assert body["report"]["generated_at"]
    assert body["last_run"]["status"] == "succeeded"
    assert "Beta private report" not in response.text
    read = [q for q in db.queries if q[0] == "report_snapshots" and q[1] == "select"]
    assert read and all(("company_id", "eq", "co-a") in q[2] for q in read)


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_other_roles_cannot_read_the_weekly_report(role):
    db = stored_reports_db()
    db.queries.clear()
    response = client_for(role, db).get("/api/briefing/weekly")
    assert response.status_code == 403
    assert db.queries == []


def test_company_without_a_report_gets_an_empty_state_not_an_error():
    db = stored_reports_db()
    response = client_for("owner", db, company_id="co-new").get("/api/briefing/weekly")
    assert response.status_code == 200
    assert response.json() == {"report": None, "last_run": None}


def test_failed_run_is_visible_and_no_report_is_invented():
    db = FakeDB(users=USERS, device_push_tokens=TOKENS)
    run_for({"id": "co-a"}, db, dashboard=RuntimeError("down"))
    body = client_for("owner", db).get("/api/briefing/weekly").json()
    assert body["report"] is None
    assert body["last_run"]["status"] == "failed"
    assert body["last_run"]["reason"] == "source_unavailable"


def test_store_outage_is_503_not_an_empty_report():
    db = stored_reports_db()
    db.down.add("report_snapshots")
    response = client_for("owner", db).get("/api/briefing/weekly")
    assert response.status_code == 503


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(status="draft"),
    lambda p: p.update(report_text=""),
    lambda p: p.update(source_quality="verified"),
    lambda p: p["figures"].update(sales_this_week=float("nan")),
    lambda p: p.update(injected="field"),
])
def test_malformed_stored_report_is_not_shown(mutation):
    db = stored_reports_db()
    row = next(r for r in db.tables["report_snapshots"]
               if r["company_id"] == "co-a" and r["report_type"] == "weekly_report")
    mutation(row["payload"])
    response = client_for("owner", db).get("/api/briefing/weekly")
    assert response.status_code == 503
    assert "Alpha private report" not in response.text


def test_weekly_route_is_mounted_in_the_real_app_with_rbac():
    """The real app (paid briefing router) serves it and still checks the role."""
    from app.main import app as real_app

    db = stored_reports_db()
    saved = dict(real_app.dependency_overrides)
    try:
        real_app.dependency_overrides[get_db] = lambda: db
        for role, expected in (("sales_rep", 403), ("viewer", 403), ("owner", 200)):
            real_app.dependency_overrides[get_current_user] = lambda role=role: {
                "company_id": "co-a", "role": role, "sub": "fixture-user",
            }
            response = TestClient(real_app).get("/api/briefing/weekly")
            assert response.status_code == expected, (role, response.text)
    finally:
        real_app.dependency_overrides.clear()
        real_app.dependency_overrides.update(saved)
    assert response.json()["report"]["report_text"] == "Alpha private report"


# ─── Log safety ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module_path", [
    "tasks/connector_health.py",
    "tasks/weekly_report.py",
    "app/scheduler/weekly_report.py",
])
def test_alert_and_report_modules_log_only_constant_events(module_path):
    source = (Path(__file__).resolve().parents[1] / module_path).read_text(encoding="utf-8")
    for call in ast.walk(ast.parse(source)):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "log"):
            assert len(call.args) == 1 and not call.keywords, f"{module_path}:{call.lineno}"
            assert isinstance(call.args[0], ast.Constant), f"{module_path}:{call.lineno}"
