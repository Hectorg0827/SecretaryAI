"""Gate A vertical slice: normalized synthetic reads -> tenant cache -> typed briefing.

No real business records, QuickBooks, email, Supabase, external HTTP,
approval queue, supplier communications or financial side effects are used.
This test certifies backend *contract compatibility only*, not production
connectivity, delivery or an end-to-end deployed customer experience.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.briefing import BriefingSourceUnavailable, build_owner_briefing
from app.connectors.base import Customer, Invoice
from app.domain.briefing_wire import BriefingTodayResponseV1
from app.scheduler.dashboard_snapshot import (
    SnapshotSourceUnavailable, compute_dashboard_payload,
    read_snapshot, store_snapshot,
)


class SyntheticUnifiedAdapter:
    """Chat 2 adapter's documented normalized read contract, without IO."""
    def __init__(self, *, velocity_observed: bool = True):
        self.calls = []
        self.velocity_observed = velocity_observed

    async def get_all_customers(self):
        self.calls.append("customers")
        return [
            Customer(
                id="customer-a", qb_id="QB-A", name="Active Fixture",
                email=None, phone=None, state="NY",
                balance=Decimal("0"), total_sales=Decimal("600"),
            ),
            Customer(
                id="customer-b", qb_id="QB-B", name="Dormant Fixture",
                email=None, phone=None, state="NJ",
                balance=Decimal("0"), total_sales=Decimal("0"),
            ),
        ]

    async def get_orders_last_n_days(self, days):
        self.calls.append(f"orders-{days}")
        return [
            Invoice(
                id="invoice-1", qb_id="QB-INV1", customer_id="QB-A",
                customer_name="Active Fixture", date=date.today(),
                due_date=None, total=Decimal("600"), balance=Decimal("0"),
                status="paid", line_items=[],
            ),
        ]

    async def get_inventory_merged(self):
        self.calls.append("inventory")
        return [{
            "qb_id": "SKU-A", "product_name": "Synthetic Product",
            # Two provider views of the same four units must never be summed.
            "qb_qty": 4, "warehouse_qty": 44, "total_qty": 4,
            "quantity_source": "quickbooks",
            "weekly_sell_rate": 8.0,
            "weekly_sell_rate_observed": self.velocity_observed,
            "weekly_sell_rate_source_status": (
                "observed" if self.velocity_observed else "missing"
            ),
        }]


class TenantSnapshotStore:
    """Tiny in-memory stand-in for tenant-filtered report_snapshots upserts."""
    def __init__(self):
        self.rows = {}
        self._operation = None
        self._filters = {}
        self._pending = None

    def table(self, name):
        assert name == "report_snapshots"
        self._operation = None
        self._filters = {}
        self._pending = None
        return self

    def upsert(self, record, on_conflict):
        assert on_conflict == "company_id,report_type"
        self._operation = "write"
        self._pending = dict(record)
        return self

    def select(self, fields):
        assert fields == "payload,generated_at,generated_by"
        self._operation = "read"
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self._operation == "write":
            row = self._pending
            self.rows[(row["company_id"], row["report_type"])] = row
            return SimpleNamespace(data=[row])
        if self._operation == "read":
            company_id = self._filters.get("company_id")
            report_type = self._filters.get("report_type")
            row = self.rows.get((company_id, report_type))
            if row is None:
                return SimpleNamespace(data=None)
            return SimpleNamespace(data={
                "payload": row["payload"],
                "generated_at": row["generated_at"],
                "generated_by": row["generated_by"],
            })
        raise AssertionError("Unexpected query")


@pytest.mark.asyncio
async def test_synthetic_data_reaches_authorized_six_section_briefing_through_cache():
    adapter = SyntheticUnifiedAdapter()
    db = TenantSnapshotStore()

    normalized = await compute_dashboard_payload(adapter)
    assert adapter.calls == ["customers", "orders-365", "inventory", "orders-30"]
    assert normalized["source_read_status"] == "completed"
    assert normalized["source_quality"] == "partial"
    assert normalized["accounts"]["total"] == 2
    assert normalized["sales_30d"] == 600
    assert len(normalized["inventory_alerts"]) == 1
    assert normalized["inventory_alerts"][0]["total_qty"] == 4
    assert normalized["inventory_alerts"][0]["stock_status"] == "critical"

    store_snapshot(
        db, "tenant-A", "dashboard_summary", normalized,
        generated_by="synthetic_contract_fixture",
    )
    snapshot = read_snapshot(db, "tenant-A", "dashboard_summary")
    assert snapshot["generated_by"] == "synthetic_contract_fixture"

    response = build_owner_briefing(
        company_id="tenant-A", role="owner", snapshot=snapshot,
        now=datetime.now(timezone.utc),
    )
    typed = BriefingTodayResponseV1.model_validate(response)
    assert typed.contract_version == "1.0"
    assert typed.company_id == "tenant-A"
    assert typed.complete_digest is False
    assert typed.source_quality == "partial"
    assert len(typed.sections.red_alerts) == 1
    assert len(typed.sections.scoreboard) == 1
    assert typed.sections.actions_taken == []
    assert typed.sections.opportunities == []
    assert "critical inventory" in typed.sections.red_alerts[0].title
    assert typed.sections.red_alerts[0].evidence[0].quality == "partial"

    # Record-level and tenant access are server-side responsibilities:
    # a different company must not see the cached first company's figures.
    assert read_snapshot(db, "tenant-B", "dashboard_summary") is None
    with pytest.raises(BriefingSourceUnavailable):
        build_owner_briefing(
            company_id="tenant-B", role="owner",
            snapshot=read_snapshot(db, "tenant-B", "dashboard_summary"),
            now=datetime.now(timezone.utc),
        )
    with pytest.raises(PermissionError):
        build_owner_briefing(
            company_id="tenant-A", role="sales_rep", snapshot=snapshot,
            now=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_unknown_velocity_cannot_publish_cache_or_business_briefing():
    adapter = SyntheticUnifiedAdapter(velocity_observed=False)
    db = TenantSnapshotStore()
    with pytest.raises(SnapshotSourceUnavailable) as error:
        normalized = await compute_dashboard_payload(adapter)
        store_snapshot(db, "tenant-A", "dashboard_summary", normalized)
    assert error.value.source == "inventory"
    assert db.rows == {}
    assert read_snapshot(db, "tenant-A", "dashboard_summary") is None
