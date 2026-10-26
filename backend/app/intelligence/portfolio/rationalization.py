"""Portfolio decision support, with explicit SKU dependencies and no deletes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class PortfolioPolicy:
    min_contribution_margin_pct: Decimal
    max_weeks_of_supply: Decimal
    max_stock_age_days: int


@dataclass(frozen=True)
class PortfolioSKU:
    company_id: str
    sku: str
    net_sales: Decimal
    total_contribution: Decimal
    stock_on_hand_cases: Decimal
    weekly_case_velocity: Decimal
    stock_age_days: int
    landed_cost_per_case: Decimal
    required_by_skus: tuple[str, ...] = ()
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class PortfolioAssessment:
    sku: str
    contribution_margin_pct: Decimal | None
    weeks_of_supply: Decimal | None
    working_capital_tied: Decimal
    status: str
    reasons: tuple[str, ...]
    required_by_skus: tuple[str, ...]
    approval_required: bool = True


def _numeric(value: Decimal, label: str, *, allow_negative: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or (value < 0 and not allow_negative):
        raise ValueError(f"Invalid {label}")


def assess_portfolio(
    company_id: str, skus: tuple[PortfolioSKU, ...], policy: PortfolioPolicy,
) -> tuple[PortfolioAssessment, ...]:
    """Return review suggestions; no automatic discontinuation or supplier writes."""
    if not company_id:
        raise ValueError("Tenant required")
    _numeric(policy.min_contribution_margin_pct, "min margin", allow_negative=True)
    _numeric(policy.max_weeks_of_supply, "weeks threshold")
    if not isinstance(policy.max_stock_age_days, int) or policy.max_stock_age_days < 0:
        raise ValueError("Invalid stock age threshold")
    seen: set[str] = set()
    results: list[PortfolioAssessment] = []
    for item in skus:
        if item.company_id != company_id or not item.sku or item.sku in seen:
            raise ValueError("Tenant mismatch, missing or duplicate SKU")
        seen.add(item.sku)
        for key in ("net_sales", "stock_on_hand_cases", "weekly_case_velocity", "landed_cost_per_case"):
            _numeric(getattr(item, key), key)
        _numeric(item.total_contribution, "contribution", allow_negative=True)
        if not isinstance(item.stock_age_days, int) or item.stock_age_days < 0:
            raise ValueError("Stock age must be nonnegative integer")
        if item.sku in item.required_by_skus:
            raise ValueError("SKU cannot depend on itself")

        contribution_pct = HUNDRED * item.total_contribution / item.net_sales if item.net_sales else None
        weeks = item.stock_on_hand_cases / item.weekly_case_velocity if item.weekly_case_velocity else None
        risk_reasons = []
        gaps = []
        if not item.source_record_id or not item.source_revision:
            gaps.append("missing_source_revision")
        if contribution_pct is None:
            gaps.append("missing_net_sales")
        if weeks is None:
            gaps.append("missing_velocity")
        if contribution_pct is not None and contribution_pct < policy.min_contribution_margin_pct:
            risk_reasons.append("low_contribution_margin")
        if weeks is not None and weeks > policy.max_weeks_of_supply:
            risk_reasons.append("excess_weeks_of_supply")
        if item.stock_age_days > policy.max_stock_age_days:
            risk_reasons.append("aged_inventory")

        if gaps:
            status = "data_review_required"
        elif risk_reasons and item.required_by_skus:
            status = "dependency_review_required"
            risk_reasons.append("dependent_skus_block_rationalization")
        elif risk_reasons:
            status = "rationalization_candidate_for_review"
        else:
            status = "maintain_or_invest_candidate"
        results.append(PortfolioAssessment(
            sku=item.sku,
            contribution_margin_pct=contribution_pct,
            weeks_of_supply=weeks,
            working_capital_tied=item.stock_on_hand_cases * item.landed_cost_per_case,
            status=status,
            reasons=tuple(gaps + risk_reasons),
            required_by_skus=item.required_by_skus,
        ))
    return tuple(results)
