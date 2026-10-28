"""Brand scorecards: connected per-product vitality and supplier previews."""
import ast
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.api.insights import _product_sales
from app.auth.rbac import get_current_user
from app.connectors.base import Invoice
from app.intelligence.brands.scorecards import brand_vitality
from app.main import app

URL = "/api/insights/brand-scorecards"
SUPPLIER_URL = "/api/insights/brand-scorecards/supplier-preview"


def _inventory():
    def row(qb_id, name, sku, qty, cost, price=30.0):
        return {"qb_id": qb_id, "product_name": name, "sku": sku, "total_qty": qty,
                "purchase_cost": cost, "unit_price": price, "source_quality": "partial"}
    return [
        row("ITEM-A", "Valley Red 12x750ml", "VR-12", 60.0, 20.0),
        row("ITEM-B", "Coast White 12x750ml", "CW-12", 0.0, 18.0, 26.0),
        row("ITEM-C", "Gift Box", "GB-1", 10.0, 0.0, 20.0),
        row("ITEM-D", "Old Rose 6x750ml", "OR-6", 40.0, 15.0),
        row("ITEM-E", "Slow Sparkling 6x750ml", "SS-6", 200.0, 10.0),
        {"qb_id": None, "product_name": "Broken row"},
    ]


def _invoices():
    today = date.today()

    def inv(inv_id, customer, days_ago, lines, status="paid"):
        return Invoice(id=inv_id, qb_id=inv_id, customer_id=customer, customer_name=customer,
                       date=today - timedelta(days=days_ago), due_date=None, total=0, balance=0,
                       status=status, line_items=lines)

    def line(item_id, qty, amount, name=""):
        return {"item_id": item_id, "name": name, "quantity": qty, "amount": amount}

    return [
        inv("INV-1", "C1", 3, [line("ITEM-A", 20, 600), line("ITEM-B", 10, 260),
                               line("SVC-1", 1, 25, "Delivery fee")]),
        inv("INV-2", "C2", 10, [line("ITEM-A", 10, 300)]),
        inv("INV-3", "C1", 20, [line("ITEM-A", 10, 300), line("ITEM-A", -2, -60)]),
        inv("INV-4", "C3", 5, [line("ITEM-C", 5, 100)]),
        inv("INV-V", "C2", 4, [line("ITEM-A", 100, 3000)], status="voided"),
        {"id": "INV-BAD", "customer_id": "C9", "date": "not-a-date", "line_items": []},
        inv("INV-5", "C1", 40, [line("ITEM-A", 30, 900)]),
        inv("INV-6", "C2", 45, [line("ITEM-B", 20, 520)]),
        inv("INV-7", "C3", 60, [line("ITEM-E", 13, 140)]),
    ]


def _adapter():
    adapter = AsyncMock()
    adapter.get_inventory_merged.return_value = _inventory()
    adapter.get_orders_last_n_days.return_value = _invoices()
    return adapter


@pytest.fixture
def client():
    def make(role="owner", company_id="tenant-a", adapter=None):
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-1", "company_id": company_id, "role": role,
        }
        app.dependency_overrides[get_adapter] = lambda: adapter or _adapter()
        return TestClient(app)

    yield make
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_adapter, None)


def _assert_advisory(body, source):
    assert body["status"] == "analysis_only"
    assert body["source"] == source
    assert body["requires_human_review"] is True
    assert body["actions_executed"] is False
    assert body["generated_at"]


def test_routes_are_mounted_under_paid_insights_prefix():
    paths = {getattr(ctx, "path", "") for ctx in iter_route_contexts(app.routes)}
    assert {URL, SUPPLIER_URL} <= paths


def test_scores_each_product_from_connected_sales(client):
    adapter = _adapter()
    response = client(adapter=adapter).get(URL)
    assert response.status_code == 200
    body = response.json()
    _assert_advisory(body, "connected_data")
    adapter.get_orders_last_n_days.assert_awaited_once_with(56)

    rows = {row["item_id"]: row for row in body["products"]}
    assert [row["item_id"] for row in body["products"]][:3] == ["ITEM-A", "ITEM-B", "ITEM-C"]
    a = rows["ITEM-A"]
    assert a["net_sales"] == 1140.0          # voided invoice excluded, return netted
    assert a["net_cases"] == 38.0
    assert a["net_case_growth_pct"] == 26.7  # 38 vs 30 cases
    assert a["account_penetration_pct"] == 66.7  # 2 of 3 active customers
    assert a["repeat_account_pct"] == 50.0
    assert a["return_rate_pct"] == 5.0
    assert a["gross_margin_pct"] == 33.3     # (1140 - 38 x 20) / 1140
    assert a["weeks_of_supply"] == 6.3       # 60 / 9.5 per week
    assert a["review_status"] == "draft_requires_review"

    b = rows["ITEM-B"]
    assert b["net_case_growth_pct"] == -50.0
    assert b["weeks_of_supply"] == 0.0

    # Missing cost is reported, never shown as a margin.
    c = rows["ITEM-C"]
    assert c["gross_margin_pct"] is None
    assert "unavailable_product_cost" in c["gaps"]
    assert "insufficient_comparable_history" in c["gaps"]

    d = rows["ITEM-D"]
    assert d["net_cases"] == 0.0 and d["weeks_of_supply"] is None
    assert body["products_total"] == 5
    assert body["rows_skipped"] == 2         # broken inventory row + undated invoice
    assert body["lines_not_in_catalog"] == 1


