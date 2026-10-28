"""Gate A: corrupted cache must not be treated as healthy business activity.

Synthetic data only. Assertions cover the real summary / briefing boundaries
and distinguish a missing cache row from a storage outage.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import briefing, dashboard
from app.scheduler.dashboard_snapshot import (
    SnapshotDataInvalid, SnapshotReadUnavailable, read_snapshot,
    validate_dashboard_cached_payload,
)


def valid_payload():
    return {
        "accounts": {
            "healthy": 2, "slowing": 1, "at_risk": 0, "dormant": 0, "total": 3,
        },
        "inventory_alerts": [{"item_id": "synthetic-1", "stock_status": "low"}],
        "sales_30d": 0,
        "source_quality": "partial",
        "source_read_status": "completed",
    }


def stored(payload=None, generated_at=None):
    return {
        "payload": valid_payload() if payload is None else payload,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.parametrize("mutation", [
    lambda x: x.pop("accounts"),
    lambda x: x.pop("sales_30d"),
    lambda x: x.pop("inventory_alerts"),
    lambda x: x.update(source_read_status="failed"),
    lambda x: x.update(source_quality="unavailable"),
    lambda x: x.update(source_quality="verified"),  # No provenance proof in v1
    lambda x: x["accounts"].update(total=300),
    lambda x: x["accounts"].update(healthy=True),
    lambda x: x["accounts"].update(slowing=-1),
    lambda x: x.update(sales_30d=float("nan")),
    lambda x: x.update(sales_30d=float("inf")),
    lambda x: x.update(sales_30d=10 ** 400),
    lambda x: x["inventory_alerts"].append({"stock_status": "healthy"}),
])
def test_dashboard_cache_rejects_corruption(mutation):
    payload = valid_payload()
    mutation(payload)
    with pytest.raises(SnapshotDataInvalid):
        validate_dashboard_cached_payload(payload)


@pytest.mark.asyncio
async def test_malformed_cached_snapshot_fails_without_new_source_reads():
    adapter = AsyncMock()
    db = MagicMock()
    payload = valid_payload()
    payload.pop("sales_30d")
    with patch.object(dashboard, "read_snapshot", return_value=stored(payload)), \
         patch.object(dashboard, "compute_dashboard_payload", new=AsyncMock()) as compute, \
         patch.object(dashboard, "store_snapshot") as save:
        with pytest.raises(HTTPException) as error:
            await dashboard.get_dashboard_summary(
                user={"company_id": "tenant-a", "sub": "owner-a", "role": "owner"},
                adapter=adapter, db=db,
            )
    assert error.value.status_code == 503
    compute.assert_not_awaited()
    save.assert_not_called()
    adapter.get_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_invalid_cache_timestamp_cannot_claim_current_metrics():
    adapter = AsyncMock()
    db = MagicMock()
    with patch.object(dashboard, "read_snapshot", return_value=stored(
        generated_at="2026-10-26T15:00:00"  # No timezone.
    )):
        with pytest.raises(HTTPException) as error:
            await dashboard.get_dashboard_summary(
                user={"company_id": "tenant-a", "sub": "owner-a", "role": "owner"},
                adapter=adapter, db=db,
            )
    assert error.value.status_code == 503
    adapter.get_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_but_structurally_valid_dashboard_stays_explicitly_stale():
    adapter = AsyncMock()
    adapter.get_emails.return_value = []
    db = MagicMock()
    old = datetime.now(timezone.utc) - timedelta(days=3)
    with patch.object(dashboard, "read_snapshot", return_value=stored(
        generated_at=old.isoformat()
    )):
        response = await dashboard.get_dashboard_summary(
            user={"company_id": "tenant-a", "sub": "owner-a", "role": "owner"},
            adapter=adapter, db=db,
        )
    assert response["snapshot_stale"] is True
    assert response["snapshot_freshness"] == "stale"
    assert response["source_quality"] == "partial"


@pytest.mark.asyncio
async def test_cache_database_outage_fails_before_provider_sync():
    db = MagicMock()
    db.table.side_effect = ConnectionError("Synthetic DB outage")
    with pytest.raises(SnapshotReadUnavailable):
        read_snapshot(db, "tenant-a", "dashboard_summary")

    adapter = AsyncMock()
    with patch.object(dashboard, "read_snapshot", side_effect=SnapshotReadUnavailable()):
        with pytest.raises(HTTPException) as error:
            await dashboard.get_dashboard_summary(
                user={"company_id": "tenant-a", "sub": "owner-a", "role": "owner"},
                adapter=adapter, db=db,
            )
    assert error.value.status_code == 503
    adapter.get_all_customers.assert_not_awaited()
    adapter.get_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_briefing_cache_outage_is_explicit_503():
    with patch.object(
        briefing, "read_snapshot", side_effect=SnapshotReadUnavailable()
    ):
        with pytest.raises(HTTPException) as error:
            await briefing.get_today_briefing(
                user={"company_id": "tenant-a", "sub": "owner-a", "role": "owner"},
                db=MagicMock(),
            )
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_new_invalid_snapshot_not_published_as_computed_success():
    payload = valid_payload()
    payload["sales_30d"] = float("nan")
    adapter = AsyncMock()
    with patch.object(dashboard, "read_snapshot", return_value=None), \
         patch.object(dashboard, "compute_dashboard_payload", new=AsyncMock(return_value=payload)), \
         patch.object(dashboard, "store_snapshot") as save:
        with pytest.raises(HTTPException) as error:
            await dashboard.get_dashboard_summary(
                user={"company_id": "tenant-a", "sub": "owner-a", "role": "owner"},
                adapter=adapter, db=MagicMock(),
            )
    assert error.value.status_code == 503
    save.assert_not_called()
