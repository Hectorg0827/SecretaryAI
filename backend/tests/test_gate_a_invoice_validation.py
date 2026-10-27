"""Gate A financial source validation: untrustworthy numbers never become sales KPIs.

No external provider/DB access: adapters below return synthetic read data.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.dashboard import get_sales_data
from app.scheduler.dashboard_snapshot import (
    SnapshotSourceUnavailable, _invoice_amount, compute_dashboard_payload,
)


@pytest.mark.parametrize("invalid", [
    "NaN", "Infinity", "-Infinity", "not-a-number",
    Decimal("NaN"), Decimal("Infinity"), True, "1e9999",
])
def test_invoice_amount_rejects_nonfinite_or_invalid_inputs(invalid):
    with pytest.raises(ValueError):
        _invoice_amount({"total": invalid})


@pytest.mark.parametrize("valid", [
    0, "0", Decimal("0"), "-12.50", Decimal("-12.50"),
])
def test_invoice_amount_preserves_legitimate_zero_and_credit_values(valid):
    assert _invoice_amount({"total": valid}) == pytest.approx(float(valid))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["NaN", "Infinity", True, "1e9999"])
async def test_snapshot_rejects_invalid_current_period_invoice(invalid):
    class Synthetic:
        async def get_all_customers(self):
            return []
        async def get_orders_last_n_days(self, days):
            return [{"customer_id": "qb-a", "date": date.today(), "total": invalid}]
        async def get_inventory_merged(self):
            return []
    with pytest.raises(SnapshotSourceUnavailable) as error:
        await compute_dashboard_payload(Synthetic())
    assert error.value.source == "account_health"


@pytest.mark.asyncio
async def test_snapshot_rejects_invalid_30_day_invoice_even_when_year_is_valid():
    class Synthetic:
        async def get_all_customers(self):
            return []
        async def get_orders_last_n_days(self, days):
            if days == 365:
                return []
            return [{"customer_id": "qb-a", "date": date.today(), "total": "NaN"}]
        async def get_inventory_merged(self):
            return []
    with pytest.raises(SnapshotSourceUnavailable) as error:
        await compute_dashboard_payload(Synthetic())
    assert error.value.source == "sales_30d"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "1e9999", True])
async def test_sales_api_invalid_current_amount_returns_503(invalid):
    adapter = AsyncMock()
    adapter.get_orders_last_n_days.return_value = [
        {"customer_id": "qb-a", "customer_name": "Example",
         "date": date.today(), "total": invalid},
    ]
    with pytest.raises(HTTPException) as error:
        await get_sales_data(
            days=30, user={"company_id": "synthetic-tenant", "role": "owner"},
            adapter=adapter,
        )
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_sales_api_aggregate_overflow_returns_503_not_infinite_revenue():
    adapter = AsyncMock()
    adapter.get_orders_last_n_days.return_value = [
        {"customer_id": "qb-a", "customer_name": "Example",
         "date": date.today(), "total": "8e307"},
    ] * 3
    with pytest.raises(HTTPException) as error:
        await get_sales_data(
            days=30, user={"company_id": "synthetic-tenant", "role": "owner"},
            adapter=adapter,
        )
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_sales_api_invalid_comparison_period_returns_503():
    adapter = AsyncMock()
    async def read(days):
        if days == 30:
            return [{"customer_name": "Synthetic", "date": date.today(), "total": 10}]
        return [{"customer_name": "Synthetic", "date": date(2026, 1, 1), "total": "Infinity"}]
    adapter.get_orders_last_n_days.side_effect = read
    with pytest.raises(HTTPException) as error:
        await get_sales_data(
            days=30, user={"company_id": "synthetic-tenant", "role": "owner"},
            adapter=adapter,
        )
    assert error.value.status_code == 503
