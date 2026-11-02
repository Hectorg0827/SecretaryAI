"""Purchasing ledger reads behind the supply chain insights."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.insights import _supply_chain_sources as sources
from app.api.insights._supply_chain_sources import read_purchasing_history

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


class Query:
    """Applies the filters, ordering and limit the reader relies on."""

    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_):
        return self

    def eq(self, field, value):
        self.rows = [r for r in self.rows if r.get(field) == value]
        return self

    def in_(self, field, values):
        values = set(values)
        self.rows = [r for r in self.rows if r.get(field) in values]
        return self

    def order(self, field, desc=False):
        self.rows.sort(key=lambda r: r[field], reverse=desc)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class DB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return Query(self.tables[name])


def _order(po_id, status, approved_days_ago, created_days_ago=None):
    return {"id": po_id, "company_id": "c1", "status": status,
            "approved_at": (NOW - timedelta(days=approved_days_ago)).isoformat(),
            "created_at": (NOW - timedelta(days=created_days_ago or approved_days_ago)).isoformat()}


@pytest.mark.asyncio
async def test_split_delivery_gives_each_sku_its_own_lead_time():
    approved = NOW - timedelta(days=40)
    db = DB(
        purchasing_orders=[_order("po-1", "received", 40)],
        purchasing_order_lines=[
            {"company_id": "c1", "po_id": "po-1", "sku": "A", "qty_ordered": 5, "qty_received": 5},
            {"company_id": "c1", "po_id": "po-1", "sku": "B", "qty_ordered": 5, "qty_received": 5},
        ],
        warehouse_receipt_documents=[
            {"company_id": "c1", "po_id": "po-1", "posted_at": (approved + timedelta(days=2)).isoformat(),
             "payload": [{"sku": "A", "accepted_qty": 5}]},
            {"company_id": "c1", "po_id": "po-1", "posted_at": (approved + timedelta(days=30)).isoformat(),
             "payload": [{"sku": "B", "accepted_qty": 5}]},
        ],
    )
    history = await read_purchasing_history(db, "c1", now=NOW)
    assert history.lead_times_by_sku == {"a": [pytest.approx(2)], "b": [pytest.approx(30)]}
    # The supplier's lead time is still time to the first delivery.
    assert history.supplier_lead_times == [pytest.approx(2)]


@pytest.mark.asyncio
async def test_old_outstanding_order_beyond_the_history_window_still_counts(monkeypatch):
    monkeypatch.setattr(sources, "MAX_LEDGER_ORDERS", 2)
    orders = [_order("po-old", "approved", 300)]
    orders += [_order(f"po-{n}", "received", 5 + n) for n in range(3)]
    db = DB(
        purchasing_orders=orders,
        purchasing_order_lines=[
            {"company_id": "c1", "po_id": "po-old", "sku": "A", "qty_ordered": 50, "qty_received": 10},
        ],
        warehouse_receipt_documents=[],
    )
    history = await read_purchasing_history(db, "c1", now=NOW)
    assert history.open_units_by_sku == {"a": 40}


@pytest.mark.asyncio
async def test_too_many_outstanding_orders_is_an_outage_not_a_partial_answer(monkeypatch):
    monkeypatch.setattr(sources, "MAX_OPEN_ORDERS", 2)
    db = DB(
        purchasing_orders=[_order(f"po-{n}", "approved", 10 + n) for n in range(3)],
        purchasing_order_lines=[],
        warehouse_receipt_documents=[],
    )
    with pytest.raises(ValueError):
        await read_purchasing_history(db, "c1", now=NOW)


@pytest.mark.asyncio
async def test_order_ids_are_queried_in_small_chunks(monkeypatch):
    monkeypatch.setattr(sources, "_IN_CHUNK", 2)
    seen = []

    class CountingDB(DB):
        def table(self, name):
            query = super().table(name)
            original = query.in_

            def in_(field, values):
                if field == "po_id":
                    seen.append(len(values))
                return original(field, values)

            query.in_ = in_
            return query

    db = CountingDB(
        purchasing_orders=[_order(f"po-{n}", "received", 10 + n) for n in range(5)],
        purchasing_order_lines=[], warehouse_receipt_documents=[],
    )
    await read_purchasing_history(db, "c1", now=NOW)
    assert seen and max(seen) <= 2
