"""Read-only opportunity taxonomy for the six-section Gate A briefing.

This is a *projection helper*, not authorization or action execution. The
calling API must authenticate the viewer, derive its PolicyScope on the server
and validate the policy decision ID. No consumer may treat a supplied scope
object alone as proof of permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class SignalDefinition:
    department: str
    section: str
    next_action_code: str
    allowed_roles: frozenset[str]


MANAGEMENT = frozenset({"owner", "manager"})
MANAGEMENT_OPERATIONS = frozenset({"owner", "manager", "back_office"})
SIGNAL_TAXONOMY: dict[str, SignalDefinition] = {
    "compliance_renewal_due": SignalDefinition("compliance", "red_alerts", "review_renewal_evidence", MANAGEMENT_OPERATIONS),
    "compliance_source_stale": SignalDefinition("compliance", "yellow_alerts", "verify_regulatory_source", MANAGEMENT_OPERATIONS),
    "brand_decline": SignalDefinition("brand", "yellow_alerts", "review_brand_vitality", MANAGEMENT),
    "supplier_service_decline": SignalDefinition("brand", "yellow_alerts", "review_supplier_scorecard", MANAGEMENT),
    "brand_growth": SignalDefinition("brand", "scoreboard", "review_growth_evidence", MANAGEMENT),
    "concept_cannibalization_risk": SignalDefinition("product_development", "yellow_alerts", "review_concept_assumptions", MANAGEMENT),
    "launch_underperformance": SignalDefinition("product_development", "yellow_alerts", "review_launch_cohort", MANAGEMENT),
    "portfolio_stock_at_risk": SignalDefinition("portfolio", "yellow_alerts", "review_portfolio_dependency", MANAGEMENT),
    "portfolio_stockout_with_demand": SignalDefinition("portfolio", "red_alerts", "review_supply_availability", MANAGEMENT_OPERATIONS),
    "portfolio_contribution_gap": SignalDefinition("portfolio", "opportunities", "review_portfolio_contribution", MANAGEMENT),
    "margin_breach": SignalDefinition("pricing", "red_alerts", "review_margin_waterfall", MANAGEMENT),
    "promotion_roi_negative": SignalDefinition("pricing", "yellow_alerts", "review_promotion_baseline", MANAGEMENT),
    "price_exception": SignalDefinition("dynamic_pricing", "yellow_alerts", "request_price_approval", MANAGEMENT),
    "insufficient_elasticity": SignalDefinition("dynamic_pricing", "yellow_alerts", "validate_price_history", MANAGEMENT),
    # The canonical RBAC has not yet added an HR role. Consequently, no
    # existing general role (including owner) is implicitly allowed HR detail.
    "hr_training_due": SignalDefinition("hr", "red_alerts", "review_private_hr_task", frozenset({"hr_admin"})),
}


@dataclass(frozen=True)
class GrowthSignal:
    company_id: str
    entity_ref: str
    signal_code: str
    department: str
    metric_value: Decimal | None
    source_quality: str  # verified | partial | unavailable
    source_record_id: str
    source_revision: str
    observed_at: datetime


@dataclass(frozen=True)
class PolicyScope:
    company_id: str
    effective_role: str
    permitted_entity_refs: frozenset[str]
    policy_decision_id: str


@dataclass(frozen=True)
class ClassifiedGrowthSignal:
    department: str
    signal_code: str
    entity_ref: str
    section: str
    status: str
    display_metric: Decimal | None
    next_action_code: str
    source_record_id: str
    source_revision: str
    approval_required: bool = True
    execution_claimed: bool = False


def classify_growth_signal(
    signal: GrowthSignal,
    *,
    scope: PolicyScope,
    as_of: datetime,
    max_age_seconds: int,
) -> ClassifiedGrowthSignal:
    """Classify only within a pre-authenticated, tenant/record-scoped view."""
    if signal.signal_code not in SIGNAL_TAXONOMY:
        raise ValueError("Unknown signal code is not eligible for a briefing")
    definition = SIGNAL_TAXONOMY[signal.signal_code]
    if signal.department != definition.department:
        raise ValueError("Signal code does not belong to requested department")
    if (
        not scope.company_id or scope.company_id != signal.company_id
        or not scope.policy_decision_id or signal.entity_ref not in scope.permitted_entity_refs
        or scope.effective_role not in definition.allowed_roles
    ):
        raise PermissionError("Tenant, role and record-level scope required")
    if not signal.entity_ref or max_age_seconds < 0:
        raise ValueError("Opaque entity reference and nonnegative freshness window required")
    if signal.source_quality not in {"verified", "partial", "unavailable"}:
        raise ValueError("Unknown source-quality status")
    if as_of.tzinfo is None or signal.observed_at.tzinfo is None:
        raise ValueError("Timezone-aware source timestamps required")
    age = (as_of.astimezone(timezone.utc) - signal.observed_at.astimezone(timezone.utc)).total_seconds()
    source_complete = (
        signal.source_quality == "verified"
        and bool(signal.source_record_id and signal.source_revision)
        and signal.metric_value is not None
        and 0 <= age <= max_age_seconds
    )
    if signal.metric_value is not None and (
        not isinstance(signal.metric_value, Decimal) or not signal.metric_value.is_finite()
    ):
        raise ValueError("Signal metrics must be finite Decimal or missing")

    return ClassifiedGrowthSignal(
        department=definition.department,
        signal_code=signal.signal_code,
        entity_ref=signal.entity_ref,
        # Missing evidence must never downgrade an urgent red alert.
        section=definition.section,
        status="requires_human_review" if source_complete else "source_unavailable",
        display_metric=signal.metric_value if source_complete else None,
        next_action_code=definition.next_action_code if source_complete else "verify_source_evidence",
        source_record_id=signal.source_record_id,
        source_revision=signal.source_revision,
    )
