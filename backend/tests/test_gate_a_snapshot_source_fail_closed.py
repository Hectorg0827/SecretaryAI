"""Gate A regression: incomplete data must not masquerade as an all-clear dashboard."""
from unittest.mock import AsyncMock

import pytest

from app.scheduler.dashboard_snapshot import (
    SnapshotSourceUnavailable,
    compute_dashboard_payload,
)


class CompleteReader:
    def __init__(self):
        self.customers = []
        self.invoices = []
        self.inventory = []
        self.read_count = 0

    async def get_all_customers(self):
        return self.customers

    async def get_orders_last_n_days(self, days):
        self.read_count += 1
        return self.invoices

    async def get_inventory_merged(self):
        return self.inventory


@pytest.mark.asyncio
async def test_verified_empty_business_is_legitimately_zero():
    adapter = CompleteReader()
    result = await compute_dashboard_payload(adapter)
    assert result["source_quality"] == "verified"
    assert result["accounts"]["total"] == 0
    assert result["inventory_alerts"] == []
    assert result["sales_30d"] == 0
    assert adapter.read_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_method, expected_source", [
    ("get_all_customers", "account_health"),
    ("get_inventory_merged", "inventory"),
])
async def test_required_source_failure_raises_instead_of_reporting_zero(
    broken_method, expected_source
):
    adapter = CompleteReader()

    async def broken():
        raise ConnectionError("Provider unavailable")

    setattr(adapter, broken_method, broken)
    with pytest.raises(SnapshotSourceUnavailable) as info:
        await compute_dashboard_payload(adapter)
    assert info.value.source == expected_source


@pytest.mark.asyncio
async def test_second_invoice_read_failure_also_blocks_successful_snapshot():
    adapter = CompleteReader()

    async def invoice_reader(days):
        if days == 30:
            raise TimeoutError("Provider timeout")
        return []

    adapter.get_orders_last_n_days = invoice_reader
    with pytest.raises(SnapshotSourceUnavailable) as info:
        await compute_dashboard_payload(adapter)
    assert info.value.source == "sales_30d"
