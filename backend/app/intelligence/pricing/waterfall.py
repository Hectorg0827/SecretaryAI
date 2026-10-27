"""Evidence-aware, Decimal-based, draft-only wholesale pricing calculations.

Inputs are externally supplied snapshots, not inferred from live connectors.
All outputs require authenticated human review before any price change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")
REQUIRED_COMPONENTS = frozenset({
    "product", "international_freight", "insurance", "duty",
    "customs_brokerage", "port_fees", "domestic_freight",
    "excise", "other_taxes",
})


def _nonnegative(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be a nonnegative finite Decimal")


def _percent(value: Decimal, label: str) -> None:
    _nonnegative(value, label)
    if value >= HUNDRED:
        raise ValueError(f"{label} must be below 100 percent")


@dataclass(frozen=True)
class LandedCostLine:
    component: str
    amount_per_case: Decimal
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class PriceScenario:
    company_id: str
    sku: str
    currency: str
    price_to_distributor: Decimal
    cost_lines: tuple[LandedCostLine, ...]
    discount_per_case: Decimal = ZERO
    returns_allowance_per_case: Decimal = ZERO
    commission_per_case: Decimal = ZERO
    marketing_per_case: Decimal = ZERO
    target_margin_pct: Decimal = Decimal("30")
    distributor_margin_pct: Decimal = Decimal("25")
    retailer_margin_pct: Decimal = Decimal("30")
    approved_list_price: Decimal | None = None


@dataclass(frozen=True)
class PriceWaterfall:
    landed_cost_per_case: Decimal
    net_revenue_per_case: Decimal
    gross_profit_per_case: Decimal
    gross_margin_pct: Decimal
    contribution_per_case: Decimal
    contribution_margin_pct: Decimal
    estimated_distributor_to_retailer: Decimal
    estimated_retailer_to_consumer: Decimal
    complete_cost_components: bool
    documented_cost_components: bool
    exception_reasons: tuple[str, ...]
    approval_required: bool = True
    status: str = "draft_unverified"


def calculate_price_waterfall(data: PriceScenario) -> PriceWaterfall:
    """Calculate a scenario, never grant price authority or claim verified cost."""
    if not data.company_id or not data.sku or len(data.currency) != 3:
        raise ValueError("Tenant, SKU and three-letter currency are required")
    _nonnegative(data.price_to_distributor, "price_to_distributor")
    if data.price_to_distributor == ZERO:
        raise ValueError("Distributor invoice/list price must be positive Decimal")
    for field_name in ("discount_per_case", "returns_allowance_per_case", "commission_per_case", "marketing_per_case"):
        _nonnegative(getattr(data, field_name), field_name)
    for field_name in ("target_margin_pct", "distributor_margin_pct", "retailer_margin_pct"):
        _percent(getattr(data, field_name), field_name)
    if data.approved_list_price is not None:
        _nonnegative(data.approved_list_price, "approved_list_price")

    seen: set[str] = set()
    for line in data.cost_lines:
        if not line.component or line.component in seen:
            raise ValueError("Cost component names must be nonempty and unique")
        seen.add(line.component)
        _nonnegative(line.amount_per_case, f"{line.component} cost")

    complete = REQUIRED_COMPONENTS.issubset(seen)
    documented = complete and all(line.source_record_id and line.source_revision for line in data.cost_lines)
    landed = sum((line.amount_per_case for line in data.cost_lines), ZERO)
    net = data.price_to_distributor - data.discount_per_case - data.returns_allowance_per_case
    if net <= 0:
        raise ValueError("Discounts and allowances consume all net revenue")
    gross_profit = net - landed
    contribution = gross_profit - data.commission_per_case - data.marketing_per_case

    # Gross-margin (not markup) percentages. The downstream chain is a
    # scenario and excludes jurisdiction-specific taxes/fees at retail.
    distributor_to_retailer = net / (Decimal("1") - data.distributor_margin_pct / HUNDRED)
    retailer_to_consumer = distributor_to_retailer / (Decimal("1") - data.retailer_margin_pct / HUNDRED)
    margin_pct = HUNDRED * gross_profit / net
    reasons = []
    if not complete:
        reasons.append("missing_cost_categories")
    if not documented:
        reasons.append("unreconciled_cost_source_revision")
    if margin_pct < data.target_margin_pct:
        reasons.append("below_target_margin")
    if data.approved_list_price is not None and data.price_to_distributor != data.approved_list_price:
        reasons.append("price_exception_requires_approval")
    if data.approved_list_price is None:
        reasons.append("missing_approved_price_reference")
    if contribution < 0:
        reasons.append("negative_unit_contribution")

    return PriceWaterfall(
        landed_cost_per_case=landed,
        net_revenue_per_case=net,
        gross_profit_per_case=gross_profit,
        gross_margin_pct=margin_pct,
        contribution_per_case=contribution,
        contribution_margin_pct=HUNDRED * contribution / net,
        estimated_distributor_to_retailer=distributor_to_retailer,
        estimated_retailer_to_consumer=retailer_to_consumer,
        complete_cost_components=complete,
        documented_cost_components=bool(documented),
        exception_reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class PromotionScenario:
    company_id: str
    sku: str
    baseline_cases: Decimal
    incremental_cases: Decimal
    normal_contribution_per_case: Decimal
    promotion_contribution_per_case: Decimal
    promotion_spend: Decimal
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class PromotionROI:
    incremental_profit: Decimal
    roi_ratio: Decimal | None
    break_even_incremental_cases: Decimal | None
    documented_inputs: bool
    approval_required: bool = True


def calculate_promotion_roi(data: PromotionScenario) -> PromotionROI:
    """Compare promotion to no-promotion baseline, including discounted base sales."""
    if not data.company_id or not data.sku:
        raise ValueError("Tenant and SKU are required for promotion analysis")
    for field_name in ("baseline_cases", "incremental_cases", "promotion_spend"):
        _nonnegative(getattr(data, field_name), field_name)
    for field_name in ("normal_contribution_per_case", "promotion_contribution_per_case"):
        value = getattr(data, field_name)
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"{field_name} must be finite Decimal")

    baseline_profit = data.baseline_cases * data.normal_contribution_per_case
    promoted_profit = (
        (data.baseline_cases + data.incremental_cases) * data.promotion_contribution_per_case
        - data.promotion_spend
    )
    incremental_profit = promoted_profit - baseline_profit
    roi = incremental_profit / data.promotion_spend if data.promotion_spend else None
    break_even = (
        (data.promotion_spend + baseline_profit - data.baseline_cases * data.promotion_contribution_per_case)
        / data.promotion_contribution_per_case
        if data.promotion_contribution_per_case > 0 else None
    )
    return PromotionROI(incremental_profit=incremental_profit, roi_ratio=roi,
                        break_even_incremental_cases=max(ZERO, break_even) if break_even is not None else None,
                        documented_inputs=bool(data.source_record_id and data.source_revision))
