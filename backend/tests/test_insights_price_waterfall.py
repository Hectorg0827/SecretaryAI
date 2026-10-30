"""Price waterfall preview: user-supplied what-if, nothing stored or changed."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.insights import price_waterfall
from app.auth.rbac import get_current_user
from app.intelligence.pricing.waterfall import REQUIRED_COMPONENTS, calculate_price_waterfall
from app.main import app

URL = "/api/insights/price-waterfall/preview"
COSTS = {
    "product": 16, "international_freight": 2, "insurance": 0.5, "duty": 1,
    "customs_brokerage": 0.5, "port_fees": 0.5, "domestic_freight": 0.5,
    "excise": 1, "other_taxes": 0,
}


@pytest.fixture
def client():
    def make(role="owner", company_id="tenant-a"):
        app.dependency_overrides[get_current_user] = lambda: {
            "sub": "user-1", "company_id": company_id, "role": role,
        }
        return TestClient(app)

    yield make
    app.dependency_overrides.pop(get_current_user, None)


def _body(**changes):
    base = {
        "product_label": "Valley Red 12x750ml", "currency": "USD",
        "price_per_case": 30, "approved_price_per_case": 30,
        "discount_per_case": 2, "returns_allowance_per_case": 0,
        "commission_per_case": 1, "marketing_per_case": 1,
        "cost_lines": [{"component": name, "amount_per_case": amount} for name, amount in COSTS.items()],
        "cost_evidence_reference": "Broker invoice Oct", "cost_evidence_version": "v1",
    }
    return {**base, **changes}


def test_walks_list_price_down_to_pocket_price(client):
    response = client().post(URL, json=_body())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "analysis_only" and body["source"] == "user_supplied"
    assert body["requires_human_review"] is True and body["actions_executed"] is False
    assert [step["running"] for step in body["steps"]] == [30.0, 28.0, 28.0, 27.0, 26.0, 4.0]
    assert body["net_revenue_per_case"] == 28.0
    assert body["pocket_price_per_case"] == 26.0
    assert body["landed_cost_per_case"] == 22.0
    assert body["gross_profit_per_case"] == 6.0
    assert body["gross_margin_pct"] == 21.4     # 6 / 28, a margin not a markup
    assert body["contribution_per_case"] == 4.0
    assert body["estimated_distributor_to_retailer"] == 37.33
    assert body["complete_cost_components"] is True
    assert body["documented_cost_components"] is True
    assert body["missing_cost_components"] == []
    assert body["exception_reasons"] == ["below_target_margin"]
    assert body["review_status"] == "draft_unverified"


def test_missing_costs_and_price_exceptions_are_flagged(client):
    lines = [{"component": "product", "amount_per_case": 16}]
    body = client().post(URL, json=_body(
        cost_lines=lines, price_per_case=24, approved_price_per_case=None,
        commission_per_case=4, marketing_per_case=8, cost_evidence_reference="",
    )).json()
    assert body["complete_cost_components"] is False
    assert set(body["missing_cost_components"]) == REQUIRED_COMPONENTS - {"product"}
    assert {"missing_cost_categories", "unreconciled_cost_source_revision",
            "missing_approved_price_reference", "negative_unit_contribution"} <= set(body["exception_reasons"])


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_financials_are_refused(client, role):
    assert client(role=role).post(URL, json=_body()).status_code == 403


@pytest.mark.parametrize("changes", [
    {"price_per_case": 0},
    {"price_per_case": "NaN"},
    {"discount_per_case": -1},
    {"discount_per_case": 30},           # consumes all revenue
    {"target_margin_pct": 100},
    {"currency": "usd"},
    {"cost_lines": [{"component": "duty", "amount_per_case": 1}] * 2},
    {"cost_lines": [{"component": "Duty; DROP", "amount_per_case": 1}]},
    {"cost_lines": [{"component": f"c_{chr(97 + i)}", "amount_per_case": 1} for i in range(21)]},
    {"company_id": "tenant-b"},
])
def test_invalid_input_is_422(client, changes):
    assert client().post(URL, json=_body(**changes)).status_code == 422


def test_tenant_comes_from_the_token(client):
    with patch.object(price_waterfall, "calculate_price_waterfall", wraps=calculate_price_waterfall) as spy:
        response = client(company_id="tenant-a").post(URL, json=_body())
    assert response.status_code == 200
    assert spy.call_args.args[0].company_id == "tenant-a"
