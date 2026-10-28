"""Product concept preview: user-supplied launch economics, nothing stored."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.insights import product_concepts
from app.auth.rbac import get_current_user
from app.intelligence.products.concepts import assess_concept
from app.main import app

URL = "/api/insights/product-concepts/preview"


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
        "concept_name": "Sparkling rose 4-pack",
        "observed_category_demand_cases": 10000, "existing_served_category_cases": 8000,
        "proposed_capture_pct": 10, "wholesale_price_per_case": 30,
        "landed_cost_per_case": 18, "variable_launch_cost_per_case": 2,
        "one_time_launch_cost": 1000, "cannibalization_pct": 25,
        "incumbent_contribution_per_case": 6,
        "demand_source_reference": "Category study Sept", "assumption_version": "v1",
    }
    return {**base, **changes}


def test_estimates_whitespace_cannibalization_and_payback(client):
    response = client().post(URL, json=_body())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "analysis_only" and body["source"] == "user_supplied"
    assert body["requires_human_review"] is True and body["actions_executed"] is False
    assert body["addressable_whitespace_cases"] == 2000.0
    assert body["projected_new_product_cases"] == 200.0
    assert body["cannibalized_incumbent_cases"] == 50.0
    assert body["gross_new_product_contribution"] == 2000.0
    assert body["incumbent_contribution_lost"] == 300.0
    assert body["incremental_first_year_profit"] == 700.0
    assert body["estimated_payback_months"] == 7.1    # 12000 / 1700
    assert body["documented_assumptions"] is True
    assert body["risk_flags"] == ["cannibalization_requires_portfolio_review"]
    assert body["review_status"] == "draft_only"


def test_unprofitable_unsourced_concept_is_flagged(client):
    body = client().post(URL, json=_body(landed_cost_per_case=35, demand_source_reference="")).json()
    assert body["estimated_payback_months"] is None
    assert body["documented_assumptions"] is False
    assert {"unverified_market_or_assumptions", "nonpositive_new_product_unit_contribution",
            "first_year_profit_not_positive"} <= set(body["risk_flags"])


@pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
def test_roles_without_financials_are_refused(client, role):
    assert client(role=role).post(URL, json=_body()).status_code == 403


@pytest.mark.parametrize("changes", [
    {"existing_served_category_cases": 10001},
    {"proposed_capture_pct": 101},
    {"cannibalization_pct": -1},
    {"wholesale_price_per_case": "Infinity"},
    {"concept_name": ""},
    {"concept_name": "x" * 121},
    {"company_id": "tenant-b"},
])
def test_invalid_input_is_422(client, changes):
    assert client().post(URL, json=_body(**changes)).status_code == 422


def test_tenant_comes_from_the_token(client):
    with patch.object(product_concepts, "assess_concept", wraps=assess_concept) as spy:
        response = client(company_id="tenant-a").post(URL, json=_body())
    assert response.status_code == 200
    assert spy.call_args.args[0].company_id == "tenant-a"
