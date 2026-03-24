"""
§23 — Dynamic Pricing Intelligence.

Price elasticity analysis, competitive response modeling, supplier price
increase pass-through strategy, and margin-based pricing tiers.

Core concepts
─────────────
Price Elasticity of Demand (PED):
    PED = (% change in quantity demanded) / (% change in price)

    PED < -1   : elastic — customers reduce purchases significantly if price rises
    PED = -1   : unit elastic
    PED > -1   : inelastic — customers tolerate price increases (essential goods)

Segment-level elasticity:
    Different customer segments have different price sensitivities.
    Large distributors: elastic (shopping alternatives)
    Small independents: inelastic (loyalty, convenience)
    Contract customers: locked in (inelastic until renewal)

Pricing tiers (wholesale distributor)
──────────────────────────────────────
  TIER_1 : National accounts / key accounts      → deepest discount
  TIER_2 : Regional distributors / mid-volume    → standard wholesale
  TIER_3 : Small independents / low volume       → near list price
  TIER_4 : One-time / spot buyers                → list price

Supplier price increase pass-through strategy
─────────────────────────────────────────────
  1. Absorb fully (margin compression)    — for elastic segments
  2. Partial pass-through                 — for mixed segments
  3. Full pass-through + surcharge        — for inelastic / locked segments
  4. Staged pass-through over 60–90 days — for relationship-sensitive accounts
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PricingTier(str, Enum):
    TIER_1 = "tier_1"      # Key accounts
    TIER_2 = "tier_2"      # Regional distributors
    TIER_3 = "tier_3"      # Small independents
    TIER_4 = "tier_4"      # Spot buyers


class PassThroughStrategy(str, Enum):
    ABSORB_FULLY = "absorb_fully"
    PARTIAL = "partial"
    FULL_PASS_THROUGH = "full_pass_through"
    STAGED = "staged"
    SURCHARGE = "surcharge"


@dataclass
class ElasticityEstimate:
    """Price elasticity estimate for a customer segment × SKU category."""
    segment: str
    sku_category: str
    ped: float                              # Price Elasticity of Demand (negative)
    confidence: float                       # 0–1
    sample_size: int                        # number of price-change events observed
    notes: str = ""

    @property
    def is_elastic(self) -> bool:
        return self.ped < -1.0

    @property
    def elasticity_label(self) -> str:
        if self.ped < -2.0:
            return "highly_elastic"
        if self.ped < -1.0:
            return "elastic"
        if self.ped < -0.5:
            return "mildly_inelastic"
        return "inelastic"

    def to_dict(self) -> dict:
        return {
            "segment": self.segment,
            "sku_category": self.sku_category,
            "ped": round(self.ped, 3),
            "elasticity_label": self.elasticity_label,
            "confidence": round(self.confidence, 2),
            "sample_size": self.sample_size,
            "notes": self.notes,
        }


@dataclass
class PricingRecommendation:
    """A pricing action recommendation for a specific SKU × segment."""
    sku: str
    segment: str
    pricing_tier: PricingTier
    current_price: float
    recommended_price: float
    price_change_pct: float
    current_margin_pct: float
    projected_margin_pct: float
    revenue_impact_estimate: float          # annual USD delta
    volume_impact_estimate_pct: float       # % volume change expected
    confidence: float
    rationale: str
    pass_through_strategy: Optional[PassThroughStrategy] = None
    staged_timeline_days: Optional[int] = None   # for STAGED strategy
    competitive_position: str = "unknown"   # below_market | at_market | above_market

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "segment": self.segment,
            "pricing_tier": self.pricing_tier.value,
            "current_price": round(self.current_price, 4),
            "recommended_price": round(self.recommended_price, 4),
            "price_change_pct": round(self.price_change_pct, 2),
            "current_margin_pct": round(self.current_margin_pct, 2),
            "projected_margin_pct": round(self.projected_margin_pct, 2),
            "revenue_impact_estimate_annual": round(self.revenue_impact_estimate, 0),
            "volume_impact_pct": round(self.volume_impact_estimate_pct, 1),
            "confidence": round(self.confidence, 2),
            "rationale": self.rationale,
            "pass_through_strategy": self.pass_through_strategy.value if self.pass_through_strategy else None,
            "staged_timeline_days": self.staged_timeline_days,
            "competitive_position": self.competitive_position,
        }


@dataclass
class SupplierIncreasePlan:
    """Strategy for responding to a supplier price increase."""
    supplier: str
    sku: str
    supplier_increase_pct: float
    recommendations: list[PricingRecommendation]
    blended_pass_through_pct: float         # weighted avg across all segments
    margin_impact_at_zero_pass_through: float  # worst case (absorb all)
    margin_impact_at_full_pass_through: float  # best case (pass all through)
    recommended_action_summary: str

    def to_dict(self) -> dict:
        return {
            "supplier": self.supplier,
            "sku": self.sku,
            "supplier_increase_pct": round(self.supplier_increase_pct, 2),
            "blended_pass_through_pct": round(self.blended_pass_through_pct, 1),
            "margin_impact_absorb_all": round(self.margin_impact_at_zero_pass_through, 2),
            "margin_impact_pass_all": round(self.margin_impact_at_full_pass_through, 2),
            "recommended_summary": self.recommended_action_summary,
            "segment_recommendations": [r.to_dict() for r in self.recommendations],
        }


# ─── Dynamic Pricing Engine ───────────────────────────────────────────────────


class DynamicPricingEngine:
    """
    Computes price elasticity, recommends pricing adjustments, and builds
    supplier price increase pass-through strategies.
    """

    def __init__(self, company_id: str, target_gross_margin_pct: float = 30.0):
        self.company_id = company_id
        self.target_margin = target_gross_margin_pct

    # ── Elasticity Estimation ────────────────────────────────────────────────

    def estimate_elasticity(
        self,
        segment: str,
        sku_category: str,
        price_change_events: list[dict],
        # [{old_price, new_price, units_before, units_after, period_days}, ...]
    ) -> ElasticityEstimate:
        """
        Compute price elasticity of demand from observed price-change events.
        Uses arc elasticity formula for robustness with large price changes.
        """
        peds: list[float] = []

        for event in price_change_events:
            p1 = event.get("old_price", 0)
            p2 = event.get("new_price", 0)
            q1 = event.get("units_before", 0)
            q2 = event.get("units_after", 0)

            if p1 <= 0 or p2 <= 0 or q1 <= 0:
                continue

            # Normalise quantities to same period length
            period = max(event.get("period_days", 30), 1)
            q1_daily = q1 / period
            q2_daily = q2 / period

            # Arc elasticity: avoids endpoint sensitivity
            pct_q = (q2_daily - q1_daily) / ((q2_daily + q1_daily) / 2) if (q2_daily + q1_daily) else 0
            pct_p = (p2 - p1) / ((p2 + p1) / 2)

            if pct_p != 0:
                peds.append(pct_q / pct_p)

        if not peds:
            return ElasticityEstimate(
                segment=segment,
                sku_category=sku_category,
                ped=-1.0,   # assume unit elastic as default
                confidence=0.1,
                sample_size=0,
                notes="No price-change events to estimate elasticity — using default -1.0",
            )

        ped = statistics.mean(peds)
        # Confidence: higher with more events and lower variance
        confidence = min(0.9, 0.3 + len(peds) * 0.1)
        if len(peds) > 2:
            cv = statistics.stdev(peds) / max(abs(ped), 0.01)
            confidence *= max(0.3, 1.0 - cv * 0.3)

        return ElasticityEstimate(
            segment=segment,
            sku_category=sku_category,
            ped=ped,
            confidence=confidence,
            sample_size=len(peds),
        )

    # ── Pricing Recommendation ───────────────────────────────────────────────

    def recommend_price(
        self,
        sku: str,
        segment: str,
        pricing_tier: PricingTier,
        current_price: float,
        unit_cost: float,
        annual_units: float,
        elasticity: ElasticityEstimate,
        competitive_prices: list[float] | None = None,
    ) -> PricingRecommendation:
        """
        Recommend optimal price for a SKU × segment combination.
        Balances margin target with volume retention based on elasticity.
        """
        current_margin = self._margin_pct(current_price, unit_cost)
        market_price = statistics.mean(competitive_prices) if competitive_prices else None

        notes: list[str] = []
        recommended_price = current_price
        rationale_parts: list[str] = []

        # ── Margin target gap ─────────────────────────────────────────────────
        target_price = unit_cost / (1 - self.target_margin / 100)
        margin_gap = target_price - current_price

        if margin_gap > 0.01:
            # Need to increase price to hit margin target
            # Discount increase based on elasticity — more elastic = smaller increase
            max_increase = margin_gap
            elasticity_dampener = 1.0 / max(abs(elasticity.ped), 0.5)
            increase = max_increase * min(elasticity_dampener, 1.0)

            # Don't raise above market if we know competitor prices
            if market_price and current_price + increase > market_price * 1.05:
                increase = max(0, market_price * 1.05 - current_price)
                notes.append("Increase capped at 5% above market to preserve competitiveness")

            recommended_price = current_price + increase
            rationale_parts.append(
                f"Current margin {current_margin:.1f}% is below target {self.target_margin:.1f}%. "
                f"Recommending {(increase / current_price * 100):.1f}% price increase."
            )

        elif margin_gap < -0.05 * current_price:
            # We're significantly above target — pricing opportunity or over-priced
            if elasticity.is_elastic:
                # Elastic: small price cut could increase volume and total profit
                suggested_cut = min(abs(margin_gap) * 0.3, current_price * 0.05)
                recommended_price = current_price - suggested_cut
                vol_gain = abs(elasticity.ped) * (suggested_cut / current_price) * 100
                rationale_parts.append(
                    f"High elasticity (PED={elasticity.ped:.2f}): {suggested_cut / current_price * 100:.1f}% "
                    f"price cut projected to increase volume by ~{vol_gain:.0f}%, "
                    "improving net margin through volume."
                )
            else:
                rationale_parts.append(
                    f"Margin {current_margin:.1f}% exceeds target — but low elasticity means "
                    "price cut unlikely to generate sufficient volume uplift. Maintain price."
                )

        # ── Competitive position ──────────────────────────────────────────────
        competitive_position = "unknown"
        if market_price:
            diff_pct = (recommended_price - market_price) / market_price * 100
            if diff_pct < -5:
                competitive_position = "below_market"
                if not elasticity.is_elastic:
                    notes.append(
                        f"Priced {abs(diff_pct):.1f}% below market — potential to raise without volume loss"
                    )
            elif diff_pct > 5:
                competitive_position = "above_market"
                notes.append(f"Priced {diff_pct:.1f}% above market — monitor for volume erosion")
            else:
                competitive_position = "at_market"

        # ── Volume and revenue impact ─────────────────────────────────────────
        price_change_pct = (recommended_price - current_price) / current_price * 100
        volume_impact_pct = elasticity.ped * price_change_pct  # % change in demand
        revenue_delta = annual_units * (
            recommended_price * (1 + volume_impact_pct / 100) - current_price
        )

        proj_margin = self._margin_pct(recommended_price, unit_cost)

        if notes:
            rationale_parts.extend(notes)

        return PricingRecommendation(
            sku=sku,
            segment=segment,
            pricing_tier=pricing_tier,
            current_price=current_price,
            recommended_price=recommended_price,
            price_change_pct=price_change_pct,
            current_margin_pct=current_margin,
            projected_margin_pct=proj_margin,
            revenue_impact_estimate=revenue_delta,
            volume_impact_estimate_pct=volume_impact_pct,
            confidence=elasticity.confidence,
            rationale=" ".join(rationale_parts) or "Price is optimal — no change recommended.",
            competitive_position=competitive_position,
        )

    # ── Supplier Price Increase Strategy ─────────────────────────────────────

    def supplier_increase_strategy(
        self,
        supplier: str,
        sku: str,
        unit_cost_before: float,
        unit_cost_after: float,
        current_price: float,
        segment_data: list[dict],
        # [{segment, pricing_tier, annual_units, elasticity_ped, competitive_prices}]
    ) -> SupplierIncreasePlan:
        """
        Build a per-segment strategy for responding to a supplier cost increase.
        """
        increase_pct = (unit_cost_after - unit_cost_before) / unit_cost_before * 100
        recommendations: list[PricingRecommendation] = []
        weighted_pass_through = 0.0
        total_units = sum(s.get("annual_units", 0) for s in segment_data)

        for seg in segment_data:
            seg_name = seg.get("segment", "unknown")
            tier = PricingTier(seg.get("pricing_tier", "tier_2"))
            annual_units = seg.get("annual_units", 0)
            ped = seg.get("elasticity_ped", -1.0)
            comp_prices = seg.get("competitive_prices")

            elasticity = ElasticityEstimate(
                segment=seg_name, sku_category="", ped=ped, confidence=0.5, sample_size=0
            )

            # Determine pass-through strategy for this segment
            strategy, pass_through_pct, staged_days = self._choose_pass_through(
                ped=ped, tier=tier, increase_pct=increase_pct
            )

            # New price = absorb (1 - pass_through_pct) of the increase
            new_unit_cost = unit_cost_after
            absorbed_increase = (unit_cost_after - unit_cost_before) * (1 - pass_through_pct / 100)
            effective_cost_for_pricing = unit_cost_after - absorbed_increase
            new_price = max(current_price, effective_cost_for_pricing / (1 - self.target_margin / 100))

            rec = self.recommend_price(
                sku=sku,
                segment=seg_name,
                pricing_tier=tier,
                current_price=current_price,
                unit_cost=effective_cost_for_pricing,
                annual_units=annual_units,
                elasticity=elasticity,
                competitive_prices=comp_prices,
            )
            rec.pass_through_strategy = strategy
            rec.staged_timeline_days = staged_days
            recommendations.append(rec)

            if total_units > 0:
                weighted_pass_through += pass_through_pct * (annual_units / total_units)

        # Margin impact scenarios
        full_absorption_margin = self._margin_pct(current_price, unit_cost_after)
        full_pass_through_price = current_price * (unit_cost_after / unit_cost_before)
        full_pass_margin = self._margin_pct(full_pass_through_price, unit_cost_after)
        current_margin = self._margin_pct(current_price, unit_cost_before)

        summary = (
            f"Supplier increased cost by {increase_pct:.1f}%. "
            f"Recommended blended pass-through: {weighted_pass_through:.0f}%. "
            f"Full absorption reduces margin from {current_margin:.1f}% to {full_absorption_margin:.1f}%. "
            f"Full pass-through maintains margin at {full_pass_margin:.1f}% but risks volume loss for elastic segments."
        )

        return SupplierIncreasePlan(
            supplier=supplier,
            sku=sku,
            supplier_increase_pct=increase_pct,
            recommendations=recommendations,
            blended_pass_through_pct=weighted_pass_through,
            margin_impact_at_zero_pass_through=full_absorption_margin - current_margin,
            margin_impact_at_full_pass_through=0.0,
            recommended_action_summary=summary,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _margin_pct(price: float, cost: float) -> float:
        if price <= 0:
            return 0.0
        return ((price - cost) / price) * 100

    @staticmethod
    def _choose_pass_through(
        ped: float, tier: PricingTier, increase_pct: float
    ) -> tuple[PassThroughStrategy, float, Optional[int]]:
        """
        Returns (strategy, pass_through_pct, staged_days).
        pass_through_pct: 0 = absorb all, 100 = pass all through.
        """
        # Key accounts (Tier 1): relationship-sensitive, negotiate carefully
        if tier == PricingTier.TIER_1:
            if increase_pct > 10:
                return PassThroughStrategy.STAGED, 70, 90
            return PassThroughStrategy.PARTIAL, 50, None

        # Elastic segments: absorb more to retain volume
        if ped < -1.5:
            return PassThroughStrategy.PARTIAL, 40, None

        # Mildly elastic
        if ped < -1.0:
            return PassThroughStrategy.PARTIAL, 65, None

        # Inelastic: pass through most or all
        if ped >= -0.5:
            if increase_pct > 15:
                # Even inelastic customers need some notice on large increases
                return PassThroughStrategy.STAGED, 90, 60
            return PassThroughStrategy.FULL_PASS_THROUGH, 100, None

        # Spot buyers (Tier 4): always full pass-through, no relationship cost
        if tier == PricingTier.TIER_4:
            return PassThroughStrategy.FULL_PASS_THROUGH, 100, None

        return PassThroughStrategy.PARTIAL, 70, None
