"""Gate A persistence receipt checks for the report_snapshots write boundary.

All responses are synthetic PostgREST shapes, no real database mutations.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.scheduler.dashboard_snapshot import (
    SnapshotPersistenceUnavailable, store_snapshot,
)

COMPANY = "tenant-fixture-a"
TYPE = "dashboard_summary"
PAYLOAD = {"source_quality": "partial", "source_read_status": "completed"}


def simulated_upsert(data):
    db = MagicMock()
    db.table.return_value.upsert.return_value.execute.return_value = (
        SimpleNamespace(data=data)
    )
    return db


def expected_row(company_id=COMPANY, report_type=TYPE):
    return {"company_id": company_id, "report_type": report_type, "payload": PAYLOAD}


@pytest.mark.parametrize("ack", [
    None, [], {}, [{"company_id": COMPANY}], [{}],
    [expected_row("tenant-other")], [expected_row(report_type="overnight_scan")],
    [expected_row(), expected_row()],
])
def test_missing_or_mismatched_snapshot_receipt_is_not_reported_as_saved(ack):
    db = simulated_upsert(ack)
    with pytest.raises(SnapshotPersistenceUnavailable):
        store_snapshot(db, COMPANY, TYPE, PAYLOAD, generated_by="fixture")
    db.table.assert_called_once_with("report_snapshots")
    assert db.table.return_value.upsert.call_args.kwargs["on_conflict"] == (
        "company_id,report_type"
    )


def test_tenant_and_report_matching_upsert_receipt_confirms_snapshot():
    db = simulated_upsert([expected_row()])
    result = store_snapshot(db, COMPANY, TYPE, PAYLOAD, generated_by="fixture")
    assert result is None
    sent = db.table.return_value.upsert.call_args.args[0]
    assert sent["company_id"] == COMPANY
    assert sent["report_type"] == TYPE
    assert sent["generated_by"] == "fixture"


def test_snapshot_write_exception_does_not_masquerade_as_success():
    db = MagicMock()
    db.table.return_value.upsert.return_value.execute.side_effect = (
        ConnectionError("Synthetic storage unavailable")
    )
    with pytest.raises(SnapshotPersistenceUnavailable):
        store_snapshot(db, COMPANY, TYPE, PAYLOAD, generated_by="fixture")
