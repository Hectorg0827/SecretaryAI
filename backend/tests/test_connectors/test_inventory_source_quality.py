"""Inventory provenance and source-failure contracts; no provider or DB access."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.base import InventoryItem
from app.connectors.unified_adapter import UnifiedDataAdapter
from app.domain.contracts import DataPath
from app.domain.data_result import DataResult, Confidence


def _adapter(*, policy="qb_authoritative", rows=None, error=None):
    adapter = object.__new__(UnifiedDataAdapter)
    adapter._config = {"id": "fixture-tenant", "inventory_quantity_policy": policy}
    adapter._db = None
    adapter.get_inventory_qb = AsyncMock(return_value=[InventoryItem(
        id="item-1", qb_id="item-1", name="Fixture Product", sku="SKU-1",
        quantity_on_hand=Decimal("100"), unit_price=Decimal("10"),
        purchase_cost=Decimal("6"), reorder_point=Decimal("25"),
    )])
    route = AsyncMock()
    if error:
        route.side_effect = error
    else:
        route.return_value = SimpleNamespace(data={"rows": rows if rows is not None else []})
    adapter._get_router = lambda: SimpleNamespace(route=route)
    return adapter


@pytest.mark.asyncio
async def test_optional_warehouse_outage_is_visible_without_double_counting():
    adapter = _adapter(error=ConnectionError("fixture warehouse unavailable"))
    item = (await adapter.get_inventory_merged())[0]
    assert item["total_qty"] == item["qb_qty"] == 100
    assert item["quantity_source"] == "quickbooks"
    assert item["source_quality"] == "partial"
    assert item["warehouse_source_status"] == "unavailable"
    assert item["warehouse_qty_observed"] is False
    assert item["weekly_sell_rate_observed"] is False


@pytest.mark.asyncio
async def test_warehouse_authoritative_outage_must_fail_closed():
    adapter = _adapter(policy="warehouse_authoritative", error=TimeoutError("fixture"))
    with pytest.raises(RuntimeError, match="warehouse source"):
        await adapter.get_inventory_merged()


@pytest.mark.asyncio
async def test_warehouse_authoritative_missing_item_must_fail_closed():
    adapter = _adapter(policy="warehouse_authoritative", rows=[
        {"sku": "OTHER", "product_name": "Other", "quantity": 10}
    ])
    with pytest.raises(RuntimeError, match="warehouse quantity"):
        await adapter.get_inventory_merged()


@pytest.mark.asyncio
async def test_unmatched_warehouse_item_is_missing_not_verified_zero():
    adapter = _adapter(rows=[{"sku": "OTHER", "quantity": 20}])
    item = (await adapter.get_inventory_merged())[0]
    assert item["warehouse_source_status"] == "missing"
    assert item["warehouse_qty_observed"] is False
    assert item["total_qty"] == 100


@pytest.mark.asyncio
async def test_verified_warehouse_zero_is_distinct_from_absent_warehouse():
    adapter = _adapter(policy="warehouse_authoritative", rows=[
        {"sku": "SKU-1", "quantity": 0}
    ])
    item = (await adapter.get_inventory_merged())[0]
    assert item["warehouse_qty"] == 0
    assert item["warehouse_qty_observed"] is True
    assert item["warehouse_source_status"] == "observed"
    assert item["total_qty"] == 0
    assert item["reconciliation_required"] is True


@pytest.mark.asyncio
async def test_warehouse_authoritative_rejects_bad_quantity():
    adapter = _adapter(policy="warehouse_authoritative", rows=[
        {"sku": "SKU-1", "quantity": "-5"}
    ])
    with pytest.raises(RuntimeError, match="warehouse quantity"):
        await adapter.get_inventory_merged()


@pytest.mark.asyncio
async def test_router_failure_envelope_does_not_become_healthy_zero():
    adapter = _adapter()
    failure = DataResult.from_error("fixture read failed", DataPath.UNKNOWN, "inventory")
    adapter._get_router = lambda: SimpleNamespace(route=AsyncMock(return_value=failure))
    item = (await adapter.get_inventory_merged())[0]
    assert item["warehouse_source_status"] == "unavailable"
    assert item["warehouse_qty_observed"] is False
    assert item["source_quality"] == "partial"


@pytest.mark.asyncio
async def test_second_quickbooks_api_response_is_not_warehouse_evidence():
    adapter = _adapter()
    second_qb_read = DataResult.ok(
        data={"items": [{"qb_id": "item-1", "sku": "SKU-1", "quantity_on_hand": 100}]},
        source=DataPath.API, confidence=Confidence.API, capability="inventory",
    )
    adapter._get_router = lambda: SimpleNamespace(route=AsyncMock(return_value=second_qb_read))
    item = (await adapter.get_inventory_merged())[0]
    assert item["warehouse_source_path"] == "api"
    assert item["warehouse_source_status"] == "not_independent"
    assert item["warehouse_qty_observed"] is False
    assert item["source"] == "qb"
    assert item["total_qty"] == 100


@pytest.mark.asyncio
async def test_warehouse_authority_rejects_duplicate_quickbooks_api_source():
    adapter = _adapter(policy="warehouse_authoritative")
    second_qb_read = DataResult.ok(
        data={"items": [{"sku": "SKU-1", "quantity": 0}]},
        source=DataPath.API, confidence=Confidence.API, capability="inventory",
    )
    adapter._get_router = lambda: SimpleNamespace(route=AsyncMock(return_value=second_qb_read))
    with pytest.raises(RuntimeError, match="independent warehouse source"):
        await adapter.get_inventory_merged()


@pytest.mark.asyncio
async def test_stale_file_cannot_authorize_warehouse_stock():
    adapter = _adapter(policy="warehouse_authoritative")
    old_file = DataResult.ok(
        data={"rows": [{"sku": "SKU-1", "quantity": 60}]},
        source=DataPath.FILE, confidence=Confidence.FILE_STALE, capability="inventory",
    )
    # Fetching an old file today does not make its underlying stock data fresh.
    old_file.fetched_at = datetime.now(timezone.utc)
    adapter._get_router = lambda: SimpleNamespace(route=AsyncMock(return_value=old_file))
    with pytest.raises(RuntimeError, match="warehouse source"):
        await adapter.get_inventory_merged()


@pytest.mark.asyncio
async def test_zero_sales_rate_is_observed_only_if_database_has_a_row():
    from unittest.mock import MagicMock

    adapter = _adapter()
    db = MagicMock()
    db.table().select().eq().execute.return_value.data = [
        {"qb_id": "item-1", "weekly_sell_rate": 0}
    ]
    adapter._db = db
    item = (await adapter.get_inventory_merged())[0]
    assert item["weekly_sell_rate"] == 0
    assert item["weekly_sell_rate_observed"] is True
    assert item["weekly_sell_rate_source_status"] == "observed"
