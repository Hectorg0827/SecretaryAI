"""Seven-domain synthetic rehearsal: no DB, provider, route or external writes.

This test intentionally checks domain *composition*, not Gate A authentication,
source verification, operational shipment approval or production integration.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from app.intelligence.brands.scorecards import BrandPeriod, brand_vitality
from app.intelligence.compliance.evidence_gate import (
    ReadinessStatus, assess_compliance_readiness,
)
from app.intelligence.compliance.models import ComplianceCheckResult
from app.intelligence.growth.opportunity_taxonomy import (
    GrowthSignal, PolicyScope, classify_growth_signal,
)
from app.intelligence.hr.onboarding import (
    HRRequirement, HRTaskAccess, assess_hr_requirements, private_hr_briefing,
)
from app.intelligence.portfolio.rationalization import (
    PortfolioPolicy, PortfolioSKU, assess_portfolio,
)
from app.intelligence.pricing.waterfall import (
    REQUIRED_COMPONENTS, LandedCostLine, PriceScenario, calculate_price_waterfall,
)
from app.intelligence.products.concepts import (
    ConceptScenario, LaunchObservation, assess_concept, track_launch,
)
from app.intelligence.wholesale_distribution.dynamic_pricing import (
    DynamicPricingEngine, PricingTier,
)


TENANT = "synthetic-tenant"
SKU = "synthetic-sku"
NOW = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)


def test_synthetic_commercial_launch_review_remains_non_operational():
    """All seven advisory domains can review one fictitious launch safely."""
    brand = brand_vitality(BrandPeriod(
        company_id=TENANT, brand_id="synthetic-brand",
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 26),
        cases_sold=D("100"), cases_returned=D("3"),
        active_accounts=15, eligible_accounts=30, repeat_accounts=6,
        net_sales=D("3000"), gross_profit=D("900"),
        stock_on_hand_cases=D("0"), weekly_case_velocity=D("5"),
        source_record_id="synthetic-sales", source_revision="rev-1",
    ))
    assert brand.net_cases == D("97")
    assert brand.weeks_of_supply == D("0")
    assert brand.action_status == "draft_requires_review"

    concept = assess_concept(ConceptScenario(
        company_id=TENANT, concept_id="synthetic-concept",
        observed_category_demand_cases=D("1000"),
        existing_served_category_cases=D("700"),
        proposed_capture_pct=D("20"), wholesale_price_per_case=D("30"),
        landed_cost_per_case=D("10"), variable_launch_cost_per_case=D("2"),
        one_time_launch_cost=D("100"), cannibalization_pct=D("10"),
        incumbent_contribution_per_case=D("8"),
        assumption_revision="synthetic-assumptions-v1",
        demand_source_record_id="synthetic-market-study",
    ))
    assert concept.projected_new_product_cases == D("60")
    assert concept.action_status == "draft_only"

    # The launch's apparent numbers cannot be treated as observed KPIs while
    # the input adapter reports partial source quality.
    launch = track_launch(LaunchObservation(
        company_id=TENANT, concept_id="synthetic-concept",
        planned_cases_to_date=D("60"), observed_net_cases_to_date=D("45"),
        net_revenue_to_date=D("1000"), contribution_to_date=D("200"),
        active_accounts=10, repeat_accounts=4,
        source_record_id="synthetic-launch", source_revision="rev-1",
        source_quality="partial",
    ))
    assert launch.volume_attainment_pct is None
    assert launch.realized_contribution_margin_pct is None
    assert "launch_source_unverified" in launch.gaps

    portfolio = assess_portfolio(TENANT, (PortfolioSKU(
        company_id=TENANT, sku=SKU, net_sales=D("3000"),
        total_contribution=D("600"), stock_on_hand_cases=D("0"),
        weekly_case_velocity=D("5"), stock_age_days=20,
        landed_cost_per_case=D("10"),
        source_record_id="synthetic-stock", source_revision="rev-1",
    ),), PortfolioPolicy(D("15"), D("20"), 180))[0]
    assert portfolio.status == "supply_review_required"
    assert portfolio.approval_required

    pricing = calculate_price_waterfall(PriceScenario(
        company_id=TENANT, sku=SKU, currency="USD",
        price_to_distributor=D("30"),
        cost_lines=tuple(LandedCostLine(
            component=name, amount_per_case=D("1"),
            source_record_id=f"synthetic-cost-{name}", source_revision="rev-1",
        ) for name in sorted(REQUIRED_COMPONENTS)),
    ))
    assert pricing.complete_cost_components
    assert pricing.documented_cost_components
    assert pricing.approval_required and pricing.status == "draft_unverified"

    dynamic = DynamicPricingEngine(TENANT)
    estimated = dynamic.estimate_elasticity("independent", "wine", [])
    suggestion = dynamic.recommend_price(
        sku=SKU, segment="independent", pricing_tier=PricingTier.TIER_3,
        current_price=30.0, unit_cost=10.0, annual_units=1000.0,
        elasticity=estimated,
    )
    assert suggestion.recommended_price == suggestion.current_price
    assert suggestion.to_dict()["requires_human_approval"] is True
    assert suggestion.to_dict()["source_status"] == "unverified_advisory"

    compliance = assess_compliance_readiness(
        TENANT, ComplianceCheckResult(
            approved=True, product_id=SKU, state_code="NY", quantity_cases=10,
        ), [], as_of=date(2026, 10, 26),
    )
    assert compliance.status is ReadinessStatus.SOURCE_REVIEW_REQUIRED
    assert compliance.authorized_to_ship is False

    requirements = assess_hr_requirements(TENANT, (
        HRRequirement(TENANT, "opaque-staff-a", "training", "training",
                      date(2026, 10, 25), source_record_id="synthetic-task-a",
                      source_revision="rev-1"),
        HRRequirement(TENANT, "opaque-staff-b", "training", "training",
                      date(2026, 10, 28), source_record_id="synthetic-task-b",
                      source_revision="rev-1"),
    ), as_of=date(2026, 10, 26))
    private = private_hr_briefing(TENANT, requirements, access=HRTaskAccess(
        TENANT, "view_hr", frozenset({"opaque-staff-a"}), "synthetic-policy-decision",
    ))
    assert private.total == 1 and private.overdue == 1
    assert {item.employee_ref for item in private.detailed_tasks} == {"opaque-staff-a"}

    alert = classify_growth_signal(GrowthSignal(
        company_id=TENANT, entity_ref=SKU,
        signal_code="portfolio_stockout_with_demand", department="portfolio",
        metric_value=D("0"), source_quality="partial",
        source_record_id="synthetic-stock", source_revision="rev-1",
        observed_at=NOW - timedelta(seconds=30),
        source_system="synthetic-inventory", source_fetched_at=NOW,
        source_confidence=60,
    ), scope=PolicyScope(
        TENANT, "back_office", frozenset({SKU}), "synthetic-policy-decision",
    ), as_of=NOW, max_age_seconds=300)
    assert alert.section == "red_alerts"
    assert alert.status == "source_unavailable"
    assert alert.display_metric is None and alert.source_confidence == 0
    assert alert.approval_required and not alert.execution_claimed


def test_rehearsal_rejects_cross_tenant_portfolio_and_unauthorized_alert_scope():
    with pytest.raises(ValueError, match="Tenant mismatch"):
        assess_portfolio(TENANT, (PortfolioSKU(
            company_id="other-tenant", sku=SKU, net_sales=D("1"),
            total_contribution=D("1"), stock_on_hand_cases=D("1"),
            weekly_case_velocity=D("1"), stock_age_days=1,
            landed_cost_per_case=D("1"),
        ),), PortfolioPolicy(D("15"), D("20"), 180))

    signal = GrowthSignal(
        TENANT, SKU, "margin_breach", "pricing", D("5"), "verified",
        "synthetic-price", "rev-1", NOW, "synthetic-pricing", NOW, 90,
    )
    with pytest.raises(PermissionError):
        classify_growth_signal(signal, scope=PolicyScope(
            TENANT, "sales_rep", frozenset({SKU}), "synthetic-policy-decision",
        ), as_of=NOW, max_age_seconds=300)
