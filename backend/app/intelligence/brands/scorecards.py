"""Deterministic, tenant-scoped brand vitality and supplier performance drafts.

These are calculations over supplied observations, not live integration or
approved supplier actions. Evidence IDs here are pointers, not trusted proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _nonnegative(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be nonnegative finite Decimal")


def _count(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")


@dataclass(frozen=True)
class BrandPeriod:
    company_id: str
    brand_id: str
    period_start: date
    period_end: date
    cases_sold: Decimal
    cases_returned: Decimal
    active_accounts: int
    eligible_accounts: int
    repeat_accounts: int
    net_sales: Decimal
    gross_profit: Decimal
    stock_on_hand_cases: Decimal
    weekly_case_velocity: Decimal
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class BrandVitals:
    net_cases: Decimal
    net_case_growth_pct: Decimal | None
    account_penetration_pct: Decimal | None
    repeat_account_pct: Decimal | None
    return_rate_pct: Decimal | None
    gross_margin_pct: Decimal | None
    weeks_of_supply: Decimal | None
    documented_inputs: bool
    gaps: tuple[str, ...]
    action_status: str = "draft_requires_review"


def brand_vitality(current: BrandPeriod, previous: BrandPeriod | None = None) -> BrandVitals:
    if not current.company_id or not current.brand_id:
        raise ValueError("Company and brand ID are required")
    if previous and (previous.company_id != current.company_id or previous.brand_id != current.brand_id):
        raise ValueError("Cannot combine different tenant or brand observations")
    if current.period_end < current.period_start:
        raise ValueError("Current period dates are invalid")
    if previous:
        if previous.period_end < previous.period_start:
            raise ValueError("Previous period dates are invalid")
        if (current.period_end - current.period_start) != (previous.period_end - previous.period_start):
            raise ValueError("Growth comparison requires equivalent observation periods")
        if previous.period_end >= current.period_start:
            raise ValueError("Prior and current periods must not overlap")
    for period in (current, previous):
        if period is None:
            continue
        for label in ("cases_sold", "cases_returned", "net_sales", "stock_on_hand_cases", "weekly_case_velocity"):
            _nonnegative(getattr(period, label), label)
        if not isinstance(period.gross_profit, Decimal) or not period.gross_profit.is_finite():
            raise ValueError("Gross profit must be finite Decimal")
        for label in ("active_accounts", "eligible_accounts", "repeat_accounts"):
            _count(getattr(period, label), label)
        if period.cases_returned > period.cases_sold:
            raise ValueError("Returned cases exceed observed cases sold")
        if period.active_accounts > period.eligible_accounts or period.repeat_accounts > period.active_accounts:
            raise ValueError("Account counts are internally inconsistent")

    net_cases = current.cases_sold - current.cases_returned
    prev_net = previous.cases_sold - previous.cases_returned if previous else ZERO
    growth = HUNDRED * (net_cases - prev_net) / prev_net if prev_net else None
    documented = bool(current.source_record_id and current.source_revision)
    if previous:
        documented = documented and bool(previous.source_record_id and previous.source_revision)
    gaps = []
    if not documented:
        gaps.append("missing_source_revision")
    if growth is None:
        gaps.append("insufficient_comparable_history")
    if current.net_sales == ZERO:
        gaps.append("unavailable_net_sales_margin")
    if current.weekly_case_velocity == ZERO:
        gaps.append("unavailable_weeks_of_supply")
    if current.eligible_accounts == 0:
        gaps.append("unavailable_account_penetration")
    if current.active_accounts == 0:
        gaps.append("unavailable_repeat_account_rate")
    if current.cases_sold == ZERO:
        gaps.append("unavailable_return_rate")

    return BrandVitals(
        net_cases=net_cases,
        net_case_growth_pct=growth,
        account_penetration_pct=HUNDRED * current.active_accounts / current.eligible_accounts if current.eligible_accounts else None,
        repeat_account_pct=HUNDRED * current.repeat_accounts / current.active_accounts if current.active_accounts else None,
        return_rate_pct=HUNDRED * current.cases_returned / current.cases_sold if current.cases_sold else None,
        gross_margin_pct=HUNDRED * current.gross_profit / current.net_sales if current.net_sales else None,
        weeks_of_supply=current.stock_on_hand_cases / current.weekly_case_velocity if current.weekly_case_velocity else None,
        documented_inputs=bool(documented),
        gaps=tuple(gaps),
        action_status="draft_requires_review" if documented else "source_review_required",
    )


@dataclass(frozen=True)
class SupplierObservation:
    company_id: str
    supplier_id: str
    units_ordered: Decimal
    units_filled: Decimal
    units_delivered: Decimal
    units_damaged: Decimal
    on_time_shipments: int
    received_shipments: int
    source_record_id: str = ""
    source_revision: str = ""


@dataclass(frozen=True)
class SupplierScorecard:
    fill_rate_pct: Decimal | None
    on_time_pct: Decimal | None
    damage_rate_pct: Decimal | None
    composite_score: Decimal | None
    documented_inputs: bool
    gaps: tuple[str, ...]
    action_status: str = "draft_requires_review"


@dataclass(frozen=True)
class SupplierScoreWeights:
    """Tenant/industry-provided score weights, not an authorization policy."""

    fill: Decimal = Decimal("0.4")
    on_time: Decimal = Decimal("0.4")
    undamaged: Decimal = Decimal("0.2")


def supplier_scorecard(
    data: SupplierObservation,
    weights: SupplierScoreWeights = SupplierScoreWeights(),
) -> SupplierScorecard:
    if not data.company_id or not data.supplier_id:
        raise ValueError("Company and supplier ID required")
    if not isinstance(weights, SupplierScoreWeights):
        raise ValueError("Supplier weights must use the typed policy contract")
    for label in ("fill", "on_time", "undamaged"):
        _nonnegative(getattr(weights, label), f"{label} score weight")
    if weights.fill + weights.on_time + weights.undamaged != Decimal("1"):
        raise ValueError("Supplier score weights must sum to exactly one")
    for label in ("units_ordered", "units_filled", "units_delivered", "units_damaged"):
        _nonnegative(getattr(data, label), label)
    _count(data.on_time_shipments, "on_time_shipments")
    _count(data.received_shipments, "received_shipments")
    if data.units_filled > data.units_ordered or data.units_damaged > data.units_delivered:
        raise ValueError("Units are internally inconsistent")
    if data.on_time_shipments > data.received_shipments:
        raise ValueError("On-time count exceeds received shipments")
    fill = HUNDRED * data.units_filled / data.units_ordered if data.units_ordered else None
    on_time = HUNDRED * data.on_time_shipments / data.received_shipments if data.received_shipments else None
    damage = HUNDRED * data.units_damaged / data.units_delivered if data.units_delivered else None
    score = (fill * weights.fill + on_time * weights.on_time + (HUNDRED - damage) * weights.undamaged
             if fill is not None and on_time is not None and damage is not None else None)
    gaps = []
    documented = bool(data.source_record_id and data.source_revision)
    if score is None:
        gaps.append("missing_supplier_denominator")
    if not documented:
        gaps.append("missing_source_revision")
        # An undocumented scalar must not be surfaced as a valid supplier
        # ranking even when all supplied arithmetic denominators exist.
        score = None
    return SupplierScorecard(fill, on_time, damage, score, documented, tuple(gaps),
                             "draft_requires_review" if documented else "source_review_required")
