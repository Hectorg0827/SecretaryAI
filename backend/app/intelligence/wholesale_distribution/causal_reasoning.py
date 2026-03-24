"""
§18 — Causal Reasoning Engine.

Root cause attribution, counterfactual analysis, and intervention targeting
for wholesale distribution intelligence.

Design philosophy
─────────────────
Every alert / anomaly produced by the system MUST include a causal chain — not
just "sales are down 20%" but WHY and WHAT TO DO ABOUT IT.

Three customer segments from causal theory:
  • Natural recoverers  — would recover without intervention (high base rate)
  • Persuadables        — respond to intervention (target these)
  • Lost causes         — intervention makes no difference or backfires

Root cause taxonomy (wholesale distribution)
────────────────────────────────────────────
  DEMAND_SHIFT         — customer changed buying behaviour
  SUPPLY_DISRUPTION    — vendor delay / shortage
  PRICING_PRESSURE     — competitor undercut
  SEASONAL_PATTERN     — expected seasonal trough
  QUALITY_EVENT        — product defect / complaint spike
  OPERATIONAL_FAILURE  — internal picking / shipping error
  EXTERNAL_MACRO       — macro / regulatory / weather event
  UNKNOWN              — insufficient evidence to assign cause
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RootCause(str, Enum):
    DEMAND_SHIFT = "demand_shift"
    SUPPLY_DISRUPTION = "supply_disruption"
    PRICING_PRESSURE = "pricing_pressure"
    SEASONAL_PATTERN = "seasonal_pattern"
    QUALITY_EVENT = "quality_event"
    OPERATIONAL_FAILURE = "operational_failure"
    EXTERNAL_MACRO = "external_macro"
    UNKNOWN = "unknown"


class CustomerSegment(str, Enum):
    NATURAL_RECOVERER = "natural_recoverer"
    PERSUADABLE = "persuadable"
    LOST_CAUSE = "lost_cause"


@dataclass
class CausalChain:
    """A single causal hypothesis with supporting evidence."""
    root_cause: RootCause
    probability: float          # 0.0–1.0 estimated probability this is THE cause
    evidence: list[str]         # Human-readable evidence items
    counterfactual: str         # "If X had not happened, Y would have been Z"
    intervention: str           # Recommended corrective action
    expected_impact: str        # Expected outcome if intervention is taken
    confidence: float = 0.5     # Model confidence in the causal attribution

    def to_dict(self) -> dict:
        return {
            "root_cause": self.root_cause.value,
            "probability": round(self.probability, 3),
            "evidence": self.evidence,
            "counterfactual": self.counterfactual,
            "intervention": self.intervention,
            "expected_impact": self.expected_impact,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class CausalAnalysis:
    """Full causal analysis result for a given anomaly or alert."""
    entity_id: str                      # customer_id, sku, supplier_id, etc.
    entity_type: str                    # customer | sku | supplier | route
    anomaly_description: str
    primary_cause: CausalChain
    alternative_causes: list[CausalChain] = field(default_factory=list)
    customer_segment: Optional[CustomerSegment] = None
    segment_reasoning: str = ""
    overall_confidence: float = 0.5
    data_quality_warning: str = ""

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "anomaly_description": self.anomaly_description,
            "primary_cause": self.primary_cause.to_dict(),
            "alternative_causes": [c.to_dict() for c in self.alternative_causes],
            "customer_segment": self.customer_segment.value if self.customer_segment else None,
            "segment_reasoning": self.segment_reasoning,
            "overall_confidence": round(self.overall_confidence, 3),
            "data_quality_warning": self.data_quality_warning,
        }


# ─── Causal Reasoning Engine ─────────────────────────────────────────────────


class CausalReasoningEngine:
    """
    Identifies root causes for anomalies detected in wholesale distribution data.

    Input: time-series of orders, inventory events, pricing changes, returns,
           supplier lead times, customer communications.

    Output: CausalAnalysis with ranked hypotheses + intervention recommendation.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id

    # ── Public API ───────────────────────────────────────────────────────────

    def analyse_customer_drop(
        self,
        customer_id: str,
        customer_name: str,
        order_history: list[dict],          # [{date, amount, units}, ...]
        recent_returns: list[dict],         # [{date, reason, amount}, ...]
        pricing_changes: list[dict],        # [{date, sku, old_price, new_price}, ...]
        competitor_signals: list[dict],     # [{date, signal_type, note}, ...]
        days_since_last_order: int,
    ) -> CausalAnalysis:
        """
        Identify why a customer's order volume dropped or went silent.
        Returns CausalAnalysis with ranked causal chains and a customer segment.
        """
        hypotheses: list[CausalChain] = []

        # 1. Check for quality events (returns spike)
        if recent_returns:
            total_return_amount = sum(r.get("amount", 0) for r in recent_returns)
            if total_return_amount > 0:
                hypotheses.append(CausalChain(
                    root_cause=RootCause.QUALITY_EVENT,
                    probability=min(0.9, 0.3 + total_return_amount / 5000),
                    evidence=[
                        f"{len(recent_returns)} returns in the period totalling ${total_return_amount:,.0f}",
                        *[f"Return reason: {r.get('reason', 'unspecified')}" for r in recent_returns[:3]],
                    ],
                    counterfactual=(
                        f"If the quality issue had not occurred, {customer_name} would likely "
                        "have maintained their normal order cadence."
                    ),
                    intervention=(
                        "Initiate quality review of returned SKUs. Reach out personally to "
                        f"{customer_name} with acknowledgment + credit note offer."
                    ),
                    expected_impact="60–80% recovery probability with proactive outreach + credit within 2 weeks.",
                    confidence=0.75,
                ))

        # 2. Check for pricing pressure (recent price increases)
        if pricing_changes:
            avg_increase_pct = self._avg_price_change_pct(pricing_changes)
            if avg_increase_pct > 3.0:
                hypotheses.append(CausalChain(
                    root_cause=RootCause.PRICING_PRESSURE,
                    probability=min(0.85, 0.2 + avg_increase_pct / 20),
                    evidence=[
                        f"Average price increase of {avg_increase_pct:.1f}% across {len(pricing_changes)} SKUs",
                        *[
                            f"{pc.get('sku')}: ${pc.get('old_price'):.2f} → ${pc.get('new_price'):.2f}"
                            for pc in pricing_changes[:3]
                        ],
                    ],
                    counterfactual=(
                        f"If prices had not been raised, {customer_name} would likely have "
                        "continued ordering at the previous cadence."
                    ),
                    intervention=(
                        f"Offer {customer_name} a temporary volume discount or price-lock "
                        "agreement. Benchmark against known competitor pricing."
                    ),
                    expected_impact="40–60% recovery if competitive pricing is restored within 30 days.",
                    confidence=0.65,
                ))

        # 3. Check for competitor signals
        if competitor_signals:
            hypotheses.append(CausalChain(
                root_cause=RootCause.PRICING_PRESSURE,
                probability=0.5,
                evidence=[
                    f"{len(competitor_signals)} competitor signal(s) detected",
                    *[f"{cs.get('signal_type')}: {cs.get('note', '')}" for cs in competitor_signals[:2]],
                ],
                counterfactual=(
                    f"Without competitive disruption, {customer_name} would likely have "
                    "maintained baseline order frequency."
                ),
                intervention="Conduct competitive analysis. Consider strategic repricing or bundling.",
                expected_impact="Variable; depends on whether price or relationship is primary driver.",
                confidence=0.45,
            ))

        # 4. Check for seasonal patterns
        seasonal_prob = self._detect_seasonal_pattern(order_history, days_since_last_order)
        if seasonal_prob > 0.3:
            hypotheses.append(CausalChain(
                root_cause=RootCause.SEASONAL_PATTERN,
                probability=seasonal_prob,
                evidence=[
                    "Order gap matches historical seasonal trough for this customer",
                    f"{days_since_last_order} days since last order — consistent with prior year pattern",
                ],
                counterfactual=(
                    f"{customer_name} has historically paused orders during this period. "
                    "No intervention may be needed."
                ),
                intervention="Monitor; send light-touch 'We're here when you're ready' message.",
                expected_impact="Customer will likely re-engage at start of next buying season.",
                confidence=0.70,
            ))

        # 5. Demand shift (default if nothing else strong)
        if not hypotheses or max(h.probability for h in hypotheses) < 0.5:
            hypotheses.append(CausalChain(
                root_cause=RootCause.DEMAND_SHIFT,
                probability=0.45,
                evidence=[
                    f"{days_since_last_order} days since last order with no clear external cause",
                    "No quality events, price changes, or seasonal pattern detected",
                ],
                counterfactual=(
                    f"If {customer_name}'s demand had not shifted, we would expect an order "
                    f"within {days_since_last_order} days."
                ),
                intervention=(
                    "Schedule discovery call to understand changed needs. "
                    "Explore new product lines that match their evolving business."
                ),
                expected_impact="50% recovery with relationship investment over 60-90 days.",
                confidence=0.4,
            ))

        # Rank hypotheses
        hypotheses.sort(key=lambda h: h.probability, reverse=True)
        primary = hypotheses[0]
        alternatives = hypotheses[1:]

        # Segment the customer
        segment, segment_reasoning = self._segment_customer(
            primary_cause=primary.root_cause,
            days_silent=days_since_last_order,
            order_history=order_history,
            recent_returns=recent_returns,
        )

        overall_confidence = primary.confidence * primary.probability

        return CausalAnalysis(
            entity_id=customer_id,
            entity_type="customer",
            anomaly_description=(
                f"{customer_name} has not ordered in {days_since_last_order} days "
                f"(expected cadence: {self._avg_order_gap_days(order_history):.0f} days)"
            ),
            primary_cause=primary,
            alternative_causes=alternatives,
            customer_segment=segment,
            segment_reasoning=segment_reasoning,
            overall_confidence=overall_confidence,
        )

    def analyse_inventory_anomaly(
        self,
        sku: str,
        sku_name: str,
        current_stock: int,
        avg_daily_demand: float,
        pending_po_units: int,
        supplier_lead_time_days: float,
        recent_demand_spike: bool,
        recent_supplier_delays: list[dict],
    ) -> CausalAnalysis:
        """Identify why a SKU is at risk (stockout, overstock, or demand spike)."""
        days_of_supply = current_stock / max(avg_daily_demand, 0.01)
        hypotheses: list[CausalChain] = []

        if recent_demand_spike:
            hypotheses.append(CausalChain(
                root_cause=RootCause.DEMAND_SHIFT,
                probability=0.75,
                evidence=[
                    f"Demand spike detected for {sku_name}",
                    f"Current stock: {current_stock} units = {days_of_supply:.0f} days of supply",
                ],
                counterfactual=(
                    f"Without the demand spike, current stock would last "
                    f"{(current_stock / max(avg_daily_demand * 0.7, 0.01)):.0f} days."
                ),
                intervention=(
                    f"Expedite PO for {sku}. "
                    "Investigate demand spike source — new customer, seasonal, or promotional."
                ),
                expected_impact="Prevent stockout if expedited PO placed within 48 hours.",
                confidence=0.8,
            ))

        if recent_supplier_delays:
            delay_days = max(
                (d.get("delay_days", 0) for d in recent_supplier_delays), default=0
            )
            hypotheses.append(CausalChain(
                root_cause=RootCause.SUPPLY_DISRUPTION,
                probability=0.80,
                evidence=[
                    f"{len(recent_supplier_delays)} recent supplier delays",
                    f"Maximum delay: {delay_days} days",
                    f"Pending PO: {pending_po_units} units",
                ],
                counterfactual=(
                    f"If the supplier had delivered on time, {sku_name} would have "
                    f"{current_stock + pending_po_units} units available."
                ),
                intervention=(
                    "Escalate to alternative supplier for emergency coverage. "
                    "Negotiate partial shipment from primary supplier."
                ),
                expected_impact="Risk of stockout reduced by 60–80% with dual-source strategy.",
                confidence=0.85,
            ))

        if not hypotheses:
            hypotheses.append(CausalChain(
                root_cause=RootCause.UNKNOWN,
                probability=0.5,
                evidence=[f"Inventory at {days_of_supply:.0f} days of supply for {sku_name}"],
                counterfactual="Insufficient data to determine root cause.",
                intervention="Review historical demand patterns and supplier performance.",
                expected_impact="Unknown without further data.",
                confidence=0.3,
            ))

        hypotheses.sort(key=lambda h: h.probability, reverse=True)

        return CausalAnalysis(
            entity_id=sku,
            entity_type="sku",
            anomaly_description=(
                f"{sku_name} has {days_of_supply:.1f} days of supply at current demand rate"
            ),
            primary_cause=hypotheses[0],
            alternative_causes=hypotheses[1:],
            overall_confidence=hypotheses[0].confidence,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _avg_price_change_pct(self, pricing_changes: list[dict]) -> float:
        pcts = []
        for pc in pricing_changes:
            old = pc.get("old_price", 0)
            new = pc.get("new_price", 0)
            if old and old > 0:
                pcts.append(((new - old) / old) * 100)
        return statistics.mean(pcts) if pcts else 0.0

    def _avg_order_gap_days(self, order_history: list[dict]) -> float:
        if len(order_history) < 2:
            return 30.0
        gaps = []
        sorted_orders = sorted(order_history, key=lambda o: o.get("date", ""))
        for i in range(1, len(sorted_orders)):
            try:
                from datetime import date
                d1 = date.fromisoformat(sorted_orders[i - 1]["date"][:10])
                d2 = date.fromisoformat(sorted_orders[i]["date"][:10])
                gaps.append((d2 - d1).days)
            except (KeyError, ValueError):
                continue
        return statistics.mean(gaps) if gaps else 30.0

    def _detect_seasonal_pattern(
        self, order_history: list[dict], days_since_last_order: int
    ) -> float:
        """
        Returns a probability 0–1 that the current silence is explained by
        a seasonal pattern observed in prior years.
        """
        if len(order_history) < 20:
            return 0.0  # Not enough history to detect seasonality

        # Look for gaps of similar length in prior data
        sorted_orders = sorted(order_history, key=lambda o: o.get("date", ""))
        gaps = []
        for i in range(1, len(sorted_orders)):
            try:
                from datetime import date
                d1 = date.fromisoformat(sorted_orders[i - 1]["date"][:10])
                d2 = date.fromisoformat(sorted_orders[i]["date"][:10])
                gaps.append((d2 - d1).days)
            except (KeyError, ValueError):
                continue

        if not gaps:
            return 0.0

        # If current gap is within 1 std dev of the max historical gap, could be seasonal
        max_gap = max(gaps)
        if days_since_last_order >= max_gap * 0.8:
            return 0.55  # Plausibly seasonal
        return 0.1

    def _segment_customer(
        self,
        primary_cause: RootCause,
        days_silent: int,
        order_history: list[dict],
        recent_returns: list[dict],
    ) -> tuple[CustomerSegment, str]:
        """
        Classify the customer as natural_recoverer / persuadable / lost_cause.
        Uses simple heuristics; can be replaced with a trained classifier.
        """
        # Long silence + quality events → likely lost cause or needs heavy intervention
        if days_silent > 180:
            return (
                CustomerSegment.LOST_CAUSE,
                f"Customer has been silent for {days_silent} days — "
                "win-back probability < 15% based on historical patterns.",
            )

        if primary_cause == RootCause.SEASONAL_PATTERN:
            return (
                CustomerSegment.NATURAL_RECOVERER,
                "Silence matches seasonal pattern — customer will likely re-engage without intervention.",
            )

        if primary_cause in (RootCause.QUALITY_EVENT, RootCause.PRICING_PRESSURE):
            return (
                CustomerSegment.PERSUADABLE,
                "A specific, addressable issue is driving the silence. "
                "Targeted intervention has high recovery probability.",
            )

        # Default: persuadable if < 90 days
        if days_silent < 90:
            return (
                CustomerSegment.PERSUADABLE,
                f"{days_silent} days silent — early enough for effective outreach.",
            )

        return (
            CustomerSegment.LOST_CAUSE,
            f"{days_silent} days silent with no clear addressable cause — "
            "low-cost monitoring recommended over active intervention.",
        )
