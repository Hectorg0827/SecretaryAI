"""Regression tests for full-cost pricing. Synthetic inputs only; no provider writes."""

from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.insights import price_waterfall
from app.auth.rbac import get_current_user
from app.intelligence.pricing.waterfall import (
    REQUIRED_COMPONENTS, WAREHOUSE_COMPONENTS, LandedCostLine, PriceScenario,
    calculate_price_waterfall,
)
from app.main import app


INBOUND = {
    "product": "16", "international_freight": "2", "insurance": "0.5",
    "duty": "1", "customs_brokerage": "0.5", "port_fees": "0.5",
    "domestic_freight": "0.5", "excise": "1", "other_taxes": "0",
}
WAREHOUSE = {
    "warehouse_receiving": "0.4", "warehouse_storage": "1.2",
    "warehouse_handling": "0.3", "warehouse_outbound": "1.5",
    "warehouse_shrink": "0.2", "inventory_carrying": "0.4",
}


def _lines(costs):
    return tuple(
        LandedCostLine(k, D(v), source_record_id=f"synthetic-{k}", source_revision="v1")
        for k, v in costs.items()
    )


def scenario(extra=None):
    return PriceScenario(
        company_id="tenant-a", sku="test-sku", currency="USD",
        price_to_distributor=D("30"),
        cost_lines=_lines({**INBOUND, **(extra or {})}),
        discount_per_case=D("2"), commission_per_case=D("1"),
        marketing_per_case=D("1"), target_margin_pct=D("30"),
        approved_list_price=D("30"),
    )


def test_full_cost_separates_inbound_and_post_warehouse_expenses():
    result = calculate_price_waterfall(scenario(WAREHOUSE))
    assert result.landed_cost_per_case == D("22")
    assert result.warehouse_cost_per_case == D("4.0")
    assert result.fully_loaded_cost_per_case == D("26.0")
    assert result.gross_profit_per_case == D("6")
    assert result.contribution_per_case == D("0.0")
    assert result.complete_cost_components
    assert result.complete_warehouse_components
    assert not result.missing_warehouse_components
    assert result.minimum_list_price_at_target_margin == D("42.0")
    assert result.fully_loaded_contribution_margin_pct == D("0.0")


def test_missing_storage_is_unknown_not_implicitly_free():
    incomplete = {k: v for k, v in WAREHOUSE.items() if k != "warehouse_storage"}
    result = calculate_price_waterfall(scenario(incomplete))
    assert result.complete_warehouse_components is False
    assert result.missing_warehouse_components == ("warehouse_storage",)
    assert result.warehouse_cost_per_case == D("2.8")


def test_warehouse_cost_can_turn_gross_profit_into_an_operating_loss():
    more_cost = {**WAREHOUSE, "warehouse_storage": "10"}
    result = calculate_price_waterfall(scenario(more_cost))
    assert result.gross_profit_per_case > 0
    assert result.contribution_per_case < 0
    assert "negative_unit_contribution" in result.exception_reasons
    assert result.minimum_list_price_at_target_margin > D("50")


def test_zero_costs_must_be_explicit_for_warehouse_completeness():
    zeroes = {name: "0" for name in WAREHOUSE_COMPONENTS}
    complete = calculate_price_waterfall(scenario(zeroes))
    missing = calculate_price_waterfall(scenario())
    assert complete.complete_warehouse_components
    assert missing.complete_warehouse_components is False
    assert set(missing.missing_warehouse_components) == WAREHOUSE_COMPONENTS


@pytest.mark.parametrize("extra", [
    {"warehouse_storage": "-1"},
    {"warehouse_outbound": "NaN"},
    {"not_a_cost": "0.10"},
])
def test_unsafe_or_unrecognized_warehouse_cost_fails_closed(extra):
    with pytest.raises(ValueError):
        calculate_price_waterfall(scenario(extra))


def test_duplicate_inbound_or_warehouse_line_is_rejected():
    base = scenario(WAREHOUSE)
    with pytest.raises(ValueError):
        calculate_price_waterfall(replace(base, cost_lines=base.cost_lines + base.cost_lines[-1:]))


def test_existing_inbound_breakdown_does_not_claim_warehouse_completion():
    result = calculate_price_waterfall(scenario())
    assert result.landed_cost_per_case == D("22")
    assert result.warehouse_cost_per_case == D("0")
    assert result.contribution_per_case == D("4")
    assert result.complete_cost_components
    assert not result.complete_warehouse_components


def test_api_shows_warehouse_step_and_never_writes_price():
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "test-user", "company_id": "tenant-a", "role": "owner",
    }
    try:
        payload = {
            "product_label": "Test SKU", "price_per_case": 30,
            "approved_price_per_case": 30, "discount_per_case": 2,
            "commission_per_case": 1, "marketing_per_case": 1,
            "cost_evidence_reference": "synthetic-invoices",
            "cost_evidence_version": "v1",
            "cost_lines": [
                {"component": k, "amount_per_case": float(v)}
                for k, v in {**INBOUND, **WAREHOUSE}.items()
            ],
        }
        with patch.object(price_waterfall, "calculate_price_waterfall",
                          wraps=calculate_price_waterfall) as spy:
            res = TestClient(app).post("/api/insights/price-waterfall/preview", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()
        assert spy.call_args.args[0].company_id == "tenant-a"
        assert body["warehouse_cost_per_case"] == 4.0
        assert body["landed_cost_per_case"] == 22.0
        assert body["fully_loaded_cost_per_case"] == 26.0
        assert body["minimum_list_price_at_target_margin"] == 42.0
        assert body["steps"][-1]["key"] == "warehouse_cost"
        assert body["steps"][-1]["running"] == 0.0
        assert body["complete_warehouse_components"] is True
        assert body["warehouse_costs_verified"] is False
        assert body["price_written"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)
