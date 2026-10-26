"""Deterministic portfolio decision support; no SKU changes are executed."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.intelligence.portfolio.rationalization import (
    PortfolioPolicy, PortfolioSKU, assess_portfolio,
)


POLICY = PortfolioPolicy(D("12"), D("20"), 180)


def _sku(**kwargs):
    base = PortfolioSKU(
        "synthetic-tenant", "sku-a", D("1000"), D("200"), D("100"),
        D("10"), 30, D("9"), source_record_id="source-a", source_revision="v1",
    )
    return replace(base, **kwargs)


def test_healthy_sku_is_maintain_candidate_not_autonomous_order():
    assessment = assess_portfolio("synthetic-tenant", (_sku(),), POLICY)[0]
    assert assessment.status == "maintain_or_invest_candidate"
    assert assessment.contribution_margin_pct == D("20")
    assert assessment.weeks_of_supply == D("10")
    assert assessment.working_capital_tied == D("900")
    assert assessment.approval_required


def test_risk_sku_is_only_rationalization_candidate():
    item = _sku(total_contribution=D("-50"), weekly_case_velocity=D("2"), stock_age_days=220)
    result = assess_portfolio("synthetic-tenant", (item,), POLICY)[0]
    assert result.status == "rationalization_candidate_for_review"
    assert set(result.reasons) == {"low_contribution_margin", "excess_weeks_of_supply", "aged_inventory"}
    assert result.approval_required


def test_dependency_prevents_unqualified_discontinuation_suggestion():
    item = _sku(total_contribution=D("0"), required_by_skus=("sku-b",))
    result = assess_portfolio("synthetic-tenant", (item,), POLICY)[0]
    assert result.status == "dependency_review_required"
    assert "dependent_skus_block_rationalization" in result.reasons


def test_unknown_velocity_or_source_revisions_force_data_review():
    item = _sku(weekly_case_velocity=D("0"), source_revision="")
    result = assess_portfolio("synthetic-tenant", (item,), POLICY)[0]
    assert result.status == "data_review_required"
    assert result.weeks_of_supply is None
    assert "missing_source_revision" in result.reasons


def test_cross_tenant_and_duplicates_rejected():
    with pytest.raises(ValueError):
        assess_portfolio("synthetic-tenant", (_sku(company_id="other"),), POLICY)
    with pytest.raises(ValueError):
        assess_portfolio("synthetic-tenant", (_sku(), _sku()), POLICY)


def test_invalid_stock_and_self_dependency_rejected():
    for item in (_sku(stock_on_hand_cases=D("NaN")), _sku(stock_age_days=-1),
                 _sku(required_by_skus=("sku-a",))):
        with pytest.raises(ValueError):
            assess_portfolio("synthetic-tenant", (item,), POLICY)


def test_out_of_stock_with_documented_demand_requests_supply_review():
    item = _sku(stock_on_hand_cases=D("0"), weekly_case_velocity=D("5"),
                stock_age_days=240)
    result = assess_portfolio("synthetic-tenant", (item,), POLICY)[0]
    assert result.status == "supply_review_required"
    assert "stockout_with_observed_demand" in result.reasons
    assert "aged_inventory" not in result.reasons
    assert result.working_capital_tied == D("0")
    assert result.approval_required


def test_unverified_stockout_remains_data_review_not_supply_claim():
    item = _sku(stock_on_hand_cases=D("0"), weekly_case_velocity=D("5"),
                source_revision="")
    result = assess_portfolio("synthetic-tenant", (item,), POLICY)[0]
    assert result.status == "data_review_required"
    assert "missing_source_revision" in result.reasons
