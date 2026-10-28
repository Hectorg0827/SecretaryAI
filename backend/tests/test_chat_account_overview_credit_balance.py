"""The assistant's account overview must not be truncated by one unscorable account."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.chat import _fetch_data_for_intent


class OverviewAdapter:
    async def get_all_customers(self):
        return [
            SimpleNamespace(qb_id="QB-1", name="First Account", balance=Decimal("0")),
            SimpleNamespace(qb_id="QB-2", name="Customer In Credit", balance=Decimal("-40.00")),
            SimpleNamespace(qb_id="QB-3", name="Third Account", balance=Decimal("15.00")),
        ]

    async def get_orders_last_n_days(self, days):
        recent = date.today() - timedelta(days=3)
        return [
            SimpleNamespace(customer_id=cid, date=recent, total=Decimal("100"))
            for cid in ("QB-1", "QB-2", "QB-3")
        ]


@pytest.mark.asyncio
async def test_credit_balance_account_is_unknown_and_overview_is_complete():
    # No account name appears in the message, so the all-accounts overview runs.
    data = await _fetch_data_for_intent("account_status", "how are my accounts doing", OverviewAdapter())
    accounts = {a["name"]: a for a in data["accounts"]}

    assert list(accounts) == ["First Account", "Customer In Credit", "Third Account"]
    assert accounts["Customer In Credit"]["health_status"] == "unknown"
    assert accounts["Customer In Credit"]["health_score"] is None
    assert accounts["Third Account"]["health_status"] != "unknown"
