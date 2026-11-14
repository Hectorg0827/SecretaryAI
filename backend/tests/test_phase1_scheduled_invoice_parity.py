"""Scheduled-account integrity: verified invoice summaries, no phantom DB writes."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.scheduler.account_health_refresh import refresh_all_account_health
from app.scheduler.weekly_report import collect_weekly_figures
from tasks.proactive_insights import _generate_for_company


class Adapter:
    def __init__(self):
        self.get_all_customers = AsyncMock(return_value=[
            SimpleNamespace(qb_id="C-1", name="Synthetic", balance=Decimal("0"))
        ])
        self.summary_days: list[int] = []
        self.invoice_summaries = [
            SimpleNamespace(qb_id="I-1", customer_id="C-1",
                            customer_name="Synthetic", date=date.today() - timedelta(days=2),
                            due_date=date.today() + timedelta(days=30),
                            total=Decimal("100"), balance=Decimal("0"),
                            status="paid", line_items=[], line_items_observed=False)
        ]
        self.get_orders_last_n_days = AsyncMock(
            side_effect=AssertionError("The strict item-line invoice reader must not be used"))
        self.get_inventory_merged = AsyncMock(return_value=[])

    async def get_invoice_summaries_last_n_days(self, days):
        self.summary_days.append(days)
        return self.invoice_summaries


class MissingScoreQuery:
    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class MissingScoreDB:
    def table(self, table):
        assert table == "accounts"
        return MissingScoreQuery()


@pytest.mark.asyncio
async def test_account_health_refresh_does_not_claim_missing_supabase_rows_are_updated():
    adapter = Adapter()
    result = await refresh_all_account_health("company-1", adapter, MissingScoreDB())
    assert result["updated"] == 0
    assert result["failed"] == 1
    adapter.get_orders_last_n_days.assert_not_awaited()
    assert adapter.summary_days == [365]


@pytest.mark.asyncio
async def test_proactive_signals_use_header_only_invoice_evidence():
    adapter = Adapter()
    result = await _generate_for_company("company-1", adapter)
    assert isinstance(result, list)
    adapter.get_orders_last_n_days.assert_not_awaited()
    assert adapter.summary_days == [90]


@pytest.mark.asyncio
async def test_weekly_report_uses_header_only_and_verified_billable_invoices():
    adapter = Adapter()
    today = date.today()
    snapshot = {
        "accounts": {"total": 1, "healthy": 1, "slowing": 0,
                     "at_risk": 0, "dormant": 0, "unknown": 0},
        "inventory_alerts": [], "sales_30d": 100,
        "inventory_unassessed": 0,
    }
    with patch("app.scheduler.weekly_report.compute_dashboard_payload",
               new=AsyncMock(return_value=snapshot)):
        result = await collect_weekly_figures(
            adapter, period_start=today - timedelta(days=7),
            period_end=today - timedelta(days=1))
    assert result["figures"]["sales_this_week"] == 100
    assert result["figures"]["invoices_this_week"] == 1
    assert adapter.summary_days == [14]
    adapter.get_orders_last_n_days.assert_not_awaited()
