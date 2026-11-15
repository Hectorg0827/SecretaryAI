"""Fail-closed, analysis-only Lola Chief-of-Staff decision gate.

Adapters must be server-authorized and tenant-scoped. This module NEVER
fabricates evidence, persists approvals, speaks, or changes a price book.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Awaitable, Callable, Mapping
from uuid import uuid4


class DecisionState(str, Enum):
    SILENT = "SILENT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"


class EvidenceError(ValueError):
    """Missing, stale, inconsistent or unauthorized evidence."""


@dataclass(frozen=True)
class WakeTrigger:
    company_id: str
    shipment_ref: str
    sku: str
    trigger_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not all((self.company_id, self.shipment_ref, self.sku, self.trigger_id)):
            raise ValueError("Company, shipment, SKU and trigger identities are required")


@dataclass(frozen=True)
class LaneEvidence:
    lane: str
    company_id: str
    shipment_ref: str
    sku: str
    source_record_id: str
    source_revision: str
    observed_at: datetime
    verified: bool
    values: Mapping[str, str]


@dataclass(frozen=True)
class GatePolicy:
    max_silent_cost_variance_usd: Decimal = Decimal("50")
    max_silent_margin_erosion_bps: Decimal = Decimal("50")
    max_evidence_age: timedelta = timedelta(hours=24)
    lane_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        for value in (self.max_silent_cost_variance_usd, self.max_silent_margin_erosion_bps):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("Silent thresholds must be finite nonnegative Decimals")
        if self.max_evidence_age <= timedelta(0) or self.lane_timeout_seconds <= 0:
            raise ValueError("Evidence age and lane timeout must be positive")


@dataclass(frozen=True)
class ProposedReview:
    company_id: str
    shipment_ref: str
    sku: str
    cost_variance_usd: str
    margin_erosion_bps: str
    suggested_price_per_case_usd: str | None
    source_revisions: tuple[str, ...]
    status: str = "proposal_only_not_persisted"
    requires_authenticated_approval: bool = True


@dataclass(frozen=True)
class RunDecision:
    trigger_id: str
    state: DecisionState
    reasons: tuple[str, ...]
    proposal: ProposedReview | None = None
    external_writes_executed: bool = False
    notifications_sent: bool = False


Lane = Callable[[WakeTrigger], Awaitable[LaneEvidence]]


def _decimal(value: str, name: str) -> Decimal:
    if not isinstance(value, str):
        raise EvidenceError(f"{name}: exact decimal string required")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceError(f"{name}: invalid decimal") from exc
    if not number.is_finite():
        raise EvidenceError(f"{name}: nonfinite decimal")
    return number


def _validate(trigger: WakeTrigger, evidence: LaneEvidence, expected_lane: str,
              now: datetime, policy: GatePolicy) -> None:
    if evidence.lane != expected_lane:
        raise EvidenceError("Wrong evidence lane")
    if (evidence.company_id, evidence.shipment_ref, evidence.sku) != (
        trigger.company_id, trigger.shipment_ref, trigger.sku
    ):
        raise EvidenceError("Tenant/shipment/SKU mismatch")
    if not evidence.verified or not evidence.source_record_id or not evidence.source_revision:
        raise EvidenceError("Evidence not independently verified/versioned")
    if evidence.observed_at.tzinfo is None:
        raise EvidenceError("Evidence timestamp timezone missing")
    age = now - evidence.observed_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > policy.max_evidence_age:
        raise EvidenceError("Evidence stale or from the future")


async def evaluate_event(
    trigger: WakeTrigger, *, logistics_lane: Lane, finance_lane: Lane,
    portal_lane: Lane | None = None, policy: GatePolicy | None = None,
    now: datetime | None = None,
) -> RunDecision:
    """Parallel evidence readers; no notifications, persistence or side effects.

    Callers, not the model, must establish source verification. A failing
    mandatory lane must never become a silent 'everything is fine' result.
    """
    policy = policy or GatePolicy()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")

    async def guarded(lane: Lane) -> LaneEvidence | Exception:
        try:
            return await asyncio.wait_for(lane(trigger), timeout=policy.lane_timeout_seconds)
        except Exception as exc:
            return exc

    lanes = [guarded(logistics_lane), guarded(finance_lane)]
    if portal_lane is not None:
        lanes.append(guarded(portal_lane))
    outputs = await asyncio.gather(*lanes)
    log, fin = outputs[:2]
    missing = []
    if isinstance(log, Exception):
        missing.append("logistics_source_unavailable")
    if isinstance(fin, Exception):
        missing.append("finance_source_unavailable")
    if missing:
        return RunDecision(trigger.trigger_id, DecisionState.EVIDENCE_UNAVAILABLE, tuple(missing))

    try:
        _validate(trigger, log, "logistics", now, policy)
        _validate(trigger, fin, "finance", now, policy)
        actual = _decimal(log.values["drayage_actual_usd"], "drayage_actual_usd")
        baseline = _decimal(log.values["drayage_baseline_usd"], "drayage_baseline_usd")
        if min(actual, baseline) < 0:
            raise EvidenceError("Drayage costs cannot be negative")
        variance = actual - baseline
        finance_variance = _decimal(fin.values["cost_variance_usd"], "cost_variance_usd")
        if finance_variance != variance:
            raise EvidenceError("Cross-system variance mismatch")
        before = _decimal(fin.values["gross_margin_before_pct"], "gross_margin_before_pct")
        after = _decimal(fin.values["gross_margin_after_pct"], "gross_margin_after_pct")
        if not 0 <= before <= 100 or not 0 <= after <= 100:
            raise EvidenceError("Gross margin outside percentage range")
        erosion_bps = (before - after) * Decimal("100")
        suggested = fin.values.get("suggested_price_per_case_usd")
        if suggested is not None and _decimal(suggested, "suggested_price_per_case_usd") <= 0:
            raise EvidenceError("Invalid proposed case price")
    except (EvidenceError, KeyError, TypeError, AttributeError):
        return RunDecision(
            trigger.trigger_id, DecisionState.EVIDENCE_UNAVAILABLE,
            ("evidence_inconsistent_or_unverified",),
        )

    reasons: list[str] = []
    source_revisions = [
        f"logistics:{log.source_record_id}@{log.source_revision}",
        f"finance:{fin.source_record_id}@{fin.source_revision}",
    ]
    if portal_lane is not None:
        portal = outputs[2]
        if isinstance(portal, Exception):
            reasons.append("portal_source_unavailable")
        else:
            try:
                _validate(trigger, portal, "portal", now, policy)
                if portal.values.get("hold_present") not in ("true", "false"):
                    raise EvidenceError("Terminal hold unverified")
                source_revisions.append(f"portal:{portal.source_record_id}@{portal.source_revision}")
                if portal.values["hold_present"] == "true":
                    reasons.append("terminal_hold_present")
            except (EvidenceError, KeyError, TypeError, AttributeError):
                reasons.append("portal_evidence_unverified")

    if variance > policy.max_silent_cost_variance_usd:
        reasons.append("cost_overrun_threshold")
    if variance < -policy.max_silent_cost_variance_usd:
        reasons.append("cost_variance_below_baseline_review")
    if erosion_bps > policy.max_silent_margin_erosion_bps:
        reasons.append("margin_erosion_threshold")
    if not reasons:
        return RunDecision(trigger.trigger_id, DecisionState.SILENT, ())
    proposal = ProposedReview(
        trigger.company_id, trigger.shipment_ref, trigger.sku,
        str(variance), str(erosion_bps), suggested, tuple(source_revisions),
    )
    return RunDecision(trigger.trigger_id, DecisionState.REVIEW_REQUIRED,
                       tuple(reasons), proposal)