def test_tenant_comes_from_token_not_request(client):
    seen = []

    def spy(current, previous=None):
        seen.append(current.company_id)
        return brand_vitality(current, previous)

    with patch.object(_product_sales, "brand_vitality", side_effect=spy):
        response = client(company_id="tenant-a").get(URL, params={"company_id": "tenant-b"})
    assert response.status_code == 200
    assert seen and set(seen) == {"tenant-a"}


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_financials_are_refused(client, role):
    assert client(role=role).get(URL).status_code == 403
    assert client(role=role).post(SUPPLIER_URL, json={"suppliers": [_supplier()]}).status_code == 403


@pytest.mark.parametrize("method", ["get_inventory_merged", "get_orders_last_n_days"])
def test_source_failure_is_an_outage(client, method):
    adapter = _adapter()
    getattr(adapter, method).side_effect = ConnectionError("provider down")
    response = client(adapter=adapter).get(URL)
    assert response.status_code == 503
    assert "products" not in response.json()


def test_invalid_window_is_rejected(client):
    assert client().get(URL, params={"weeks": 0}).status_code == 422
    assert client().get(URL, params={"weeks": 27}).status_code == 422


def _supplier(**changes):
    base = {
        "supplier_name": "Atlantic Imports", "units_ordered": 1000, "units_filled": 900,
        "units_delivered": 900, "units_damaged": 18, "on_time_shipments": 8,
        "received_shipments": 10, "evidence_reference": "Receiving log Oct", "evidence_version": "v1",
    }
    return {**base, **changes}


def test_supplier_preview_scores_entered_figures(client):
    response = client().post(SUPPLIER_URL, json={"suppliers": [
        _supplier(), _supplier(supplier_name="No Paperwork Co", evidence_reference=""),
    ]})
    assert response.status_code == 200
    body = response.json()
    _assert_advisory(body, "user_supplied")
    first, second = body["suppliers"]
    assert first["fill_rate_pct"] == 90.0
    assert first["on_time_pct"] == 80.0
    assert first["damage_rate_pct"] == 2.0
    assert first["composite_score"] == 87.6
    # Without an evidence reference the rates show but no rankable score.
    assert second["composite_score"] is None
    assert second["review_status"] == "source_review_required"


def test_supplier_preview_custom_weights(client):
    response = client().post(SUPPLIER_URL, json={
        "suppliers": [_supplier()], "weights": {"fill": 0.5, "on_time": 0.3, "undamaged": 0.2},
    })
    assert response.status_code == 200
    assert response.json()["suppliers"][0]["composite_score"] == 88.6


@pytest.mark.parametrize("body", [
    {"suppliers": []},
    {"suppliers": [_supplier(units_filled=1001)]},
    {"suppliers": [_supplier(on_time_shipments=11)]},
    {"suppliers": [_supplier(units_ordered="NaN")]},
    {"suppliers": [_supplier(units_ordered=-1)]},
    {"suppliers": [_supplier(supplier_name="x" * 121)]},
    {"suppliers": [_supplier()], "weights": {"fill": 0.5, "on_time": 0.5, "undamaged": 0.5}},
    {"suppliers": [_supplier()], "company_id": "tenant-b"},
    {"suppliers": [_supplier()] * 26},
])
def test_supplier_preview_invalid_input_is_422(client, body):
    assert client().post(SUPPLIER_URL, json=body).status_code == 422


def test_supplier_preview_uses_token_tenant(client):
    from app.api.insights import brand_scorecards
    from app.intelligence.brands.scorecards import supplier_scorecard

    with patch.object(brand_scorecards, "supplier_scorecard", wraps=supplier_scorecard) as spy:
        client(company_id="tenant-a").post(SUPPLIER_URL, json={"suppliers": [_supplier()]})
    assert spy.call_args.args[0].company_id == "tenant-a"


@pytest.mark.parametrize("module", [
    "app/api/insights/_product_sales.py",
    "app/api/insights/brand_scorecards.py",
    "app/api/insights/growth_opportunities.py",
    "app/api/insights/portfolio_review.py",
    "app/api/insights/price_waterfall.py",
    "app/api/insights/product_concepts.py",
])
def test_growth_insight_logs_are_constant(module):
    tree = ast.parse((Path(__file__).resolve().parents[1] / module).read_text(encoding="utf-8"))
    for call in ast.walk(tree):
        func = getattr(call, "func", None)
        if isinstance(call, ast.Call) and isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) and func.value.id == "log":
            assert len(call.args) == 1 and not call.keywords
            assert isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
