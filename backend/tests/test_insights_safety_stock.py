"""Safety stock insight: connected demand + purchasing ledger, and the what-if form."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_adapter, get_db
from app.api.insights import safety_stock
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


def ledger(lead_days=(10, 12, 14, 12, 12), open_qty=100):
    """Five received MALT-1 purchase orders plus one approved, unreceived order."""
    orders, lines, receipts = [], [], []
    for n, days in enumerate(lead_days):
        approved = NOW - timedelta(days=days + 20 + n)
        orders.append({"id": f"po-{n}", "status": "received", "approved_at": approved.isoformat()})
        lines.append({"po_id": f"po-{n}", "sku": "MALT-1", "qty_ordered": 200, "qty_received": 200})
        receipts.append({"po_id": f"po-{n}", "posted_at": (approved + timedelta(days=days)).isoformat(),
                         "payload": [{"sku": "MALT-1", "accepted_qty": 1}]})
    orders.append({"id": "po-open", "status": "approved",
                   "approved_at": (NOW - timedelta(days=3)).isoformat()})
    lines.append({"po_id": "po-open", "sku": "malt-1", "qty_ordered": open_qty, "qty_received": 0})
    lines.append({"po_id": "po-open", "sku": "BAD", "qty_ordered": "n/a", "qty_received": 0})
    return FakeDB({
        "purchasing_orders": orders,
        "purchasing_order_lines": lines,
        "warehouse_receipt_documents": receipts,
    })


def inventory():
    return [
        {"qb_id": "QB-1", "product_name": "Malta", "sku": "MALT-1", "total_qty": 40,
         "purchase_cost": 6.0, "unit_price": 10.0},
        {"qb_id": "QB-2", "product_name": "Ginger Beer", "sku": "GB-1", "total_qty": 500,
         "purchase_cost": 4.0, "unit_price": 8.0},
        {"qb_id": "QB-3", "product_name": "Coconut Water", "sku": "CW-1", "total_qty": 100,
         "purchase_cost": 3.0, "unit_price": 6.0},
    ]


def invoices():
    rows = []
    for age in range(90):
        lines = [{"item_id": "QB-1", "name": "Malta", "quantity": 8 if age % 2 == 0 else 12}]
        if age % 3 == 0:
            lines.append({"item_id": "", "name": "Ginger Beer", "quantity": 5})
        rows.append({"date": TODAY - timedelta(days=age), "status": "open", "total": 100, "line_items": lines})
    rows.append({"date": TODAY, "status": "void", "total": 1,
                 "line_items": [{"item_id": "QB-1", "quantity": 9999}]})
    rows.append({"date": None, "status": "open", "total": 1, "line_items": []})
    rows.append({"date": TODAY, "status": "open", "total": 1,
                 "line_items": [{"item_id": "QB-1", "quantity": "lots"}]})
    return rows


def make_adapter():
    return SimpleNamespace(
        get_inventory_merged=AsyncMock(return_value=inventory()),
        get_orders_last_n_days=AsyncMock(return_value=invoices()),
    )


def client(role="owner", adapter=None, db=None):
    app = FastAPI()
    app.include_router(safety_stock.router, prefix="/api/insights" + safety_stock.PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "tenant-a", "sub": "user-a", "role": role,
    }
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def assert_advisory(body, source):
    assert body["status"] == "analysis_only"
    assert body["source"] == source
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False


def test_connected_safety_stock_uses_observed_sales_and_lead_times():
    adapter, db = make_adapter(), ledger()
    response = client(adapter=adapter, db=db).get("/api/insights/safety-stock")
    assert response.status_code == 200, response.text
    body = response.json()
    assert_advisory(body, "connected_data")
    assert body["company_id"] == "tenant-a"
    adapter.get_orders_last_n_days.assert_awaited_once_with(90)

    malta = next(i for i in body["items"] if i["sku"] == "MALT-1")
    assert malta["status"] == "calculated"
    assert malta["avg_daily_sales"] == 10.0
    assert malta["lead_time_days_avg"] == 12.0
    assert malta["lead_time_observations"] == 5
    assert malta["demand_days_observed"] == 90
    assert malta["data_quality"] == "sparse"
    # SS = 1.645 * sqrt(12 * 2.011^2 + 10^2 * 1.414^2) = 25.9 -> 26
    assert malta["safety_stock_units"] == 26
    assert malta["reorder_point_units"] == 146
    assert malta["suggested_order_qty"] == 494
    # 40 on hand + 100 already on order is at/below the reorder point.
    assert malta["on_order"] == 100
    assert malta["stock_position"] == 140
    assert malta["reorder_now"] is True
    assert body["items"][0]["sku"] == "MALT-1"

    by_sku = {i["sku"]: i for i in body["items"]}
    assert by_sku["GB-1"]["status"] == "insufficient_data"
    assert "delivery time is unknown" in by_sku["GB-1"]["reason"]
    assert by_sku["CW-1"]["status"] == "insufficient_data"
    assert "No sales" in by_sku["CW-1"]["reason"]
    assert "safety_stock_units" not in by_sku["CW-1"]
    assert body["summary"] == {
        "items_total": 3, "items_calculated": 1, "items_insufficient_data": 2,
        "items_to_reorder": 1, "rows_skipped": 3,
    }


def test_ledger_reads_are_scoped_to_the_token_tenant():
    db = ledger()
    client(adapter=make_adapter(), db=db).get("/api/insights/safety-stock")
    assert {q.name for q in db.queries} == {
        "purchasing_orders", "purchasing_order_lines", "warehouse_receipt_documents",
    }
    for query in db.queries:
        assert ("company_id", "tenant-a") in query.filters


@pytest.mark.parametrize("broken", ["inventory", "invoices", "ledger"])
def test_source_failure_is_503_not_empty_result(broken):
    adapter, db = make_adapter(), ledger()
    if broken == "inventory":
        adapter.get_inventory_merged.side_effect = ConnectionError("down")
    elif broken == "invoices":
        adapter.get_orders_last_n_days.side_effect = RuntimeError("No QB connector configured")
    else:
        db.tables["purchasing_orders"] = ConnectionError("db down")
    response = client(adapter=adapter, db=db).get("/api/insights/safety-stock")
    assert response.status_code == 503


@pytest.mark.parametrize("role", ["viewer", "unknown"])
def test_roles_without_inventory_access_are_refused_before_reading(role):
    adapter = make_adapter()
    api = client(role=role, adapter=adapter, db=ledger())
    assert api.get("/api/insights/safety-stock").status_code == 403
    assert api.post("/api/insights/safety-stock/preview", json=preview_body()).status_code == 403
    adapter.get_inventory_merged.assert_not_awaited()


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "manager"])
def test_inventory_roles_may_read(role):
    response = client(role=role, adapter=make_adapter(), db=ledger()).get("/api/insights/safety-stock")
    assert response.status_code == 200


def test_unsupported_service_level_is_422():
    response = client(adapter=make_adapter(), db=ledger()).get(
        "/api/insights/safety-stock?service_level=0.5"
    )
    assert response.status_code == 422


def preview_body(**overrides):
    body = {
        "product_name": "Malta",
        "daily_sales": [8, 12] * 45,
        "lead_times_days": [10, 12, 14, 12, 12],
        "unit_cost": 6,
        "current_stock": 140,
        "service_level": 0.95,
    }
    body.update(overrides)
    return body


def test_preview_matches_connected_calculation_and_is_user_supplied():
    response = client(role="sales_rep").post("/api/insights/safety-stock/preview", json=preview_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert_advisory(body, "user_supplied")
    assert body["company_id"] == "tenant-a"
    assert body["safety_stock_units"] == 26
    assert body["reorder_point_units"] == 146
    assert body["suggested_order_qty"] == 494
    assert body["reorder_now"] is True


@pytest.mark.parametrize("overrides", [
    {"daily_sales": [1, 2]},
    {"daily_sales": [-1] * 10},
    {"lead_times_days": [0]},
    {"unit_cost": 0},
    {"service_level": 0.5},
    {"product_name": "x" * 121},
    {"company_id": "tenant-b"},
])
def test_preview_rejects_invalid_input(overrides):
    response = client().post("/api/insights/safety-stock/preview", json=preview_body(**overrides))
    assert response.status_code == 422


@pytest.mark.parametrize("module_path", [
    "app/api/insights/_supply_chain_sources.py",
    "app/api/insights/cause_analysis.py",
    "app/api/insights/pricing_suggestions.py",
    "app/api/insights/safety_stock.py",
    "app/api/insights/supplier_tco.py",
    "app/api/insights/supply_chain_health.py",
])
def test_supply_chain_insight_logs_are_constant(module_path):
    """Same rule as the Gate A log-safety test: one literal message, no values."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / module_path).read_text(encoding="utf-8"))
    for call in ast.walk(tree):
        func = getattr(call, "func", None)
        if isinstance(call, ast.Call) and isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) and func.value.id == "log":
            assert len(call.args) == 1 and not call.keywords, module_path
            assert isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str), module_path


SUPPLY_CHAIN_ROUTES = {
    ("GET", "/api/insights/cause-analysis"),
    ("GET", "/api/insights/safety-stock"),
    ("POST", "/api/insights/safety-stock/preview"),
    ("GET", "/api/insights/supply-chain-health"),
    ("POST", "/api/insights/supply-chain-health/preview"),
    ("GET", "/api/insights/pricing-suggestions"),
    ("POST", "/api/insights/pricing-suggestions/preview"),
    ("POST", "/api/insights/pricing-suggestions/supplier-increase"),
    ("POST", "/api/insights/supplier-tco/preview"),
}


def test_supply_chain_insights_are_mounted_on_the_app_and_require_login():
    from fastapi.routing import iter_route_contexts

    from app.main import app

    mounted = {
        (method, context.path)
        for context in iter_route_contexts(app.routes)
        for method in (getattr(context, "methods", None) or ())
    }
    assert SUPPLY_CHAIN_ROUTES <= mounted
    anonymous = TestClient(app)
    for method, path in SUPPLY_CHAIN_ROUTES:
        assert anonymous.request(method, path, json={}).status_code == 401, path
