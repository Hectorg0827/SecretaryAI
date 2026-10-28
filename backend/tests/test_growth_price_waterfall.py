"""Pure-domain pricing contract tests using synthetic, non-customer inputs."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.intelligence.pricing.waterfall import (
    LandedCostLine,
    PriceScenario,
    PromotionScenario,
    REQUIRED_COMPONENTS,
    calculate_price_waterfall,
    calculate_promotion_roi,
)


AMOUNTS = {
    "product": "16", "international_freight": "2", "insurance": "0.5",
    "duty": "1", "customs_brokerage": "0.5", "port_fees": "0.5",
    "domestic_freight": "0.5", "excise": "1", "other_taxes": "0",
}


def _scenario():
    lines = tuple(
        LandedCostLine(name, D(AMOUNTS[name]), source_record_id=f"synthetic-{name}", source_revision="v1")
        for name in sorted(REQUIRED_COMPONENTS)
    )
    return PriceScenario("synthetic-tenant", "synthetic-sku", "USD", D("30"),
                         lines, discount_per_case=D("2"), commission_per_case=D("1"),
                         marketing_per_case=D("1"), approved_list_price=D("30"))


def test_reconciled_waterfall_uses_gross_margin_not_markup():
    result = calculate_price_waterfall(_scenario())
    assert result.landed_cost_per_case == D("22")
    assert result.net_revenue_per_case == D("28")
    assert result.gross_profit_per_case == D("6")
    assert result.contribution_per_case == D("4")
    assert result.gross_margin_pct == D("600") / D("28")
    assert result.estimated_distributor_to_retailer == D("28") / D("0.75")
    assert result.estimated_retailer_to_consumer == (D("28") / D("0.75")) / D("0.7")
    assert result.complete_cost_components
    assert result.documented_cost_components
    assert result.approval_required
    assert "below_target_margin" in result.exception_reasons


def test_missing_duty_or_evidence_never_counts_as_complete_cost():
    baseline = _scenario()
    missing = replace(baseline, cost_lines=tuple(x for x in baseline.cost_lines if x.component != "duty"))
    result = calculate_price_waterfall(missing)
    assert not result.complete_cost_components
    assert "missing_cost_categories" in result.exception_reasons

    unreferenced = replace(baseline, cost_lines=(replace(baseline.cost_lines[0], source_revision=""),) + baseline.cost_lines[1:])
    result = calculate_price_waterfall(unreferenced)
    assert result.complete_cost_components
    assert not result.documented_cost_components
    assert "unreconciled_cost_source_revision" in result.exception_reasons


def test_price_exception_and_negative_contribution_require_review():
    scenario = replace(_scenario(), price_to_distributor=D("24"), discount_per_case=D("2"),
                       commission_per_case=D("4"), marketing_per_case=D("2"))
    result = calculate_price_waterfall(scenario)
    assert "price_exception_requires_approval" in result.exception_reasons
    assert "negative_unit_contribution" in result.exception_reasons
    assert result.approval_required


def test_duplicate_component_rejected_to_prevent_double_count():
    s = _scenario()
    with pytest.raises(ValueError):
        calculate_price_waterfall(replace(s, cost_lines=s.cost_lines + s.cost_lines[:1]))


@pytest.mark.parametrize("mutation", [
    {"price_to_distributor": D("0")},
    {"price_to_distributor": D("NaN")},
    {"discount_per_case": D("30")},
    {"target_margin_pct": D("100")},
    {"commission_per_case": D("-1")},
])
def test_invalid_financial_inputs_do_not_yield_price_scenarios(mutation):
    with pytest.raises(ValueError):
        calculate_price_waterfall(replace(_scenario(), **mutation))


def test_promotion_roi_accounts_for_discounted_baseline_sales():
    result = calculate_promotion_roi(PromotionScenario(
        company_id="synthetic-tenant", sku="synthetic-sku",
        baseline_cases=D("100"), incremental_cases=D("20"),
        normal_contribution_per_case=D("6"), promotion_contribution_per_case=D("4"),
        promotion_spend=D("200"), source_record_id="synthetic-orders", source_revision="v1",
    ))
    assert result.incremental_profit == D("-320")
    assert result.roi_ratio == D("-1.6")
    assert result.break_even_incremental_cases == D("100")
    assert result.approval_required
    assert result.documented_inputs


def test_zero_spend_roi_is_undefined_not_division_error():
    result = calculate_promotion_roi(PromotionScenario("synthetic-tenant", "sku", D("100"), D("10"), D("5"), D("5"), D("0")))
    assert result.roi_ratio is None
    assert not result.documented_inputs


def test_negative_promotion_quantity_rejected():
    with pytest.raises(ValueError):
        calculate_promotion_roi(PromotionScenario("synthetic-tenant", "sku", D("50"), D("-1"), D("5"), D("4"), D("20")))


def test_promotion_tenant_scope_required():
    with pytest.raises(ValueError):
        calculate_promotion_roi(PromotionScenario("", "sku", D("50"), D("10"), D("5"), D("4"), D("20")))
