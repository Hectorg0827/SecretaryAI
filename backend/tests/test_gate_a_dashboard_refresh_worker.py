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


def test_worker_refresh_attaches_db_so_inventory_sell_rates_are_read():
    db = MagicMock()
    reader = MagicMock()
    payload = {
        "source_quality": "partial", "source_read_status": "completed",
        "accounts": {"total": 0}, "inventory_alerts": [], "sales_30d": 0,
    }
    with patch(
        "tasks.dashboard_refresh.compute_dashboard_payload",
        new=AsyncMock(return_value=payload),
    ), patch("tasks.dashboard_refresh.store_snapshot"):
        refresh_company_snapshot(
            "tenant-a", db=db, companies=[{"id": "tenant-a"}],
            adapter_factory=lambda row: reader,
        )
    reader.set_db.assert_called_once_with(db)


def test_staging_refresh_reports_each_fixed_stage_before_snapshot_write():
    db = MagicMock()
    adapter = MagicMock()
    observed_stages = []
    payload = {
        "source_quality": "partial", "source_read_status": "completed",
        "accounts": {"total": 0}, "inventory_alerts": [], "sales_30d": 0,
    }
    with patch(
        "tasks.dashboard_refresh.compute_dashboard_payload",
        new=AsyncMock(return_value=payload),
    ), patch(
        "tasks.dashboard_refresh.compare_invoice_headers_to_qbo_income",
        new=AsyncMock(return_value={"status": "unavailable"}),
    ), patch(
        "tasks.dashboard_refresh.read_finance_document_coverage",
        new=AsyncMock(return_value={"ar": {"status": "unavailable"}}),
    ), patch(
        "tasks.dashboard_refresh.read_purchasing_coverage",
        return_value={"status": "unavailable"},
    ), patch(
        "tasks.dashboard_refresh.store_snapshot",
    ) as write:
        refresh_company_snapshot(
            "tenant-a", db=db, companies=[{"id": "tenant-a"}],
            adapter_factory=lambda row: adapter,
            include_finance_comparison=True,
            include_purchasing_coverage=True,
            include_finance_document_coverage=True,
            stage_hook=observed_stages.append,
        )
    assert observed_stages == [
        "setup_provider_adapter", "attach_inventory_database",
        "read_required_dashboard_sources", "read_optional_income_comparison",
        "read_optional_finance_documents", "read_optional_purchasing",
        "persist_snapshot",
    ]
    write.assert_called_once()


def test_required_provider_failure_never_advances_to_persistence_stage():
    observed_stages = []
    with patch(
        "tasks.dashboard_refresh.compute_dashboard_payload",
        new=AsyncMock(side_effect=SnapshotSourceUnavailable("sales_30d")),
    ), patch("tasks.dashboard_refresh.store_snapshot") as write:
        with pytest.raises(SnapshotSourceUnavailable):
            refresh_company_snapshot(
                "tenant-a", db=MagicMock(), companies=[{"id": "tenant-a"}],
                adapter_factory=lambda row: MagicMock(),
                stage_hook=observed_stages.append,
            )
    assert observed_stages[-1] == "read_required_dashboard_sources"
    assert "persist_snapshot" not in observed_stages
    write.assert_not_called()
