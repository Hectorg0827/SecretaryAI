"""Brand/supplier scorecard acceptance with synthetic observation snapshots."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from app.intelligence.brands.scorecards import (
    BrandPeriod, SupplierObservation, SupplierScoreWeights,
    brand_vitality, supplier_scorecard,
)


def _period(**kwargs):
    base = BrandPeriod(
        company_id="tenant-a", brand_id="brand-a",
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 30),
        cases_sold=D("120"),
        cases_returned=D("10"), active_accounts=20, eligible_accounts=40,
        repeat_accounts=12, net_sales=D("5000"), gross_profit=D("1500"),
        stock_on_hand_cases=D("180"), weekly_case_velocity=D("30"),
        source_record_id="synthetic-read", source_revision="v1",
    )
    return replace(base, **kwargs)


def _supplier(**kwargs):
    base = SupplierObservation(
        company_id="tenant-a", supplier_id="sup-a", units_ordered=D("1000"),
        units_filled=D("900"), units_delivered=D("900"), units_damaged=D("18"),
        on_time_shipments=8, received_shipments=10,
        source_record_id="synthetic-shipments", source_revision="v1",
    )
    return replace(base, **kwargs)


def test_brand_vitality_growth_distribution_margin_and_inventory():
    result = brand_vitality(_period(), _period(
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
        cases_sold=D("100"), cases_returned=D("0"),
    ))
    assert result.net_cases == D("110")
    assert result.net_case_growth_pct == D("10")
    assert result.account_penetration_pct == D("50")
    assert result.repeat_account_pct == D("60")
    assert result.gross_margin_pct == D("30")
    assert result.weeks_of_supply == D("6")
    assert result.documented_inputs
    assert result.action_status.startswith("draft")


def test_missing_history_or_zero_velocity_never_generates_fake_growth():
    result = brand_vitality(_period(weekly_case_velocity=D("0"), source_revision=""))
    assert result.net_case_growth_pct is None
    assert result.weeks_of_supply is None
    assert not result.documented_inputs
    assert "missing_source_revision" in result.gaps
    assert result.action_status == "source_review_required"


def test_brand_undefined_account_and_return_denominators_are_explicit():
    result = brand_vitality(_period(cases_sold=D("0"), cases_returned=D("0"),
                                   active_accounts=0, eligible_accounts=0,
                                   repeat_accounts=0))
    assert result.account_penetration_pct is None
    assert result.repeat_account_pct is None
    assert result.return_rate_pct is None
    assert {"unavailable_account_penetration", "unavailable_repeat_account_rate",
            "unavailable_return_rate"}.issubset(result.gaps)


def test_cross_tenant_or_wrong_brand_comparison_is_denied():
    for prior in (_period(company_id="tenant-b"), _period(brand_id="brand-b")):
        with pytest.raises(ValueError):
            brand_vitality(_period(), prior)


def test_noncomparable_or_overlapping_period_growth_is_rejected():
    for previous in (
        _period(period_start=date(2026, 9, 1), period_end=date(2026, 9, 29)),
        _period(period_start=date(2026, 10, 1), period_end=date(2026, 10, 30)),
    ):
        with pytest.raises(ValueError):
            brand_vitality(_period(), previous)


def test_returns_and_account_counts_cannot_exceed_observation():
    for bad in (_period(cases_returned=D("121")), _period(repeat_accounts=21),
                _period(eligible_accounts=10)):
        with pytest.raises(ValueError):
            brand_vitality(bad)


def test_supplier_scorecard_uses_weighted_observed_rates():
    result = supplier_scorecard(_supplier())
    assert result.fill_rate_pct == D("90")
    assert result.on_time_pct == D("80")
    assert result.damage_rate_pct == D("2")
    assert result.composite_score == D("87.6")
    assert result.documented_inputs


def test_supplier_score_weights_support_explicit_industry_policy():
    result = supplier_scorecard(_supplier(), SupplierScoreWeights(
        fill=D("0.5"), on_time=D("0.3"), undamaged=D("0.2"),
    ))
    assert result.composite_score == D("88.6")
    for weights in (SupplierScoreWeights(D("0.5"), D("0.5"), D("0.5")),
                    SupplierScoreWeights(D("-0.1"), D("0.9"), D("0.2")),
                    SupplierScoreWeights(D("NaN"), D("0.6"), D("0.4"))):
        with pytest.raises(ValueError):
            supplier_scorecard(_supplier(), weights)


def test_supplier_without_source_revision_has_no_rankable_composite():
    result = supplier_scorecard(_supplier(source_revision=""))
    assert result.composite_score is None
    assert result.fill_rate_pct == D("90")  # arithmetic scenario, not verified
    assert result.action_status == "source_review_required"
    assert "missing_source_revision" in result.gaps
    assert "missing_supplier_denominator" not in result.gaps


def test_supplier_zero_denominators_remain_unknown():
    result = supplier_scorecard(_supplier(units_ordered=D("0"), units_filled=D("0"),
                                          units_delivered=D("0"), units_damaged=D("0"),
                                          on_time_shipments=0, received_shipments=0))
    assert result.composite_score is None
    assert "missing_supplier_denominator" in result.gaps


def test_supplier_inconsistent_counts_are_rejected():
    for bad in (_supplier(units_filled=D("1001")), _supplier(units_damaged=D("901")),
                _supplier(on_time_shipments=11)):
        with pytest.raises(ValueError):
            supplier_scorecard(bad)
