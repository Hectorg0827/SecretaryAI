"""Inventory forecasting must use verified invoice history, not fabricated sales."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.inventory import get_item_forecast


def _adapter(invoices, **item_overrides):
    item = {
        "qb_id": "QB-1", "product_name": "Malta",
        "sku": "MALT-1", "total_qty": 120, "weekly_sell_rate": 999,
    }
    item.update(item_overrides)
    adapter = SimpleNamespace(
        get_inventory_merged=AsyncMock(return_value=[item]),
        get_orders_last_n_days=AsyncMock(return_value=invoices),
    )
    return adapter


@pytest.mark.asyncio
async def test_forecast_uses_real_invoice_lines_not_fabricated_weekly_rate():
    today = date.today()
    invoices = [
        SimpleNamespace(
            date=today - timedelta(days=7 * week),
            status="open",
            line_items=[{"sku": "MALT-1", "name": "Malta", "quantity": 10}],
        )
        for week in range(8)
    ]
    # Other product and void invoices must not inflate Malta sales.
    invoices += [
        SimpleNamespace(
            date=today, status="open",
            line_items=[{"sku": "OTHER", "name": "Other", "quantity": 999}],
        ),
        SimpleNamespace(
            date=today, status="void",
            line_items=[{"sku": "MALT-1", "name": "Malta", "quantity": 999}],
        ),
    ]
    adapter = _adapter(invoices, lead_time_weeks=3)
    result = await get_item_forecast("QB-1", user={"company_id": "co-1"}, adapter=adapter)

    assert result["historical_invoice_count"] == 8
    assert sum(result["weekly_sell_history"]) == 80
    assert result["recommended_order_qty"] == 80
    assert result["lead_time_weeks"] == 3
    assert result["lead_time_source"] == "product"
    assert result["confidence"] == "high"
    adapter.get_orders_last_n_days.assert_awaited_once_with(84)


@pytest.mark.asyncio
async def test_no_invoice_evidence_means_no_invented_forecast():
    adapter = _adapter([])
    result = await get_item_forecast("QB-1", user={"company_id": "co-1"}, adapter=adapter)

    assert result["confidence"] == "low"
    assert result["weekly_sell_history"] == []
    assert result["recommended_order_qty"] == 0
    assert result["projected_stockout_date"] is None
    assert result["lead_time_source"] == "industry_default"


@pytest.mark.asyncio
async def test_unavailable_invoice_source_is_an_error_not_zero_sales():
    adapter = _adapter([])
    adapter.get_orders_last_n_days.side_effect = ConnectionError("QuickBooks unavailable")
    with pytest.raises(HTTPException) as err:
        await get_item_forecast("QB-1", user={"company_id": "co-1"}, adapter=adapter)
    assert err.value.status_code == 503


@pytest.mark.asyncio
async def test_unknown_item_is_404_before_invoice_lookup():
    adapter = _adapter([])
    with pytest.raises(HTTPException) as err:
        await get_item_forecast("not-found", user={"company_id": "co-1"}, adapter=adapter)
    assert err.value.status_code == 404
    adapter.get_orders_last_n_days.assert_not_awaited()
