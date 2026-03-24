"""
§20 — Progressive Autonomy Framework.

Action Object schema + Autonomy Tier engine.

Every recommendation produced by any module in this package is expressed as
an ActionObject.  The AutonomyEngine decides what to DO with that object based
on the company's current tier and the action's confidence / risk / impact.

Tiers
─────
Tier 1 — Inform      : show insight in dashboard, no action
Tier 2 — Draft       : create draft (PO, email) awaiting human approval
Tier 3 — Act-in-rails: execute automatically within pre-approved guardrails
Tier 4 — Autonomous  : act and escalate only on anomaly / policy breach

Action Object fields
────────────────────
  id            : uuid
  type          : enum (reorder | pricing_adjustment | customer_outreach |
                         supplier_escalation | inventory_transfer |
                         returns_claim | cost_review | capacity_alert)
  title         : short human-readable label
  description   : full explanation of why this action is recommended
  confidence    : 0.0–1.0  (model certainty)
  risk          : low | medium | high | critical
  impact        : dict  { revenue_delta, margin_delta, cost_delta, units }
  expiration    : ISO-8601 datetime after which action is stale
  context       : arbitrary dict (supporting data, links, calculations)
  audit_trail   : list of events (created, reviewed, approved, executed, …)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


# ─── Types ───────────────────────────────────────────────────────────────────


class ActionType(str, Enum):
    REORDER = "reorder"
    PRICING_ADJUSTMENT = "pricing_adjustment"
    CUSTOMER_OUTREACH = "customer_outreach"
    SUPPLIER_ESCALATION = "supplier_escalation"
    INVENTORY_TRANSFER = "inventory_transfer"
    RETURNS_CLAIM = "returns_claim"
    COST_REVIEW = "cost_review"
    CAPACITY_ALERT = "capacity_alert"
    DEMAND_ALERT = "demand_alert"
    MARGIN_ALERT = "margin_alert"


RiskLevel = Literal["low", "medium", "high", "critical"]

Tier = Literal[1, 2, 3, 4]


@dataclass
class ActionImpact:
    revenue_delta: float = 0.0      # positive = gain, negative = loss (USD)
    margin_delta: float = 0.0       # absolute margin change (USD)
    cost_delta: float = 0.0         # cost change (USD)
    units: int = 0                  # units affected
    days_of_supply_change: float = 0.0

    def to_dict(self) -> dict:
        return {
            "revenue_delta": self.revenue_delta,
            "margin_delta": self.margin_delta,
            "cost_delta": self.cost_delta,
            "units": self.units,
            "days_of_supply_change": self.days_of_supply_change,
        }


@dataclass
class AuditEntry:
    event: str          # created | reviewed | approved | rejected | executed | expired
    actor: str          # user_id or "system"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str = ""

    def to_dict(self) -> dict:
        return {"event": self.event, "actor": self.actor,
                "timestamp": self.timestamp, "note": self.note}


@dataclass
class ActionObject:
    """The universal recommendation unit produced by every intelligence module."""

    type: ActionType
    title: str
    description: str
    confidence: float               # 0.0–1.0
    risk: RiskLevel
    impact: ActionImpact
    expiration: str                 # ISO-8601
    context: dict[str, Any] = field(default_factory=dict)
    audit_trail: list[AuditEntry] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "pending"         # pending | approved | rejected | executed | expired

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "description": self.description,
            "confidence": self.confidence,
            "risk": self.risk,
            "impact": self.impact.to_dict(),
            "expiration": self.expiration,
            "context": self.context,
            "audit_trail": [e.to_dict() for e in self.audit_trail],
            "created_at": self.created_at,
            "status": self.status,
        }

    def record(self, event: str, actor: str = "system", note: str = "") -> None:
        self.audit_trail.append(AuditEntry(event=event, actor=actor, note=note))


# ─── Guardrails ───────────────────────────────────────────────────────────────


@dataclass
class Guardrails:
    """
    Pre-approved operational limits for Tier 3 autonomous execution.
    Values are per-action-type dollar / unit limits.
    """
    max_reorder_value: float = 5_000.0      # auto-PO ≤ $5k
    max_pricing_change_pct: float = 5.0     # price change ≤ 5%
    max_outreach_per_day: int = 20          # email sends per day
    allowed_suppliers: list[str] = field(default_factory=list)
    require_approval_above_confidence: float = 0.0   # 0 = never force; 1.0 = always
    require_approval_risks: list[RiskLevel] = field(
        default_factory=lambda: ["high", "critical"]
    )

    def action_within_rails(self, action: ActionObject) -> bool:
        if action.risk in self.require_approval_risks:
            return False
        if action.type == ActionType.REORDER:
            if abs(action.impact.cost_delta) > self.max_reorder_value:
                return False
        if action.type == ActionType.PRICING_ADJUSTMENT:
            pct = action.context.get("price_change_pct", 0.0)
            if abs(pct) > self.max_pricing_change_pct:
                return False
        return True


# ─── Autonomy Engine ──────────────────────────────────────────────────────────


class AutonomyEngine:
    """
    Decides how to handle each ActionObject based on:
      • company tier (1–4)
      • action confidence
      • action risk
      • guardrails

    Returns a disposition dict with:
      disposition : inform | draft | execute | escalate
      reason      : human-readable explanation
    """

    def __init__(self, tier: Tier, guardrails: Guardrails | None = None):
        self.tier = tier
        self.guardrails = guardrails or Guardrails()

    def evaluate(self, action: ActionObject) -> dict[str, Any]:
        """
        Returns disposition dict.  Caller is responsible for acting on it.
        """
        # Tier 1 — always inform only
        if self.tier == 1:
            return self._disposition("inform", action, "Tier 1: inform-only mode")

        # Expired action
        try:
            exp = datetime.fromisoformat(action.expiration.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                action.status = "expired"
                action.record("expired")
                return self._disposition("inform", action, "Action has expired")
        except (ValueError, AttributeError):
            pass

        # Tier 2 — always draft
        if self.tier == 2:
            return self._disposition("draft", action, "Tier 2: draft for approval")

        # Low confidence → downgrade to draft regardless of tier
        if action.confidence < 0.65:
            return self._disposition(
                "draft", action,
                f"Confidence {action.confidence:.0%} below 65% threshold — draft for review"
            )

        # Tier 3 — execute only if within guardrails
        if self.tier == 3:
            if self.guardrails.action_within_rails(action):
                return self._disposition("execute", action,
                                         "Tier 3: within guardrails — auto-execute")
            return self._disposition("draft", action,
                                     "Tier 3: outside guardrails — draft for approval")

        # Tier 4 — execute; escalate only on critical risk
        if self.tier == 4:
            if action.risk == "critical":
                return self._disposition("escalate", action,
                                         "Tier 4: critical risk — escalate to manager")
            return self._disposition("execute", action, "Tier 4: autonomous execution")

        return self._disposition("inform", action, "Unrecognised tier")

    @staticmethod
    def _disposition(
        disposition: str,
        action: ActionObject,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "action_id": action.id,
            "disposition": disposition,   # inform | draft | execute | escalate
            "reason": reason,
            "action": action.to_dict(),
        }
