"""One legitimate customer credit must not abort unrelated account health scoring."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.scheduler.account_health_refresh import refresh_all_account_health
from app.scheduler.dashboard_snapshot import compute_dashboard_payload


class CreditBalanceAdapter:
    async def get_all_customers(self):
        return [
            SimpleNamespace(qb_id="credit-customer", name="Credit Account", balance=Decimal("-12.50")),
            SimpleNamespace(qb_id="ordinary-customer", name="Ordinary Account", balance=Decimal("0")),
        ]

    async def get_orders_last_n_days(self, days):
        return []

    async def get_inventory_merged(self):
        return []


class RecordingDB:
    def __init__(self):
        self.updates = []
        self.pending = None

    def table(self, name):
        assert name == "accounts"
        return self

    def update(self, values):
        self.pending = dict(values)
        return self

    def eq(self, key, value):
        return self

    def execute(self):
        self.updates.append(self.pending)
        return SimpleNamespace(data=[self.pending])


@pytest.mark.asyncio
async def test_nightly_refresh_marks_credit_balance_unknown_and_keeps_scoring():
    db = RecordingDB()
    result = await refresh_all_account_health("tenant-fixture", CreditBalanceAdapter(), db)

    assert result["unscored"] == 1
    assert result["updated"] == 1
    assert len(db.updates) == 2
    assert db.updates[0]["health_status"] == "unknown"
    assert db.updates[0]["health_score"] is None
    assert db.updates[1]["health_status"] in {"healthy", "slowing", "at_risk", "dormant"}
    assert isinstance(db.updates[1]["health_score"], int)


@pytest.mark.asyncio
async def test_dashboard_counts_credit_balance_as_unknown_without_losing_other_accounts():
    summary = await compute_dashboard_payload(CreditBalanceAdapter())
    accounts = summary["accounts"]

    assert accounts["unknown"] == 1
    assert accounts["total"] == 2
    assert sum(accounts[status] for status in ("healthy", "slowing", "at_risk", "dormant")) == 1
