"""Gate A: dashboard-only background refresh must be tenant-scoped and read-only."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tasks.dashboard_refresh import refresh_company_snapshot
from app.scheduler.dashboard_snapshot import (
    SnapshotPersistenceUnavailable, SnapshotSourceUnavailable, store_snapshot,
)


def test_manual_refresh_reuses_read_only_snapshot_path():
    db = MagicMock()
    reader = MagicMock()
    payload = {
        "source_quality": "partial", "source_read_status": "completed",
        "accounts": {"total": 0}, "inventory_alerts": [], "sales_30d": 0,
    }
    companies = [{"id": "tenant-a", "name": "Fixture A"}]
    with patch(
        "tasks.dashboard_refresh.compute_dashboard_payload",
        new=AsyncMock(return_value=payload),
    ) as read, patch(
        "tasks.dashboard_refresh.store_snapshot"
    ) as write:
        result = refresh_company_snapshot(
            "tenant-a", db=db, companies=companies,
            adapter_factory=lambda row: reader,
        )
    read.assert_awaited_once_with(reader)
    write.assert_called_once_with(
        db, "tenant-a", "dashboard_summary", payload,
        generated_by="manual_dashboard_refresh",
    )
    assert result["status"] == "refreshed"


def test_dashboard_only_worker_rejects_cross_tenant_selection():
    db = MagicMock()
    with patch("tasks.dashboard_refresh.compute_dashboard_payload") as read, \
         patch("tasks.dashboard_refresh.store_snapshot") as write:
        with pytest.raises(ValueError, match="not eligible"):
            refresh_company_snapshot(
                "tenant-b", db=db,
                companies=[{"id": "tenant-a"}],
                adapter_factory=lambda _: MagicMock(),
            )
    read.assert_not_called()
    write.assert_not_called()


def test_dashboard_snapshot_store_failure_is_not_reported_as_success():
    db = MagicMock()
    db.table.side_effect = RuntimeError("DB write unavailable")
    with pytest.raises(SnapshotPersistenceUnavailable):
        store_snapshot(
            db, "tenant-a", "dashboard_summary",
            {"source_quality": "partial"}, generated_by="fixture",
        )


def test_dashboard_only_worker_rejects_failed_provider_read_without_persisting():
    db = MagicMock()
    with patch(
        "tasks.dashboard_refresh.compute_dashboard_payload",
        new=AsyncMock(side_effect=SnapshotSourceUnavailable("inventory")),
    ), patch("tasks.dashboard_refresh.store_snapshot") as write:
        with pytest.raises(SnapshotSourceUnavailable):
            refresh_company_snapshot(
                "tenant-a", db=db, companies=[{"id": "tenant-a"}],
                adapter_factory=lambda _: MagicMock(),
            )
    write.assert_not_called()
