"""Gate A regression: incomplete data must not masquerade as an all-clear dashboard."""

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
    assert result["source_quality"] == "partial"
    assert result["accounts"]["total"] == 0
    assert result["inventory_alerts"] == []
    assert result["sales_30d"] == 0
    assert result["source_read_status"] == "completed"
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



@pytest.mark.asyncio
async def test_zero_total_invoice_dataclass_is_valid_not_a_mapping_error():
    from datetime import date
    from decimal import Decimal
    from app.connectors.base import Customer, Invoice

    adapter = CompleteReader()
    adapter.customers = [
        Customer(
            id="cust1", qb_id="QB-C", name="Test", email=None,
            phone=None, state=None, balance=Decimal("0"),
            total_sales=Decimal("0"),
        )
    ]
    adapter.invoices = [
        Invoice(
            id="inv1", qb_id="QB-INV", customer_id="QB-C",
            customer_name="Test", date=date.today(), due_date=None,
            total=Decimal("0"), balance=Decimal("0"), status="open",
            line_items=[],
        )
    ]
    result = await compute_dashboard_payload(adapter)
    assert result["source_quality"] == "partial"
    assert result["accounts"]["total"] == 1
    assert result["sales_30d"] == 0


@pytest.mark.asyncio
async def test_malformed_invoice_missing_amount_cannot_be_treated_as_zero():
    from datetime import date
    from decimal import Decimal
    from app.connectors.base import Customer

    adapter = CompleteReader()
    adapter.customers = [
        Customer(
            id="cust1", qb_id="QB-C", name="Test", email=None,
            phone=None, state=None, balance=Decimal("0"),
            total_sales=Decimal("0"),
        )
    ]
    adapter.invoices = [
        {"customer_id": "QB-C", "date": date.today(), "balance": 0}
    ]
    with pytest.raises(SnapshotSourceUnavailable) as info:
        await compute_dashboard_payload(adapter)
    assert info.value.source == "account_health"



@pytest.mark.asyncio
async def test_inventory_uses_authoritative_quantity_not_qb_plus_warehouse():
    adapter = CompleteReader()
    # Two systems describe the same five physical units, not 105 units.
    # At ten units/week the item is critical (0.5 weeks available).
    adapter.inventory = [{
        "qb_id": "SKU-1", "product_name": "Critical SKU",
        "qb_qty": 5, "warehouse_qty": 100, "total_qty": 5,
        "weekly_sell_rate": 10, "weekly_sell_rate_observed": True, "quantity_source": "quickbooks",
    }]
    result = await compute_dashboard_payload(adapter)
    assert result["inventory_alerts"][0]["stock_status"] == "critical"
    assert result["inventory_alerts"][0]["total_qty"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_qty", [None, -5, "nan", "inf"])
async def test_inventory_without_valid_authoritative_qty_is_not_false_all_clear(bad_qty):
    adapter = CompleteReader()
    adapter.inventory = [{
        "qb_id": "SKU-BAD", "product_name": "Untrusted stock",
        "qb_qty": 100, "warehouse_qty": 100,
        "total_qty": bad_qty, "weekly_sell_rate": 10,
        "weekly_sell_rate_observed": True,
    }]
    with pytest.raises(SnapshotSourceUnavailable) as info:
        await compute_dashboard_payload(adapter)
    assert info.value.source == "inventory"


@pytest.mark.asyncio
async def test_inventory_missing_demand_history_is_not_silently_assumed_zero():
    adapter = CompleteReader()
    adapter.inventory = [{
        "qb_id": "SKU-NO-RATE", "product_name": "Unknown velocity",
        "qb_qty": 7, "total_qty": 7,
    }]
    with pytest.raises(SnapshotSourceUnavailable) as info:
        await compute_dashboard_payload(adapter)
    assert info.value.source == "inventory"



def test_cached_dashboard_freshness_is_explicit_and_timezone_aware():
    from datetime import datetime, timedelta, timezone
    from app.scheduler.dashboard_snapshot import snapshot_freshness

    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=3)).isoformat()
    old = (now - timedelta(hours=49)).isoformat()
    assert snapshot_freshness(recent, now=now)["status"] == "fresh"
    assert snapshot_freshness(old, now=now)["status"] == "stale"
    assert snapshot_freshness(old, now=now)["stale"] is True
    assert snapshot_freshness(None, now=now)["status"] == "unknown"
    assert snapshot_freshness("2026-10-26T09:00:00", now=now)["status"] == "unknown"
    assert snapshot_freshness("bogus", now=now)["stale"] is True


