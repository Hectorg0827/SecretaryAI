"""Supply chain health insight: measured SCOR metrics, and the full check on entered figures."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_adapter, get_db
from app.api.insights import supply_chain_health
from app.auth.rbac import get_current_user
from app.intelligence.wholesale_distribution.scor_toc import (
    ConstraintType, ProcessNode, SCORDomain, SCORToCEngine,
)

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
    orders, lines, receipts = [], [], []
    for n, days in enumerate((10, 14)):
        approved = NOW - timedelta(days=40 + n)
        orders.append({"id": f"po-{n}", "status": "received", "approved_at": approved.isoformat()})
        lines.append({"po_id": f"po-{n}", "sku": "MALT-1", "qty_ordered": 10, "qty_received": 10})
        receipts.append({"po_id": f"po-{n}", "posted_at": (approved + timedelta(days=days)).isoformat(),
                         "payload": [{"sku": "MALT-1", "accepted_qty": 1}]})
    return FakeDB({
        "purchasing_orders": orders,
        "purchasing_order_lines": lines,
        "warehouse_receipt_documents": receipts,
    })


def make_adapter(line_item_id="QB-1"):
    inventory = [
        {"qb_id": "QB-1", "product_name": "Malta", "sku": "MALT-1", "total_qty": 100, "purchase_cost": 6.0},
        {"qb_id": "QB-2", "product_name": "Ginger Beer", "sku": "GB-1", "total_qty": 50, "purchase_cost": 4.0},
        {"qb_id": "QB-3", "product_name": "Coconut Water", "sku": "CW-1", "total_qty": 10, "purchase_cost": 0},
    ]
    invoices = [
        {"date": TODAY - timedelta(days=30 * n), "status": "paid", "total": Decimal("1000"),
         "line_items": [{"item_id": line_item_id, "name": "", "quantity": 50, "amount": 1000}]}
        for n in range(10)
    ]
    customers = [SimpleNamespace(balance=Decimal("1500")), SimpleNamespace(balance=Decimal("500"))]
    return SimpleNamespace(
        get_inventory_merged=AsyncMock(return_value=inventory),
        get_orders_last_n_days=AsyncMock(return_value=invoices),
        get_all_customers=AsyncMock(return_value=customers),
    )


def client(role="owner", adapter=None, db=None):
    app = FastAPI()
    app.include_router(supply_chain_health.router, prefix="/api/insights" + supply_chain_health.PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "tenant-a", "sub": "user-a", "role": role,
    }
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_measured_metrics_come_from_connected_data_and_unknowns_are_not_zero():
    adapter, db = make_adapter(), ledger()
    response = client(adapter=adapter, db=db).get("/api/insights/supply-chain-health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["actions_executed"] is False
    assert body["company_id"] == "tenant-a"
    adapter.get_orders_last_n_days.assert_awaited_once_with(365)

    # Inventory 100*6 + 50*4 = 800; COGS 10 invoices * 50 * 6 = 3000; AR 2000; revenue 10000.
    assert body["measured"] == {
        "days_inventory_outstanding": 97.3,
        "days_sales_outstanding": 73.0,
        "supplier_lead_time_days": 12.0,
        "supplier_lead_time_observations": 2,
    }
    assert body["prefill"] == {
        "revenue": 10000.0, "cogs": 3000.0, "inventory_value": 800.0, "ar_balance": 2000.0,
        "supplier_lead_times_days": [10.0, 14.0],
    }
    assert body["coverage"]["cogs_revenue_coverage_pct"] == 100.0
    assert body["coverage"]["inventory_items_missing_cost"] == 1
    needs = {n["metric"] for n in body["needs_input"]}
    assert {"days_payable_outstanding", "supply_chain_cost_pct_revenue", "primary_constraint"} <= needs
    assert "cash_to_cash_cycle_days" not in body["measured"]
    for query in db.queries:
        assert ("company_id", "tenant-a") in query.filters


def test_unmatched_sales_cost_hides_inventory_days_instead_of_guessing():
    response = client(adapter=make_adapter(line_item_id="UNKNOWN"), db=ledger()).get(
        "/api/insights/supply-chain-health"
    )
    body = response.json()
    assert body["coverage"]["cogs_revenue_coverage_pct"] == 0.0
    assert body["measured"]["days_inventory_outstanding"] is None
    assert body["prefill"]["cogs"] is None
    assert body["measured"]["days_sales_outstanding"] == 73.0


@pytest.mark.parametrize("broken", ["get_inventory_merged", "get_orders_last_n_days", "get_all_customers", "ledger"])
def test_source_failure_is_503(broken):
    adapter, db = make_adapter(), ledger()
    if broken == "ledger":
        db.tables["purchasing_order_lines"] = ConnectionError("db down")
    else:
        getattr(adapter, broken).side_effect = ConnectionError("down")
    assert client(adapter=adapter, db=db).get("/api/insights/supply-chain-health").status_code == 503


@pytest.mark.parametrize("role", ["back_office", "sales_rep", "viewer"])
def test_roles_without_financial_access_are_refused(role):
    adapter = make_adapter()
    api = client(role=role, adapter=adapter, db=ledger())
    assert api.get("/api/insights/supply-chain-health").status_code == 403
    assert api.post("/api/insights/supply-chain-health/preview", json=preview_body()).status_code == 403
    adapter.get_inventory_merged.assert_not_awaited()


def preview_body(**overrides):
    body = {
        "revenue": 1_000_000, "cogs": 700_000, "inventory_value": 100_000,
        "ar_balance": 80_000, "ap_balance": 60_000, "supply_chain_costs": 90_000,
        "supplier_lead_times_days": [10, 12, 14],
        "process_steps": [
            {"name": "Warehouse picking", "domain": "deliver", "throughput_per_day": 200,
             "backlog": 40, "utilization_pct": 95, "avg_cycle_time_hours": 2},
            {"name": "Staging area", "domain": "deliver", "throughput_per_day": 300,
             "backlog": 10, "utilization_pct": 88, "avg_cycle_time_hours": 1},
            {"name": "Order entry", "domain": "plan", "throughput_per_day": 80,
             "backlog": 0, "utilization_pct": 99, "avg_cycle_time_hours": 0.5},
        ],
    }
    body.update(overrides)
    return body


def test_full_check_ranks_bottlenecks_and_benchmarks_entered_figures():
    response = client(role="manager").post("/api/insights/supply-chain-health/preview", json=preview_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "user_supplied"
    assert body["requires_human_review"] is True
    assert body["company_id"] == "tenant-a"
    assets = body["scor_metrics"]["assets"]
    assert assets["days_inventory_outstanding"] == 52.1
    assert assets["days_sales_outstanding"] == 29.2
    assert assets["days_payable_outstanding"] == 31.3
    assert assets["cash_to_cash_cycle_days"] == 50.1
    assert body["scor_metrics"]["cost"]["supply_chain_cost_pct_revenue"] == 9.0
    assert body["benchmarks"]["cash_to_cash"] == "fair"
    assert body["benchmarks"]["supply_chain_cost"] == "good"
    assert body["benchmarks"]["perfect_order_fulfillment"] == "insufficient_data"
    # A step with no backlog is not a constraint, however busy it is.
    names = [c["node_name"] for c in body["all_constraints"]]
    assert names == ["Warehouse picking", "Staging area"]
    assert body["primary_constraint"]["constraint_type"] == "warehouse_throughput"
    assert body["primary_constraint"]["severity"] == "high"
    assert body["all_constraints"][1]["constraint_type"] == "none_identified"


@pytest.mark.parametrize("overrides", [
    {"revenue": -1},
    {"process_steps": [{"name": "Picking", "throughput_per_day": 1, "backlog": 1,
                        "utilization_pct": 120, "avg_cycle_time_hours": 1}]},
    {"process_steps": [{"name": "Picking", "throughput_per_day": 0, "backlog": 1,
                        "utilization_pct": 90, "avg_cycle_time_hours": 1}]},
    {"supplier_lead_times_days": [0]},
    {"company_id": "tenant-b"},
])
def test_full_check_rejects_invalid_input(overrides):
    response = client().post("/api/insights/supply-chain-health/preview", json=preview_body(**overrides))
    assert response.status_code == 422


def test_missing_figure_is_422():
    body = preview_body()
    del body["ap_balance"]
    assert client().post("/api/insights/supply-chain-health/preview", json=body).status_code == 422


@pytest.mark.parametrize("name,expected", [
    ("Staging area", ConstraintType.NONE_IDENTIFIED),
    ("Quarantine", ConstraintType.NONE_IDENTIFIED),
    ("AR collections", ConstraintType.CASH_FLOW),
    ("Customer payment follow-up", ConstraintType.CASH_FLOW),
])
def test_constraint_type_matches_whole_words_not_fragments(name, expected):
    node = ProcessNode("n1", name, SCORDomain.DELIVER, 100, 10, 92, 1)
    assert SCORToCEngine("co").identify_constraints([node])[0].constraint_type == expected
