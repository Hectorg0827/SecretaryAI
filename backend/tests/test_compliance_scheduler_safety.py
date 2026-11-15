"""Synthetic-only regression tests for the Phase 1 compliance alert scheduler.

No QuickBooks calls, real customers, agency submissions or production writes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import compliance as compliance_api
from app.intelligence.compliance.models import AlertPriority
from app.scheduler import compliance_alert_check as scheduler


class SnapshotDB:
    def __init__(self, confirmed: bool = True):
        self.confirmed = confirmed
        self.written = None

    def table(self, name: str):
        assert name == "report_snapshots"
        return self

    def upsert(self, record, on_conflict=None):
        assert on_conflict == "company_id,report_type"
        self.written = record
        return self

    def execute(self):
        if self.confirmed:
            return SimpleNamespace(data=[{
                "company_id": self.written["company_id"],
                "report_type": self.written["report_type"],
            }])
        return SimpleNamespace(data=[])


class FakeComplianceModule:
    def __init__(self):
        self.lookahead = None

    def get_alerts(self, *, lookahead_days):
        self.lookahead = lookahead_days
        return [SimpleNamespace(
            id="synthetic-alert-1",
            alert_type="state_license_expiry",
            priority=AlertPriority.CRITICAL,
            state_code="NY",
            item_name="Synthetic NY License",
            days_until=7,
            message="Review synthetic license expiry",
            action_required="Verify license status",
            estimated_fee=0,
            expiry_date=date.today() + timedelta(days=7),
            renewal_url=None,
        )]

    def get_deadlines(self, **_kwargs):
        raise AssertionError("Unverified legal filing deadlines must not be emitted")


@pytest.mark.asyncio
async def test_scheduled_scan_uses_tenant_source_and_serializes_priority(monkeypatch):
    module = FakeComplianceModule()
    def load(user, db):
        assert user == {"company_id": "tenant-a"}
        assert isinstance(db, SnapshotDB)
        return module
    monkeypatch.setattr(compliance_api, "_get_compliance_module", load)
    db = SnapshotDB()

    result = await scheduler.check_compliance_alerts("tenant-a", db)

    assert result["error"] is None
    assert result["stored"] is True
    assert result["critical"] == 1
    assert result["warning"] == 0
    assert module.lookahead == 90
    payload = db.written["payload"]
    assert payload["alerts"][0]["priority"] == "critical"
    assert payload["regulatory_deadlines_status"] == "verification_required"
    assert payload["deadlines"] == []
    assert db.written["company_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_unconfirmed_snapshot_is_failure_and_not_success(monkeypatch):
    monkeypatch.setattr(
        compliance_api, "_get_compliance_module",
        lambda _user, _db: FakeComplianceModule(),
    )
    result = await scheduler.check_compliance_alerts("tenant-a", SnapshotDB(confirmed=False))
    assert result["stored"] is False
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_source_read_failure_does_not_write_false_empty_snapshot(monkeypatch):
    def fail_load(_user, _db):
        raise RuntimeError("Synthetic compliance source unavailable")
    monkeypatch.setattr(compliance_api, "_get_compliance_module", fail_load)
    db = SnapshotDB()
    result = await scheduler.check_compliance_alerts("tenant-a", db)
    assert result["stored"] is False
    assert result["error"] is not None
    assert db.written is None


def test_celery_task_does_not_notify_on_failed_scan(monkeypatch):
    from tasks import compliance_alerts as task_module

    class CompanyDB:
        def table(self, name):
            assert name == "companies"
            return self

        def select(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": "tenant-a", "name": "Synthetic Co"}])

    @contextmanager
    def no_network_lock(_name, ttl_seconds=3600):
        yield True

    monkeypatch.setattr(task_module, "get_supabase", CompanyDB)
    monkeypatch.setattr(task_module, "task_lock", no_network_lock)
    monkeypatch.setattr(
        scheduler, "check_compliance_alerts",
        AsyncMock(return_value={
            "critical": 2, "warning": 0, "stored": False,
            "error": "synthetic source error",
        }),
    )
    notify = AsyncMock()
    monkeypatch.setattr(task_module, "_notify_compliance", notify)

    summary = task_module.check_all.run()
    assert summary["errors"] == 1
    assert summary["processed"] == 0
    assert summary["notified"] == 0
    assert summary["total_critical"] == 0
    notify.assert_not_awaited()
