"""Inventory forecasting must use verified invoice history, not fabricated sales."""
from datetime import date, timedelta
from decimal import Decimal
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


@pytest.mark.asyncio
async def test_qb_and_warehouse_overlap_is_not_double_counted():
    from decimal import Decimal
    from app.connectors.base import InventoryItem
    from app.connectors.unified_adapter import UnifiedDataAdapter

    async def merge_with_policy(policy, rows):
        # Construct without cloud connector side effects. Exercise the real
        # merging implementation with mocked authorized source responses.
        adapter = object.__new__(UnifiedDataAdapter)
        adapter._config = {"id": "co-1", "inventory_quantity_policy": policy}
        adapter._db = None
        adapter.get_inventory_qb = AsyncMock(return_value=[
            InventoryItem(
                id="item-1", qb_id="QB-1", name="Malta", sku="MALT-1",
                quantity_on_hand=Decimal("100"), unit_price=Decimal("10"),
                purchase_cost=Decimal("6"), reorder_point=Decimal("20"),
            )
        ])
        adapter._get_router = lambda: SimpleNamespace(
            route=AsyncMock(return_value=SimpleNamespace(data={"rows": rows}))
        )
        return (await adapter.get_inventory_merged())[0]

    warehouse = [{"sku": "MALT-1", "product_name": "Malta", "quantity": 60}]
    default = await merge_with_policy("qb_authoritative", warehouse)
    assert default["qb_qty"] == 100
    assert default["warehouse_qty"] == 60
    assert default["total_qty"] == 100  # NOT 160
    assert default["reconciliation_required"] is True
    assert default["quantity_source"] == "quickbooks"

    explicitly_warehouse = await merge_with_policy("warehouse_authoritative", warehouse)
    assert explicitly_warehouse["total_qty"] == 60
    assert explicitly_warehouse["quantity_source"] == "warehouse"

    # A fuzzy substring must never associate an unrelated warehouse item.
    unrelated = await merge_with_policy(
        "qb_authoritative", [{"product_name": "Malta Extra", "quantity": 500}]
    )
    assert unrelated["total_qty"] == 100
    assert unrelated["source"] == "qb"

@pytest.mark.parametrize("observed,rate,qty,expected", [
    (False, 0, 120, "unassessed"),
    (False, None, 120, "unassessed"),
    (False, 0, 0, "out_of_stock"),
    (True, 0, 120, "healthy"),
    (True, 10, 15, "critical"),
    (True, 0.1, 0.5, "healthy"),
])
def test_inventory_status_requires_observed_velocity(observed, rate, qty, expected):
    from app.api.inventory import _inventory_status
    status = _inventory_status({"total_qty": qty, "weekly_sell_rate": rate,
                                "weekly_sell_rate_observed": observed})
    assert status.status == expected
    assert status.total_qty == Decimal(str(qty))
    if not observed:
        assert status.weeks_remaining is None


@pytest.mark.asyncio
async def test_merge_reads_actual_staging_inventory_qb_item_id_column():
    from app.connectors.base import InventoryItem
    from app.connectors.unified_adapter import UnifiedDataAdapter

    class Query:
        fields = None

        def select(self, fields):
            self.fields = fields
            return self

        def eq(self, column, value):
            assert (column, value) == ("company_id", "test-company")
            return self

        def execute(self):
            return SimpleNamespace(data=[{"qb_item_id": "QB-1", "weekly_sell_rate": 3}])

    query = Query()
    adapter = object.__new__(UnifiedDataAdapter)
    adapter._config = {"id": "test-company", "inventory_quantity_policy": "qb_authoritative"}
    adapter._db = SimpleNamespace(table=lambda name: query if name == "inventory" else None)
    adapter.get_inventory_qb = AsyncMock(return_value=[
        InventoryItem(
            id="item-1", qb_id="QB-1", name="Malta", sku="MALT-1",
            quantity_on_hand=Decimal("100"), unit_price=Decimal("10"),
            purchase_cost=Decimal("6"), reorder_point=Decimal("20"),
        )
    ])
    adapter._get_router = lambda: SimpleNamespace(route=AsyncMock(side_effect=RuntimeError("No warehouse source")))

    merged = await adapter.get_inventory_merged()
    assert query.fields == "qb_item_id,weekly_sell_rate"
    assert merged[0]["weekly_sell_rate"] == 3
    assert merged[0]["weekly_sell_rate_observed"] is True


@pytest.mark.asyncio
async def test_inventory_list_and_alerts_expose_unassessed_stock():
    from app.api.inventory import list_inventory, get_inventory_alerts
    adapter = _adapter([], weekly_sell_rate=0, weekly_sell_rate_observed=False)
    result = await list_inventory(limit=50, offset=0, status_filter=None, user={}, adapter=adapter)
    assert result["items"][0]["stock_status"] == "unassessed"
    assert result["items"][0]["weeks_remaining"] is None
    alerts = await get_inventory_alerts(user={}, adapter=adapter)
    assert alerts == {"alerts": [], "count": 0, "unassessed_count": 1}


@pytest.mark.parametrize("qty,rate", [(None, 10), ("NaN", 10), (-1, 10), (10, "Infinity"), (10, -1)])
def test_invalid_inventory_measurement_fails_closed(qty, rate):
    from app.api.inventory import _inventory_status
    with pytest.raises(HTTPException) as error:
        _inventory_status({"total_qty": qty, "weekly_sell_rate": rate, "weekly_sell_rate_observed": True})
    assert error.value.status_code == 503