def test_future_dated_cache_does_not_claim_freshness():
    from datetime import datetime, timedelta, timezone
    from app.scheduler.dashboard_snapshot import snapshot_freshness

    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    bad = (now + timedelta(hours=2)).isoformat()
    result = snapshot_freshness(bad, now=now)
    assert result["status"] == "unknown"
    assert result["stale"] is True



@pytest.mark.asyncio
@pytest.mark.parametrize("bad_date", [None, "unparseable", "2026-13-48"])
async def test_invalid_transaction_date_cannot_trigger_false_dormancy(bad_date):
    from decimal import Decimal
    from app.connectors.base import Customer
    adapter = CompleteReader()
    adapter.customers = [
        Customer(
            id="customer-1", qb_id="C1", name="Active Account",
            email=None, phone=None, state="NY", balance=Decimal("0"),
            total_sales=Decimal("100"),
        )
    ]
    adapter.invoices = [{
        "customer_id": "C1", "date": bad_date, "total": 100,
    }]
    with pytest.raises(SnapshotSourceUnavailable) as info:
        await compute_dashboard_payload(adapter)
    assert info.value.source == "account_health"



@pytest.mark.asyncio
async def test_fractional_inventory_is_not_truncated_and_payload_is_json_safe():
    import json
    adapter = CompleteReader()
    adapter.inventory = [{
        "qb_id": "SKU-WEIGHT", "product_name": "Bulk item",
        "warehouse_qty": 10.5, "qb_qty": 10.5,
        "total_qty": 10.5, "weekly_sell_rate": 7.0,
        "weekly_sell_rate_observed": True,
    }]
    result = await compute_dashboard_payload(adapter)
    alert = result["inventory_alerts"][0]
    assert alert["total_qty"] == 10.5
    assert alert["weeks_remaining"] == pytest.approx(1.5)
    json.dumps(result)  # Supabase JSON persistence must not see Decimal values



@pytest.mark.asyncio
async def test_unobserved_zero_sell_rate_is_not_an_actual_zero_demand_measurement():
    adapter = CompleteReader()
    adapter.inventory = [{
        "qb_id": "SKU-NO-SYNC", "product_name": "Unmeasured",
        "qb_qty": 100, "total_qty": 100,
        "weekly_sell_rate": 0.0,
        "weekly_sell_rate_observed": False,
        "weekly_sell_rate_source_status": "missing",
    }]
    with pytest.raises(SnapshotSourceUnavailable) as error:
        await compute_dashboard_payload(adapter)
    assert error.value.source == "inventory"


@pytest.mark.asyncio
async def test_genuine_measured_zero_sell_rate_is_distinct_from_unknown():
    adapter = CompleteReader()
    adapter.inventory = [{
        "qb_id": "SKU-VERIFIED-ZERO", "product_name": "No recent sales",
        "qb_qty": 100, "total_qty": 100,
        "weekly_sell_rate": 0.0,
        "weekly_sell_rate_observed": True,
        "weekly_sell_rate_source_status": "observed",
    }]
    payload = await compute_dashboard_payload(adapter)
    assert payload["source_read_status"] == "completed"
    assert payload["inventory_alerts"] == []
    assert payload["source_quality"] == "partial"


@pytest.mark.asyncio
async def test_missing_observation_metadata_does_not_count_as_verified_demand():
    adapter = CompleteReader()
    adapter.inventory = [{
        "qb_id": "SKU-LEGACY", "product_name": "Legacy item",
        "qb_qty": 100, "total_qty": 100,
        "weekly_sell_rate": 0.0,
    }]
    with pytest.raises(SnapshotSourceUnavailable) as error:
        await compute_dashboard_payload(adapter)
    assert error.value.source == "inventory"
