"""Draft-only product whitespace, launch economics and cannibalization model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _nonnegative(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"Invalid nonnegative {label}")


def _ratio(value: Decimal, label: str) -> None:
    _nonnegative(value, label)
    if value > HUNDRED:
        raise ValueError(f"{label} cannot exceed 100%")


@dataclass(frozen=True)
class ConceptScenario:
    company_id: str
    concept_id: str
    observed_category_demand_cases: Decimal
    existing_served_category_cases: Decimal
    proposed_capture_pct: Decimal
    wholesale_price_per_case: Decimal
    landed_cost_per_case: Decimal
    variable_launch_cost_per_case: Decimal
    one_time_launch_cost: Decimal
    cannibalization_pct: Decimal
    incumbent_contribution_per_case: Decimal
    assumption_revision: str = ""
    demand_source_record_id: str = ""


@dataclass(frozen=True)
class ConceptAssessment:
    addressable_whitespace_cases: Decimal
    projected_new_product_cases: Decimal
    cannibalized_incumbent_cases: Decimal
    gross_new_product_contribution: Decimal
    incumbent_contribution_lost: Decimal
    incremental_first_year_profit_after_launch: Decimal
    estimated_payback_months: Decimal | None
    documented_assumptions: bool
    risk_flags: tuple[str, ...]
    action_status: str = "draft_only"


def assess_concept(data: ConceptScenario) -> ConceptAssessment:
    if not data.company_id or not data.concept_id:
        raise ValueError("Tenant and concept required")
    for label in (
        "observed_category_demand_cases", "existing_served_category_cases",
        "wholesale_price_per_case", "landed_cost_per_case",
        "variable_launch_cost_per_case", "one_time_launch_cost",
        "incumbent_contribution_per_case",
    ):
        _nonnegative(getattr(data, label), label)
    _ratio(data.proposed_capture_pct, "capture percentage")
    _ratio(data.cannibalization_pct, "cannibalization percentage")
    if data.existing_served_category_cases > data.observed_category_demand_cases:
        raise ValueError("Served category volume exceeds total observed demand")

    whitespace = data.observed_category_demand_cases - data.existing_served_category_cases
    projected = whitespace * data.proposed_capture_pct / HUNDRED
    cannibalized = projected * data.cannibalization_pct / HUNDRED
    unit_contribution = data.wholesale_price_per_case - data.landed_cost_per_case - data.variable_launch_cost_per_case
    gross_contribution = projected * unit_contribution
    incumbent_lost = cannibalized * data.incumbent_contribution_per_case
    annual_before_launch = gross_contribution - incumbent_lost
    profit = annual_before_launch - data.one_time_launch_cost
    payback = data.one_time_launch_cost * Decimal("12") / annual_before_launch if annual_before_launch > 0 else None
    documented = bool(data.assumption_revision and data.demand_source_record_id)
    flags = []
    if not documented:
        flags.append("unverified_market_or_assumptions")
    if unit_contribution <= 0:
        flags.append("nonpositive_new_product_unit_contribution")
    if profit <= 0:
        flags.append("first_year_profit_not_positive")
    if data.cannibalization_pct:
        flags.append("cannibalization_requires_portfolio_review")
    return ConceptAssessment(whitespace, projected, cannibalized, gross_contribution,
                             incumbent_lost, profit, payback, documented, tuple(flags))


@dataclass(frozen=True)
class LaunchObservation:
    company_id: str
    concept_id: str
    planned_cases_to_date: Decimal
    observed_net_cases_to_date: Decimal
    net_revenue_to_date: Decimal
    contribution_to_date: Decimal
    active_accounts: int
    repeat_accounts: int
    source_record_id: str = ""
    source_revision: str = ""
    # The calling, authenticated source adapter must establish this quality;
    # a record ID and revision alone do not prove the observations are valid.
    source_quality: str = "unavailable"


@dataclass(frozen=True)
class LaunchTracking:
    volume_attainment_pct: Decimal | None
    realized_contribution_margin_pct: Decimal | None
    repeat_account_rate_pct: Decimal | None
    documented_inputs: bool
    gaps: tuple[str, ...]
    action_status: str = "draft_requires_review"


def track_launch(data: LaunchObservation) -> LaunchTracking:
    """Calculate read-only launch KPIs without presenting unverified actuals.

    The adapter must authenticate tenant/source lineage and derive
    ``source_quality``; this pure function cannot certify external evidence.
    """
    if not data.company_id or not data.concept_id:
        raise ValueError("Tenant and concept required")
    for label in ("planned_cases_to_date", "observed_net_cases_to_date", "net_revenue_to_date"):
        _nonnegative(getattr(data, label), label)
    if not isinstance(data.contribution_to_date, Decimal) or not data.contribution_to_date.is_finite():
        raise ValueError("Invalid contribution")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (data.active_accounts, data.repeat_accounts)):
        raise ValueError("Account counts must be nonnegative integers")
    if data.repeat_accounts > data.active_accounts:
        raise ValueError("Repeat accounts exceed active accounts")
    if data.source_quality not in {"verified", "partial", "unavailable"}:
        raise ValueError("Unknown launch source-quality status")
    documented = bool(
        data.source_record_id and data.source_revision
        and data.source_quality == "verified"
    )
    gaps = []
    if not data.source_record_id or not data.source_revision:
        gaps.append("missing_observed_source_revision")
    if data.source_quality != "verified":
        gaps.append("launch_source_unverified")
    if data.planned_cases_to_date == ZERO:
        gaps.append("missing_launch_plan")
    if data.net_revenue_to_date == ZERO:
        gaps.append("missing_revenue")
    if data.active_accounts == 0:
        gaps.append("missing_active_accounts")
    return LaunchTracking(
        volume_attainment_pct=HUNDRED * data.observed_net_cases_to_date / data.planned_cases_to_date if documented and data.planned_cases_to_date else None,
        realized_contribution_margin_pct=HUNDRED * data.contribution_to_date / data.net_revenue_to_date if documented and data.net_revenue_to_date else None,
        repeat_account_rate_pct=HUNDRED * data.repeat_accounts / data.active_accounts if documented and data.active_accounts else None,
        documented_inputs=documented,
        gaps=tuple(gaps),
    )
