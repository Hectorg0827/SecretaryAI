"""Gate A regression: scheduler must speak UnifiedDataAdapter's actual API."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.connectors.base import Customer, Invoice, InventoryItem
from app.scheduler.overnight_scan import run_overnight_scan


def _customer(qb_id, name):
    return Customer(
        id=f"internal-{qb_id}", qb_id=qb_id, name=name,
        email=None, phone=None, state=None,
        balance=Decimal("0"), total_sales=Decimal("0"),
    )


class StrictUnifiedAdapter:
    """Implements only the actual unified read contract, not QB native names."""
    def __init__(self, *, fail=False):
        self.fail = fail
        self.days_requested = None

    async def get_all_customers(self):
        if self.fail:
            raise RuntimeError("accounting source unavailable")
        return [_customer("QB-A", "Active buyer"), _customer("QB-B", "No orders")]

    async def get_orders_last_n_days(self, days):
        self.days_requested = days
        return [Invoice(
            id="inv-1", qb_id="inv-1", customer_id="QB-A",
            customer_name="Active buyer", date=date.today() - timedelta(days=4),
            due_date=date.today() + timedelta(days=26),
            total=Decimal("100"), balance=Decimal("100"),
            status="open", line_items=[{"item_id": "SKU-1", "quantity": 260}],
        )]

    async def get_inventory_qb(self):
        return [InventoryItem(
            id="i-1", qb_id="SKU-1", name="Low-stock SKU", sku="SKU-1",
            quantity_on_hand=Decimal("2"), unit_price=Decimal("10"),
            purchase_cost=Decimal("5"), reorder_point=Decimal("20"),
        )]


@pytest.mark.asyncio
async def test_scan_consumes_real_unified_adapter_contract():
    adapter = StrictUnifiedAdapter()
    result = await run_overnight_scan("co-1", adapter)
    assert adapter.days_requested == 365
    assert result["company_id"] == "co-1"
    assert result["accounts_scanned"] == 2
    assert result["account_alerts"][0]["account_name"] == "No orders"
    assert result["inventory_alerts"][0]["status"] == "critical"
    assert result["alerts_detected"] == 2
    assert result["alerts_queued"] == 0  # observing is not sending


@pytest.mark.asyncio
async def test_source_failure_never_returns_false_all_clear():
    with pytest.raises(RuntimeError, match="accounting source unavailable"):
        await run_overnight_scan("co-1", StrictUnifiedAdapter(fail=True))
