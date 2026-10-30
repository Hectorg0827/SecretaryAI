"""Pricing suggestions insight: advisory only, evidence-gated, never changes prices."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_adapter
from app.api.insights import pricing_suggestions
from app.auth.rbac import get_current_user

TODAY = date.today()


def period_invoice(period, item_id, qty, price):
    """One invoice in the middle of 4-week period ``period`` (0 = oldest of 13)."""
    age = (12 - period) * 28 + 14
    return {"date": TODAY - timedelta(days=age), "status": "paid", "total": qty * price,
            "line_items": [{"item_id": item_id, "name": "", "quantity": qty,
                            "unit_price": price, "amount": qty * price}]}


def invoices():
    rows = []
    for period in range(13):
        # Malta alternates $10 / $11; customers buy 100 / 80 cases (elastic).
        rows.append(period_invoice(period, "QB-1", 100 if period % 2 == 0 else 80,
                                   10.0 if period % 2 == 0 else 11.0))
        rows.append(period_invoice(period, "QB-2", 10, 20.0))
    rows.append({"date": TODAY, "status": "open", "total": 5, "line_items": ["not a line"]})
    return rows


def make_adapter():
    inventory = [
        {"qb_id": "QB-1", "product_name": "Malta", "sku": "MALT-1", "unit_price": 10.0, "purchase_cost": 8.0},
        {"qb_id": "QB-2", "product_name": "Ginger Beer", "sku": "GB-1", "unit_price": 20.0, "purchase_cost": 10.0},
        {"qb_id": "QB-3", "product_name": "Sample Pack", "sku": "SP-1", "unit_price": 0, "purchase_cost": 2.0},
        {"qb_id": "QB-4", "product_name": "Coconut Water", "sku": "CW-1", "unit_price": 6.0, "purchase_cost": 3.0},
    ]
    return SimpleNamespace(
        get_inventory_merged=AsyncMock(return_value=inventory),
        get_orders_last_n_days=AsyncMock(return_value=invoices()),
    )


def client(role="owner", adapter=None):
    app = FastAPI()
    app.include_router(pricing_suggestions.router, prefix="/api/insights" + pricing_suggestions.PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "tenant-a", "sub": "user-a", "role": role,
    }
    app.dependency_overrides[get_adapter] = lambda: adapter
    return TestClient(app)


def test_connected_suggestions_need_observed_price_changes():
    adapter = make_adapter()
    response = client(adapter=adapter).get("/api/insights/pricing-suggestions")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "analysis_only"
    assert body["source"] == "connected_data"
    assert body["actions_executed"] is False
    assert body["prices_changed"] is False
    assert body["company_id"] == "tenant-a"
    adapter.get_orders_last_n_days.assert_awaited_once_with(364)

    items = {i["sku"]: i for i in body["items"]}
    malta = items["MALT-1"]
    assert malta["status"] == "suggested_change"
    assert malta["price_changes_observed"] == 12
    assert malta["elasticity"] == pytest.approx(-2.333, abs=0.001)
    assert malta["current_margin_pct"] == 20.0
    assert malta["below_target_margin"] is True
    # Target price 8 / 0.7 = 11.43; elastic demand dampens the move to +6.1%.
    assert malta["suggested_price"] == pytest.approx(10.6122, abs=0.0001)
    assert malta["change_pct"] == pytest.approx(6.12, abs=0.01)
    assert malta["units_last_12_months"] == 1180

    ginger = items["GB-1"]
    assert ginger["status"] == "hold"
    assert ginger["suggested_price"] == ginger["current_price"] == 20.0
    assert ginger["price_changes_observed"] == 0
    assert ginger["price_sensitivity"] == "unknown"
    assert items["SP-1"]["status"] == "insufficient_data"
    assert items["CW-1"]["reason"] == "No sales in the last 12 months."
    assert body["summary"] == {
        "items_total": 4, "suggested_changes": 1, "holds": 1, "below_target_margin": 1,
        "items_insufficient_data": 2, "rows_skipped": 1,
    }
    assert body["items"][0]["sku"] == "MALT-1"


def test_source_failure_is_503():
    adapter = make_adapter()
    adapter.get_orders_last_n_days.side_effect = ConnectionError("down")
    assert client(adapter=adapter).get("/api/insights/pricing-suggestions").status_code == 503
    adapter = make_adapter()
    adapter.get_inventory_merged.side_effect = RuntimeError("No QB connector configured")
    assert client(adapter=adapter).get("/api/insights/pricing-suggestions").status_code == 503


def test_target_margin_out_of_range_is_422():
    response = client(adapter=make_adapter()).get("/api/insights/pricing-suggestions?target_margin_pct=150")
    assert response.status_code == 422


@pytest.mark.parametrize("role", ["back_office", "sales_rep", "viewer"])
def test_roles_without_financial_access_are_refused(role):
    adapter = make_adapter()
    api = client(role=role, adapter=adapter)
    assert api.get("/api/insights/pricing-suggestions").status_code == 403
    assert api.post("/api/insights/pricing-suggestions/preview", json=check_body()).status_code == 403
    assert api.post("/api/insights/pricing-suggestions/supplier-increase", json=increase_body()).status_code == 403
    adapter.get_inventory_merged.assert_not_awaited()


def check_body(**overrides):
    body = {
        "product_name": "Malta", "current_price": 10, "unit_cost": 8, "annual_units": 1000,
        "price_changes": [
            {"old_price": 10, "new_price": 11, "units_before": 100, "units_after": 80, "period_days": 28},
            {"old_price": 11, "new_price": 10, "units_before": 80, "units_after": 100, "period_days": 28},
            {"old_price": 10, "new_price": 11, "units_before": 100, "units_after": 80, "period_days": 28},
        ],
    }
    body.update(overrides)
    return body


def test_price_check_uses_entered_history():
    response = client(role="manager").post("/api/insights/pricing-suggestions/preview", json=check_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "user_supplied"
    assert body["company_id"] == "tenant-a"
    assert body["prices_changed"] is False
    assert body["status"] == "analysis_only"
    assert body["outcome"] == "suggested_change"
    assert body["suggested_price"] == pytest.approx(10.6122, abs=0.0001)
    assert body["price_changes_observed"] == 3


def test_price_check_without_history_holds_price():
    response = client().post("/api/insights/pricing-suggestions/preview",
                             json=check_body(price_changes=[], competitor_prices=[9.5, 10.5]))
    body = response.json()
    assert body["outcome"] == "hold"
    assert body["status"] == "analysis_only"
    assert body["suggested_price"] == 10
    assert body["change_pct"] == 0
    assert "hold price" in body["rationale"]


def increase_body(**overrides):
    body = {
        "supplier": "Andes Bottling", "product_name": "Malta",
        "cost_before": 5, "cost_after": 6, "current_price": 10,
        "segments": [
            {"segment": "Key accounts", "pricing_tier": "tier_1", "annual_units": 1000},
            {"segment": "Small shops", "pricing_tier": "tier_3", "annual_units": 500, "elasticity_ped": -0.3},
        ],
    }
    body.update(overrides)
    return body


def test_supplier_increase_plan_per_customer_group():
    response = client().post("/api/insights/pricing-suggestions/supplier-increase", json=increase_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "user_supplied"
    assert body["company_id"] == "tenant-a"
    assert body["supplier_increase_pct"] == 20.0
    assert body["blended_pass_through_pct"] == pytest.approx(76.7, abs=0.05)
    plans = {r["segment"]: r for r in body["segment_recommendations"]}
    assert plans["Key accounts"]["pass_through_strategy"] == "staged"
    assert plans["Key accounts"]["recommended_price"] == 10.7
    assert plans["Small shops"]["recommended_price"] == 10.9
    assert body["prices_changed"] is False


@pytest.mark.parametrize("path,body", [
    ("/preview", check_body(current_price=0)),
    ("/preview", check_body(target_margin_pct=99)),
    ("/preview", check_body(competitor_prices=[1.0] * 21)),
    ("/preview", check_body(tenant="tenant-b")),
    ("/supplier-increase", increase_body(cost_after=4)),
    ("/supplier-increase", increase_body(segments=[])),
    ("/supplier-increase", increase_body(segments=[
        {"segment": "Same", "pricing_tier": "tier_2", "annual_units": 1},
        {"segment": "Same", "pricing_tier": "tier_3", "annual_units": 1},
    ])),
    ("/supplier-increase", increase_body(segments=[
        {"segment": "Gold", "pricing_tier": "tier_9", "annual_units": 1},
    ])),
])
def test_invalid_what_if_input_is_422(path, body):
    response = client().post("/api/insights/pricing-suggestions" + path, json=body)
    assert response.status_code == 422
