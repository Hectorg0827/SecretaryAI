"""Cause analysis insight: ranked likely causes for quiet customers and stock risks."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_adapter, get_db
from app.api.insights import cause_analysis
from app.auth.rbac import get_current_user

TODAY = date.today()
NOW = datetime.now(timezone.utc)


class FakeQuery:
    def __init__(self, name, data, log):
        self.name, self.data, self.filters = name, data, []
        log.append(self)

    def select(self, *_):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def in_(self, field, values):
        self.filters.append((field, tuple(values)))
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        if isinstance(self.data, Exception):
            raise self.data
        return SimpleNamespace(data=self.data)


class FakeDB:
    def __init__(self, tables):
        self.tables, self.queries = tables, []

    def table(self, name):
        return FakeQuery(name, self.tables[name], self.queries)


def ledger():
    """MALT-1 usually arrives in 10 days; an order approved 20 days ago is still open."""
    orders, lines, receipts = [], [], []
    for n in range(2):
        approved = NOW - timedelta(days=60 + n)
        orders.append({"id": f"po-{n}", "status": "received", "approved_at": approved.isoformat()})
        lines.append({"po_id": f"po-{n}", "sku": "MALT-1", "qty_ordered": 100, "qty_received": 100})
        receipts.append({"po_id": f"po-{n}", "posted_at": (approved + timedelta(days=10)).isoformat(),
                         "payload": [{"sku": "MALT-1", "accepted_qty": 1}]})
    orders.append({"id": "po-late", "status": "approved",
                   "approved_at": (NOW - timedelta(days=20)).isoformat()})
    lines.append({"po_id": "po-late", "sku": "MALT-1", "qty_ordered": 100, "qty_received": 0})
    return FakeDB({
        "purchasing_orders": orders,
        "purchasing_order_lines": lines,
        "warehouse_receipt_documents": receipts,
    })


def invoice(customer_id, name, age, lines, status="open"):
    total = sum(line["amount"] for line in lines)
    return {"customer_id": customer_id, "customer_name": name, "date": TODAY - timedelta(days=age),
            "total": total, "status": status, "line_items": lines}


def sale(item_id, name, qty, price):
    return {"item_id": item_id, "name": name, "quantity": qty, "unit_price": price, "amount": qty * price}


def invoices():
    rows = []
    # Bodega Uno ordered every 14 days, went quiet 60 days ago, after a 10% price rise.
    for age in range(284, 59, -14):
        price = 10.0 if age > 170 else 11.0
        rows.append(invoice("C1", "Bodega Uno", age, [sale("QB-2", "Ginger Beer", 20, price)]))
    # Steady Mart orders weekly and is fine; it also drives Malta sales.
    for age in range(3, 365, 7):
        rows.append(invoice("C2", "Steady Mart", age, [sale("QB-1", "Malta", 35, 10.0)]))
    # New Shop has too little history to judge.
    rows += [invoice("C3", "New Shop", age, [sale("QB-3", "Coconut Water", 1, 5.0)]) for age in (40, 200)]
    # Shrinking Co still orders weekly but spends about two thirds less this quarter.
    for age in range(5, 180, 7):
        qty = 30 if age <= 90 else 100
        rows.append(invoice("C4", "Shrinking Co", age, [sale("QB-3", "Coconut Water", qty, 10.0)]))
    rows.append(invoice("C1", "Bodega Uno", 1, [sale("QB-2", "Ginger Beer", 999, 1.0)], status="void"))
    rows.append({"customer_id": "C9", "date": "not-a-date", "total": 5, "line_items": []})
    return rows


def inventory():
    return [
        {"qb_id": "QB-1", "product_name": "Malta", "sku": "MALT-1", "total_qty": 20},
        {"qb_id": "QB-2", "product_name": "Ginger Beer", "sku": "GB-1", "total_qty": 1000},
        {"qb_id": "QB-3", "product_name": "Coconut Water", "sku": "CW-1", "total_qty": 5000},
    ]


def make_adapter():
    return SimpleNamespace(
        get_inventory_merged=AsyncMock(return_value=inventory()),
        get_orders_last_n_days=AsyncMock(return_value=invoices()),
    )


def client(role="owner", adapter=None, db=None):
    app = FastAPI()
    app.include_router(cause_analysis.router, prefix="/api/insights" + cause_analysis.PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "tenant-a", "sub": "user-a", "role": role,
    }
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_ranked_causes_for_quiet_customers_and_stock_risks():
    adapter, db = make_adapter(), ledger()
    response = client(adapter=adapter, db=db).get("/api/insights/cause-analysis")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False
    assert body["company_id"] == "tenant-a"
    adapter.get_orders_last_n_days.assert_awaited_once_with(365)

    drops = {c["customer_name"]: c for c in body["customer_drops"]}
    assert set(drops) == {"Bodega Uno", "Shrinking Co"}
    bodega = drops["Bodega Uno"]
    assert bodega["days_since_last_order"] == 60
    assert bodega["usual_days_between_orders"] == 14.0
    assert bodega["analysis"]["primary_cause"]["root_cause"] == "pricing_pressure"
    assert any("10.0%" in e for e in bodega["analysis"]["primary_cause"]["evidence"])
    assert bodega["analysis"]["customer_segment"] == "persuadable"
    shrinking = drops["Shrinking Co"]
    assert shrinking["trigger"] == "Spent 68% less in the last 90 days than the 90 days before"
    assert shrinking["analysis"]["primary_cause"]["root_cause"] == "demand_shift"

    assert [s["product_name"] for s in body["stock_risks"]] == ["Malta"]
    malta = body["stock_risks"][0]
    assert malta["avg_daily_sales"] == 5.0
    assert malta["days_of_stock_left"] == 4.0
    assert malta["typical_supplier_days"] == 10.0
    assert malta["late_purchase_orders"] == 1
    assert malta["analysis"]["primary_cause"]["root_cause"] == "supply_disruption"

    assert body["summary"]["customers_reviewed"] == 4
    assert body["summary"]["customers_flagged"] == 2
    assert body["summary"]["customers_with_too_little_history"] == 1
    assert body["summary"]["rows_skipped"] == 1
    assert body["evidence_gaps"]
    for query in db.queries:
        assert ("company_id", "tenant-a") in query.filters


@pytest.mark.parametrize("broken", ["inventory", "invoices", "ledger"])
def test_source_failure_is_503(broken):
    adapter, db = make_adapter(), ledger()
    if broken == "inventory":
        adapter.get_inventory_merged.side_effect = ConnectionError("down")
    elif broken == "invoices":
        adapter.get_orders_last_n_days.side_effect = ConnectionError("down")
    else:
        db.tables["warehouse_receipt_documents"] = ConnectionError("db down")
    assert client(adapter=adapter, db=db).get("/api/insights/cause-analysis").status_code == 503


def test_malformed_invoice_payload_is_503():
    adapter = make_adapter()
    adapter.get_orders_last_n_days.return_value = {"unexpected": "shape"}
    assert client(adapter=adapter, db=ledger()).get("/api/insights/cause-analysis").status_code == 503


@pytest.mark.parametrize("role", ["sales_rep", "viewer"])
def test_roles_without_customer_access_are_refused(role):
    adapter = make_adapter()
    response = client(role=role, adapter=adapter, db=ledger()).get("/api/insights/cause-analysis")
    assert response.status_code == 403
    adapter.get_orders_last_n_days.assert_not_awaited()


def test_back_office_may_read():
    response = client(role="back_office", adapter=make_adapter(), db=ledger()).get("/api/insights/cause-analysis")
    assert response.status_code == 200
