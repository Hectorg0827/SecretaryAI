"""One legitimate customer credit must not abort unrelated account health scoring.

The dashboard snapshot's per-account boundary lives with Gate A's
dashboard_snapshot.py (tests/test_gate_a_unscored_accounts.py on that branch).
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.scheduler.account_health_refresh import refresh_all_account_health


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


class DictCustomerAdapter(CreditBalanceAdapter):
    async def get_all_customers(self):
        return [
            {"qb_id": "dict-credit", "name": "Dict Credit", "balance": Decimal("-40.00")},
            {"qb_id": "dict-missing", "name": "Dict Missing"},
            {"qb_id": "dict-ordinary", "name": "Dict Ordinary", "balance": Decimal("15.00")},
        ]


@pytest.mark.asyncio
async def test_nightly_refresh_reads_dict_balances_instead_of_assuming_zero():
    db = RecordingDB()
    result = await refresh_all_account_health("tenant-fixture", DictCustomerAdapter(), db)

    assert result["unscored"] == 2
    assert result["updated"] == 1
    statuses = [update["health_status"] for update in db.updates]
    assert statuses[:2] == ["unknown", "unknown"]
    assert statuses[2] in {"healthy", "slowing", "at_risk", "dormant"}
