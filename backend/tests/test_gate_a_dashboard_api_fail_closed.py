"""Gate A: dashboard API must not convert provider failures into healthy zeros."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.dashboard import get_dashboard_summary
from app.scheduler.dashboard_snapshot import SnapshotSourceUnavailable


@pytest.mark.asyncio
async def test_missing_inbox_and_approval_sources_are_unknown_not_zero():
    db = MagicMock()
    db.table.side_effect = RuntimeError("fixture pending queue down")
    adapter = AsyncMock()
    adapter.get_emails.side_effect = RuntimeError("fixture email offline")
    healthy_snapshot = {
        "payload": {
            "accounts": {"healthy": 1, "slowing": 0, "at_risk": 0, "dormant": 0},
            "inventory_alerts": [],
            "sales_30d": 300,
            "source_quality": "partial",
        },
        "generated_at": "2026-10-26T12:00:00Z",
    }
    with patch("app.api.dashboard.read_snapshot", return_value=healthy_snapshot):
        response = await get_dashboard_summary(
            user={"company_id": "test-co", "role": "owner"},
            adapter=adapter, db=db,
        )
    assert response["pending_actions"] is None
    assert response["unread_emails"] is None
    assert response["source_quality"] == "partial"
    assert response["accounts"]["healthy"] == 1


@pytest.mark.asyncio
async def test_initial_dashboard_source_failure_returns_503_without_writing_snapshot():
    db = MagicMock()
    adapter = AsyncMock()
    with patch("app.api.dashboard.read_snapshot", return_value=None), \
         patch("app.api.dashboard.compute_dashboard_payload",
               new=AsyncMock(side_effect=SnapshotSourceUnavailable("sales_30d"))), \
         patch("app.api.dashboard.store_snapshot") as write:
        with pytest.raises(HTTPException) as exc:
            await get_dashboard_summary(
                user={"company_id": "test-co", "role": "owner"},
                adapter=adapter, db=db,
            )
    assert exc.value.status_code == 503
    write.assert_not_called()



@pytest.mark.asyncio
async def test_manual_refresh_queues_only_dashboard_task_not_morning_email():
    from types import SimpleNamespace
    from app.api.dashboard import trigger_dashboard_refresh

    with patch(
        "tasks.dashboard_refresh.refresh_dashboard_snapshot.apply_async",
        return_value=SimpleNamespace(id="fixture-task-1"),
    ) as dashboard_job, patch(
        "tasks.morning_briefing.send_morning_briefing_all.apply_async"
    ) as briefing_job:
        result = await trigger_dashboard_refresh(
            user={"company_id": "tenant-fixture", "role": "owner"},
        )
    assert result["status"] == "refresh_queued"
    assert result["company_id"] == "tenant-fixture"
    assert result["task_id"] == "fixture-task-1"
    dashboard_job.assert_called_once_with(
        kwargs={"company_id": "tenant-fixture"}
    )
    briefing_job.assert_not_called()


@pytest.mark.asyncio
async def test_manual_refresh_broker_failure_returns_503():
    from app.api.dashboard import trigger_dashboard_refresh

    with patch(
        "tasks.dashboard_refresh.refresh_dashboard_snapshot.apply_async",
        side_effect=ConnectionError("Fixture broker offline"),
    ):
        with pytest.raises(HTTPException) as info:
            await trigger_dashboard_refresh(
                user={"company_id": "tenant-fixture", "role": "owner"},
            )
    assert info.value.status_code == 503


@pytest.mark.asyncio
async def test_initial_dashboard_snapshot_persistence_failure_returns_503():
    from app.scheduler.dashboard_snapshot import SnapshotPersistenceUnavailable
    db = MagicMock()
    adapter = AsyncMock()
    payload = {
        "accounts": {"healthy": 1, "total": 1}, "inventory_alerts": [],
        "sales_30d": 100, "source_quality": "partial",
        "source_read_status": "completed",
    }
    with patch("app.api.dashboard.read_snapshot", return_value=None), \
         patch("app.api.dashboard.compute_dashboard_payload",
               new=AsyncMock(return_value=payload)), \
         patch("app.api.dashboard.store_snapshot",
               side_effect=SnapshotPersistenceUnavailable("fixture storage down")):
        with pytest.raises(HTTPException) as info:
            await get_dashboard_summary(
                user={"company_id": "tenant-fixture", "role": "owner"},
                adapter=adapter, db=db,
            )
    assert info.value.status_code == 503
