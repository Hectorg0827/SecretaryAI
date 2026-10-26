"""Regression tests for draft-only dynamic pricing math and input guards."""

import math

import pytest

from app.intelligence.wholesale_distribution.dynamic_pricing import (
    DynamicPricingEngine,
    PricingTier,
)


def _engine():
    return DynamicPricingEngine("synthetic-tenant", 30)


def test_default_elasticity_cannot_drive_auto_price_changes():
    engine = _engine()
    estimate = engine.estimate_elasticity("segment", "wine", [])
    draft = engine.recommend_price("synthetic-sku", "segment", PricingTier.TIER_2,
                                   10.0, 9.0, 1000, estimate)
    assert draft.recommended_price == 10.0
    assert draft.revenue_impact_estimate == 0
    assert draft.to_dict()["requires_human_approval"] is True
    assert "Insufficient" in draft.rationale


@pytest.mark.parametrize("price,cost,units", [
    (0, 5, 100), (-1, 5, 100), (10, -1, 100), (10, 5, -2),
    (math.nan, 5, 100), (10, math.inf, 100),
])
def test_invalid_price_cost_or_units_fails_closed(price, cost, units):
    with pytest.raises(ValueError):
        _engine().recommend_price("sku", "segment", PricingTier.TIER_2, price, cost,
                                  units, _engine().estimate_elasticity("s", "c", []))


def test_target_margin_cannot_generate_division_by_zero():
    with pytest.raises(ValueError):
        DynamicPricingEngine("synthetic-tenant", 100)


def test_supplier_cost_scenario_uses_real_cost_and_dollar_pass_through():
    plan = _engine().supplier_increase_strategy(
        supplier="supplier", sku="sku", unit_cost_before=5, unit_cost_after=6,
        current_price=10,
        segment_data=[{"segment": "regional", "pricing_tier": "tier_2",
                       "annual_units": 100, "elasticity_ped": -2}],
    )
    draft = plan.recommendations[0]
    assert draft.recommended_price == pytest.approx(10.4)
    assert draft.projected_margin_pct == pytest.approx((10.4 - 6) / 10.4 * 100)
    assert plan.margin_impact_at_full_pass_through == pytest.approx((11 - 6) / 11 * 100 - 50)
    assert draft.confidence == 0.0  # scenario assumptions, not observed events
    assert draft.to_dict()["requires_human_approval"] is True


def test_supplier_cost_decrease_or_missing_cost_is_not_silent_increase_plan():
    for before, after in [(0, 1), (6, 5)]:
        with pytest.raises(ValueError):
            _engine().supplier_increase_strategy("supplier", "sku", before, after, 10, [])


def test_supplier_scenario_with_no_elasticity_discloses_low_confidence():
    plan = _engine().supplier_increase_strategy(
        "supplier", "sku", 5, 5.5, 10,
        [{"segment": "segment", "annual_units": 20}],
    )
    assert plan.recommendations[0].confidence == 0.0
    assert "Unverified" in plan.recommendations[0].rationale
