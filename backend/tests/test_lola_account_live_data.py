"""Regression: Lola and Accounts must read the same verified QBO invoice headers.

All examples are synthetic; the strict product-line reader is never invoked.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.data_summarizer import build_query_context
from app.api.accounts import list_accounts
from app.api.chat import _fetch_data_for_intent


class SummaryAdapter:
    def __init__(self, *, second_customer=False, unavailable=False):
        self.days_requested = []
        self.unavailable = unavailable
        self.full_reader_called = False
        self.customers = [
            SimpleNamespace(qb_id="11", name="Bill's Windsurf Shop",
                            balance=Decimal("85"), email="Surf@Intuit.com")
        ]
        if second_customer:
            self.customers.append(SimpleNamespace(
                qb_id="12", name="Bill's Windsurf Outlet", balance=Decimal("0")))
        self.invoices = [
            self.invoice("11", "one", 141, "220"),
            self.invoice("11", "two", 111, "170"),
            self.invoice("11", "three", 81, "130"),
            self.invoice("11", "four", 51, "50"),
        ]

    @staticmethod
    def invoice(cid, invoice_id, age, total):
        return SimpleNamespace(
            qb_id=invoice_id, customer_id=cid, customer_name="Bill's Windsurf Shop",
            date=date.today() - timedelta(days=age), total=Decimal(total),
            status="paid", line_items=[], line_items_observed=False
        )

    async def get_all_customers(self):
        return self.customers

    async def get_invoice_summaries_last_n_days(self, days):
        self.days_requested.append(days)
        if self.unavailable:
            raise ValueError("QBO invoice line Qty is missing or invalid")
        return self.invoices

    async def get_orders_last_n_days(self, days):
        self.full_reader_called = True
        raise AssertionError("Do not call strict item-line reader for account summaries")

    async def get_inventory_merged(self):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "why is Bills windsurf slow?",
    "Regarding the accounts item named Bill's Windsurf Shop: explain sales",
])
async def test_lola_matches_bill_and_agrees_with_accounts_screen(query):
    adapter = SummaryAdapter()
    visible = await list_accounts(limit=50, user={"role": "owner"}, adapter=adapter)
    assert visible["total"] == 1
    ui = visible["accounts"][0]
    assert ui["health_status"] == "slowing"

    reply = await _fetch_data_for_intent("account_status", query, adapter)
    account = reply["account"]
    assert account["name"] == "Bill's Windsurf Shop"
    assert account["health_status"] == ui["health_status"]
    assert account["health_score"] == ui["health_score"]
    assert account["current_balance"] == 85
    assert account["invoice_count_365d"] == 4
    assert account["trend_30d"]["current"]["revenue"] == 0
    assert account["trend_30d"]["prior"]["revenue"] == 50
    assert account["trend_30d"]["change_pct"] == -100.0
    assert "Order overdue" in " ".join(account["health_flags"])
    assert adapter.days_requested == [365, 365]
    assert adapter.full_reader_called is False
    summary = build_query_context("account_status", reply)
    assert "Bill's Windsurf Shop" in summary
    assert "Health-scoring evidence" in summary
    assert "Last 30 days" in summary
    assert "depletion rates" in summary
    assert "not proof" in summary


@pytest.mark.asyncio
async def test_no_short_name_guess_when_multiple_accounts_match():
    adapter = SummaryAdapter(second_customer=True)
    data = await _fetch_data_for_intent("account_status", "why is Bills windsurf slow?", adapter)
    assert "account" not in data
    assert data["ambiguous_accounts"] == [
        "Bill's Windsurf Shop", "Bill's Windsurf Outlet"
    ]
    assert "Multiple accounts match" in build_query_context("account_status", data)


@pytest.mark.asyncio
async def test_slowness_overview_identifies_scored_slowing_customers():
    adapter = SummaryAdapter()
    data = await _fetch_data_for_intent("account_status", "which accounts are slowing?", adapter)
    assert data["accounts"][0]["health_status"] == "slowing"
    summary = build_query_context("account_status", data)
    assert "Slowing: 1" in summary
    assert "Bill's Windsurf Shop (slowing)" in summary


@pytest.mark.asyncio
async def test_general_business_chat_surfaces_real_billed_invoice_metrics():
    adapter = SummaryAdapter()
    data = await _fetch_data_for_intent("general_question", "what is happening?", adapter)
    assert data["accounts"][0]["health_status"] == "slowing"
    assert data["sales_summary"]["gross_billed_amount"] == 0
    assert data["sales_summary"]["order_count"] == 0
    summary = build_query_context("general_question", data)
    assert "gross billed invoices" in summary
    assert "NOT recognized revenue" in summary
    assert "Slowing: 1" in summary
    assert adapter.full_reader_called is False


@pytest.mark.asyncio
async def test_account_provider_outage_is_503_not_a_fake_empty_result():
    adapter = SummaryAdapter(unavailable=True)
    with pytest.raises(HTTPException) as error:
        await _fetch_data_for_intent("account_status", "Bills Windsurf", adapter)
    assert error.value.status_code == 503
    assert "temporarily unavailable" in error.value.detail
    assert adapter.full_reader_called is False


@pytest.mark.asyncio
async def test_invalid_invoice_identity_does_not_silently_drop_all_sales():
    adapter = SummaryAdapter()
    adapter.invoices[0].qb_id = None
    with pytest.raises(HTTPException) as error:
        await _fetch_data_for_intent("account_status", "Bills Windsurf", adapter)
    assert error.value.status_code == 503
