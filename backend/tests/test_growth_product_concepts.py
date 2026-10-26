"""Product whitespace and launch cohort acceptance, with synthetic inputs."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.intelligence.products.concepts import (
    ConceptScenario, LaunchObservation, assess_concept, track_launch,
)


def _concept(**kwargs):
    value = ConceptScenario(
        "synthetic-tenant", "new-sku-concept", D("10000"), D("8000"),
        D("10"), D("30"), D("18"), D("2"), D("1000"),
        D("25"), D("6"), assumption_revision="assumption-v1",
        demand_source_record_id="synthetic-research",
    )
    return replace(value, **kwargs)


def test_whitespace_and_cannibalization_reduce_incremental_profit():
    result = assess_concept(_concept())
    assert result.addressable_whitespace_cases == D("2000")
    assert result.projected_new_product_cases == D("200")
    assert result.cannibalized_incumbent_cases == D("50")
    assert result.gross_new_product_contribution == D("2000")
    assert result.incumbent_contribution_lost == D("300")
    assert result.incremental_first_year_profit_after_launch == D("700")
    assert result.estimated_payback_months == D("12000") / D("1700")
    assert "cannibalization_requires_portfolio_review" in result.risk_flags
    assert result.action_status == "draft_only"


def test_missing_assumptions_and_nonprofitable_concept_are_flagged():
    result = assess_concept(_concept(assumption_revision="", landed_cost_per_case=D("35")))
    assert not result.documented_assumptions
    assert result.estimated_payback_months is None
    assert "nonpositive_new_product_unit_contribution" in result.risk_flags
    assert "first_year_profit_not_positive" in result.risk_flags


def test_served_market_larger_than_demand_is_rejected():
    with pytest.raises(ValueError):
        assess_concept(_concept(existing_served_category_cases=D("10001")))


@pytest.mark.parametrize("value", [D("-1"), D("101"), D("NaN")])
def test_invalid_market_capture_cannot_produce_forecast(value):
    with pytest.raises(ValueError):
        assess_concept(_concept(proposed_capture_pct=value))


def test_missing_market_whitespace_is_not_synthetic_demand():
    result = assess_concept(_concept(existing_served_category_cases=D("10000")))
    assert result.projected_new_product_cases == 0
    assert result.estimated_payback_months is None


def _launch(**kwargs):
    value = LaunchObservation(
        "synthetic-tenant", "new-sku-concept", D("200"), D("120"),
        D("2400"), D("600"), 20, 8,
        source_record_id="synthetic-actual", source_revision="v2",
    )
    return replace(value, **kwargs)


def test_launch_tracking_uses_observed_metrics():
    result = track_launch(_launch())
    assert result.volume_attainment_pct == D("60")
    assert result.realized_contribution_margin_pct == D("25")
    assert result.repeat_account_rate_pct == D("40")
    assert result.documented_inputs


def test_launch_missing_plan_and_revision_remains_unknown():
    result = track_launch(_launch(planned_cases_to_date=D("0"), source_revision=""))
    assert result.volume_attainment_pct is None
    assert not result.documented_inputs
    assert "missing_launch_plan" in result.gaps


def test_launch_account_count_is_validated():
    with pytest.raises(ValueError):
        track_launch(_launch(repeat_accounts=21))
