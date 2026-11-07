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
