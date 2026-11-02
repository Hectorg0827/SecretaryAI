"""Every sync check result must be stored with a status the table accepts."""
import json
import logging
from types import SimpleNamespace

import pytest

from app.api.insights.sync_health import _classify
from app.scheduler.sync_check import run_sync_check

ALLOWED = {"healthy", "warning", "broken", "unknown"}  # sync_checks CHECK constraint


class _Table:
    def __init__(self, store):
        self.store = store

    def insert(self, record):
        json.dumps(record)  # JSONB column: the record must serialise
        assert record["status"] in ALLOWED
        self.store.append(record)
        return self

    def execute(self):
        return SimpleNamespace(data=self.store)


class _Db:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "sync_checks"
        return _Table(self.rows)


class _Adapter:
    def __init__(self, qb_qty=None, wh_qty=None, qb_error=False):
        self.qb_qty, self.wh_qty, self.qb_error = qb_qty, wh_qty, qb_error

    async def get_inventory_qb(self):
        if self.qb_error:
            raise ConnectionError("token=secret provider detail")
        return [SimpleNamespace(name="Widget", quantity_on_hand=self.qb_qty)]

    async def get_inventory_merged(self):
        return [{"product_name": "Widget", "warehouse_qty": self.wh_qty, "source": "qb+warehouse"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("qb", "wh", "stored", "state"),
    [
        (100, 100, "healthy", "in_sync"),
        (100, 92, "warning", "mismatches_found"),
        (100, 50, "broken", "mismatches_found"),  # critical variance
    ],
)
async def test_reconciliation_results_are_stored_with_allowed_status(qb, wh, stored, state):
    db = _Db()
    await run_sync_check("co-1", _Adapter(qb, wh), db)
    assert len(db.rows) == 1
    row = db.rows[0]
    assert row["status"] == stored
    assert _classify(row)["state"] == state


@pytest.mark.asyncio
async def test_failed_quickbooks_read_is_recorded_as_failed_check(caplog):
    db = _Db()
    with caplog.at_level(logging.WARNING):
        result = await run_sync_check("co-1", _Adapter(qb_error=True), db)
    assert result["status"] == "error"
    assert len(db.rows) == 1
    assert db.rows[0]["status"] == "broken"
    assert _classify(db.rows[0])["state"] == "check_failed"
    assert "secret" not in json.dumps(db.rows[0])
    assert "secret" not in caplog.text
