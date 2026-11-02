"""Portfolio review: keep / fix / phase-out suggestions from connected data."""
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.api.insights import portfolio_review
from app.auth.rbac import get_current_user
from app.connectors.base import Invoice
from app.intelligence.portfolio.rationalization import assess_portfolio
from app.main import app

URL = "/api/insights/portfolio-review"


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


def test_sorts_products_into_keep_fix_and_phase_out(client):
    adapter = _adapter()
    response = client(adapter=adapter).get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "analysis_only" and body["source"] == "connected_data"
    assert body["requires_human_review"] is True and body["actions_executed"] is False
    adapter.get_orders_last_n_days.assert_awaited_once_with(91)

    rows = {row["item_id"]: row for row in body["products"]}
    keep = rows["ITEM-A"]
    assert keep["decision"] == "keep"
    assert keep["net_sales"] == 2040.0
    assert keep["contribution_margin_pct"] == 33.3   # (2040 - 68 x 20) / 2040
    assert keep["weeks_of_supply"] == 11.5           # 60 / (68 / 13)
    assert keep["working_capital_tied"] == 1200.0

    stockout = rows["ITEM-B"]
    assert stockout["decision"] == "fix"
    assert stockout["status"] == "supply_review_required"
    assert "stockout_with_observed_demand" in stockout["reasons"]

    phase_out = rows["ITEM-E"]
    assert phase_out["decision"] == "review_phase_out"
    assert set(phase_out["reasons"]) == {"low_contribution_margin", "excess_weeks_of_supply"}
    assert phase_out["contribution_margin_pct"] == 7.1
    assert phase_out["weeks_of_supply"] == 200.0
    assert phase_out["working_capital_tied"] == 2000.0

    # Missing cost or no sales is a data check, never an invented number.
    assert rows["ITEM-C"]["decision"] == "check_data"
    assert rows["ITEM-C"]["reasons"] == ["missing_product_cost"]
    assert rows["ITEM-C"]["contribution_margin_pct"] is None
    assert rows["ITEM-D"]["decision"] == "check_data"
    assert rows["ITEM-D"]["no_sales_in_period"] is True

    assert body["products"][0]["item_id"] == "ITEM-E"
    assert body["summary"] == {"review_phase_out": 1, "fix": 1, "check_data": 2, "keep": 1}
    assert body["working_capital_in_phase_out_review"] == 2000.0
    assert body["rows_skipped"] == 2
    assert set(body["checks_not_applied"]) == {"aged_inventory", "product_dependencies"}


def test_policy_thresholds_change_the_suggestions(client):
    body = client().get(URL, params={"min_margin_pct": 40}).json()
    rows = {row["item_id"]: row for row in body["products"]}
    assert rows["ITEM-A"]["decision"] == "review_phase_out"
    assert rows["ITEM-A"]["reasons"] == ["low_contribution_margin"]


def test_returns_exceeding_sales_skip_the_row_not_the_report(client):
    adapter = _adapter()
    invoices = _invoices()
    invoices[3].line_items = [{"item_id": "ITEM-C", "quantity": -5, "amount": -100}]
    inventory = _inventory()
    inventory[2]["purchase_cost"] = 8.0
    adapter.get_orders_last_n_days.return_value = invoices
    adapter.get_inventory_merged.return_value = inventory
    body = client(adapter=adapter).get(URL).json()
    assert "ITEM-C" not in {row["item_id"] for row in body["products"]}
    assert body["rows_skipped"] == 3


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_financials_are_refused(client, role):
    assert client(role=role).get(URL).status_code == 403


@pytest.mark.parametrize("method", ["get_inventory_merged", "get_orders_last_n_days"])
def test_source_failure_is_an_outage(client, method):
    adapter = _adapter()
    getattr(adapter, method).side_effect = RuntimeError("No QB connector configured")
    response = client(adapter=adapter).get(URL)
    assert response.status_code == 503
    assert "products" not in response.json()


@pytest.mark.parametrize("params", [{"weeks": 3}, {"weeks": 53}, {"max_weeks_of_supply": 0}, {"min_margin_pct": 101}])
def test_invalid_parameters_are_rejected(client, params):
    assert client().get(URL, params=params).status_code == 422


def test_tenant_comes_from_the_token(client):
    with patch.object(portfolio_review, "assess_portfolio", wraps=assess_portfolio) as spy:
        response = client(company_id="tenant-a").get(URL, params={"company_id": "tenant-b"})
    assert response.status_code == 200
    assert spy.call_count == 4  # every product with cost and stock readings
    for call in spy.call_args_list:
        company_id, skus = call.args[0], call.args[1]
        assert company_id == "tenant-a"
        assert all(sku.company_id == "tenant-a" for sku in skus)
