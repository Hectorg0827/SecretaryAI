"""Account/dashboard invoice headers are independent of item-line analysis.

All provider responses in these tests are synthetic. No accounting writes or
network calls are allowed.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.api.accounts import list_accounts
from app.api.dashboard import get_sales_data
from app.connectors.base import Customer
from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.unified_adapter import UnifiedDataAdapter, read_invoice_summaries


def _online():
    return QBOnlineAdapter("client", "secret", "fake-access", "fake-refresh", "fake-realm")


def _provider_invoice():
    return {
        "Id": "invoice-1", "TxnDate": date.today().isoformat(),
        "TotalAmt": "125.25", "Balance": "25.25",
        "CustomerRef": {"value": "customer-1", "name": "Example"},
        # Missing the sold-item reference makes the *item* projection invalid.
        # Verified invoice headers remain appropriate for account/revenue totals.
        "Line": [{"Id": "1", "DetailType": "SalesItemLineDetail",
                  "Amount": "125.25", "SalesItemLineDetail": {"Qty": 1}}],
    }


@pytest.mark.asyncio
async def test_valid_header_survives_unreadable_item_details_only_in_summary_mode():
    qb = _online()
    qb._query = AsyncMock(return_value={"QueryResponse": {"Invoice": [_provider_invoice()]}})
    with pytest.raises(ValueError, match="ItemRef"):
        await qb.get_invoices(date.today(), date.today())
    headers = await qb.get_invoice_summaries(date.today(), date.today())
    assert len(headers) == 1
    assert headers[0].total == Decimal("125.25")
    assert headers[0].balance == Decimal("25.25")
    assert headers[0].customer_id == "customer-1"
    assert headers[0].line_items == []
    assert headers[0].line_items_observed is False


@pytest.mark.parametrize("field", ["Id", "TxnDate", "TotalAmt", "Balance", "CustomerRef"])
def test_header_only_rejects_missing_financial_or_identity_fields(field):
    qb = _online()
    raw = _provider_invoice()
    del raw[field]
    with pytest.raises(ValueError):
        qb._normalize_invoice_summary(raw)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-1", None])
def test_header_only_rejects_invalid_financial_amounts(invalid):
    qb = _online()
    raw = _provider_invoice()
    raw["TotalAmt"] = invalid
    with pytest.raises(ValueError):
        qb._normalize_invoice_summary(raw)


def test_qbo_void_marker_is_preserved_in_header_and_full_invoice_readers():
    qb = _online()
    raw = _provider_invoice()
    raw.update({"TotalAmt": "0", "Balance": "0",
                "PrivateNote": "Voided by QuickBooks on request", "Line": []})
    assert qb._normalize_invoice_summary(raw).status == "void"
    assert qb._normalize_invoice(raw).status == "void"
    raw["PrivateNote"] = "Ordinary customer note"
    assert qb._normalize_invoice_summary(raw).status == "paid"


@pytest.mark.asyncio
async def test_account_and_sales_routes_use_headers_but_product_detail_stays_strict():
    qb = _online()
    qb._query = AsyncMock(return_value={"QueryResponse": {"Invoice": [_provider_invoice()]}})
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = qb
    adapter.get_all_customers = AsyncMock(return_value=[Customer(
        id="customer-1", qb_id="customer-1", name="Example",
        email=None, phone=None, state="NY", balance=Decimal("25.25"),
    )])

    accounts = await list_accounts(limit=50, user={"role": "owner"}, adapter=adapter)
    assert accounts["total"] == 1
    assert accounts["accounts"][0]["avg_order_value"] == 125.25
    sales = await get_sales_data(days=30, user={"role": "owner"}, adapter=adapter)
    assert sales["total_revenue"] == 125.25
    assert sales["order_count"] == 1
    with pytest.raises(ValueError, match="ItemRef"):
        await adapter.get_orders_last_n_days(30)


@pytest.mark.asyncio
async def test_legacy_adapter_uses_existing_strict_reader():
    adapter = AsyncMock()
    adapter.get_orders_last_n_days.return_value = []
    assert await read_invoice_summaries(adapter, 365) == []
    adapter.get_orders_last_n_days.assert_awaited_once_with(365)


@pytest.mark.asyncio
async def test_dashboard_chart_and_prior_period_ignore_void_draft_and_cancelled_invoices():
    from datetime import timedelta
    from app.connectors.base import Invoice

    def invoice(ref, amount, status, days_ago):
        return Invoice(
            id=ref, qb_id=ref, customer_id="customer-1", customer_name="Example",
            date=date.today() - timedelta(days=days_ago), due_date=None,
            total=Decimal(str(amount)), balance=Decimal("0"),
            status=status, line_items=[],
        )

    current = [invoice("paid", 40, "paid", 1),
               invoice("voided", 10000, "void", 1),
               invoice("draft", 500, "draft", 2)]
    previous = [invoice("prior", 20, "paid", 40),
                invoice("prior-cancel", 1000, "cancelled", 42)]
    adapter = AsyncMock()
    adapter.get_orders_last_n_days.side_effect = [current, current + previous]
    result = await get_sales_data(days=30, user={"role": "owner"}, adapter=adapter)
    assert result["total_revenue"] == 40
    assert result["order_count"] == 1
    assert result["avg_order_value"] == 40
    assert result["vs_prior_period_pct"] == 100.0
    assert sum(day["order_count"] for day in result["chart_data"]) == 1
    assert sum(day["revenue"] for day in result["chart_data"]) == 40
