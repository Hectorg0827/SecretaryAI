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

import math
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
    company_id: str = ""                    # scope of observed events; blank for legacy fixtures

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
            "company_id": self.company_id,
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
    company_id: str = ""

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
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
            "requires_human_approval": True,
            "source_status": "unverified_advisory",
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
    company_id: str = ""

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
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
        if not company_id or not isinstance(company_id, str):
            raise ValueError("A tenant identifier is required for pricing intelligence")
        if not math.isfinite(target_gross_margin_pct) or not 0 <= target_gross_margin_pct < 100:
            raise ValueError("Target gross margin must be finite and in [0, 100)")
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
            if not isinstance(event, dict):
                continue
            # Ignore evidence explicitly belonging to another tenant, product
            # category or segment. Missing IDs remain legacy unverified input.
            if any(event.get(key) not in (None, expected) for key, expected in (
                ("company_id", self.company_id),
                ("segment", segment),
                ("sku_category", sku_category),
            )):
                continue
            p1 = event.get("old_price", 0)
            p2 = event.get("new_price", 0)
            q1 = event.get("units_before", 0)
            q2 = event.get("units_after", 0)

            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                       for x in (p1, p2, q1, q2)):
                continue
            if p1 <= 0 or p2 <= 0 or q1 <= 0 or q2 < 0:
                continue

            # Normalise quantities to same period length
            period = event.get("period_days", 30)
            # Unequal before/after sales windows must be compared as rates.
            # If either explicit window is present, require the other one too.
            has_before = "period_before_days" in event
            has_after = "period_after_days" in event
            if has_before != has_after:
                continue
            before_days = event.get("period_before_days", period)
            after_days = event.get("period_after_days", period)
            if any(not isinstance(days, (int, float)) or isinstance(days, bool)
                   or not math.isfinite(days) or days <= 0
                   for days in (before_days, after_days)):
                continue
            q1_daily = q1 / before_days
            q2_daily = q2 / after_days

            # Arc elasticity: avoids endpoint sensitivity
            pct_q = (q2_daily - q1_daily) / ((q2_daily + q1_daily) / 2) if (q2_daily + q1_daily) else 0
            pct_p = (p2 - p1) / ((p2 + p1) / 2)

            if pct_p != 0:
                event_ped = pct_q / pct_p
                if math.isfinite(event_ped):
                    peds.append(event_ped)

        if not peds:
            return ElasticityEstimate(
                segment=segment,
                sku_category=sku_category,
                ped=-1.0,   # assume unit elastic as default
                confidence=0.1,
                sample_size=0,
                notes="No price-change events to estimate elasticity — using default -1.0",
                company_id=self.company_id,
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
            company_id=self.company_id,
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
        self._validate_inputs(current_price, unit_cost, annual_units)
        if not sku or not segment or not isinstance(pricing_tier, PricingTier):
            raise ValueError("SKU, segment and a valid pricing tier are required")
        if (not isinstance(elasticity.ped, (int, float)) or isinstance(elasticity.ped, bool)
            or not math.isfinite(elasticity.ped)
            or not isinstance(elasticity.confidence, (int, float)) or isinstance(elasticity.confidence, bool)
            or not math.isfinite(elasticity.confidence) or not 0 <= elasticity.confidence <= 1
            or not isinstance(elasticity.sample_size, int) or isinstance(elasticity.sample_size, bool)
            or elasticity.sample_size < 0):
            raise ValueError("Invalid elasticity estimate")
        if elasticity.company_id and elasticity.company_id != self.company_id:
            raise ValueError("Elasticity evidence belongs to another tenant")
        current_margin = self._margin_pct(current_price, unit_cost)
        market_price = None
        if competitive_prices:
            if any(not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0
                   for p in competitive_prices):
                raise ValueError("Competitive prices must be positive and finite")
            market_price = statistics.mean(competitive_prices)

        # A default PED or thin observational sample is not actionable evidence.
        # Keep a draft hold recommendation until verified observations arrive.
        # Nonnegative observational PED is not evidence that raising prices
        # increases demand. Seasonality, promotions and stock-outs can confound
        # price/volume comparisons. Require manual causal review before any
        # change is modeled as a recommendation.
        if elasticity.sample_size < 3 or elasticity.confidence < 0.5 or elasticity.ped >= 0:
            return PricingRecommendation(
                sku=sku, segment=segment, pricing_tier=pricing_tier,
                current_price=current_price, recommended_price=current_price,
                price_change_pct=0.0, current_margin_pct=current_margin,
                projected_margin_pct=current_margin, revenue_impact_estimate=0.0,
                volume_impact_estimate_pct=0.0, confidence=elasticity.confidence,
                rationale="Insufficient or confounded price-change evidence; hold price for human review.",
                company_id=self.company_id,
            )

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
        volume_impact_pct = max(-100.0, elasticity.ped * price_change_pct)
        revenue_delta = annual_units * (
            recommended_price * (1 + volume_impact_pct / 100) - current_price
        )
        if not all(math.isfinite(x) for x in (recommended_price, volume_impact_pct, revenue_delta)):
            raise ValueError("Price or impact projection exceeds finite numeric range")

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
            company_id=self.company_id,
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
        self._validate_inputs(current_price, unit_cost_before, 0)
        self._validate_inputs(current_price, unit_cost_after, 0)
        if not supplier or not sku or not segment_data:
            raise ValueError("Supplier, SKU and segment evidence are required")
        if unit_cost_before <= 0 or unit_cost_after < unit_cost_before:
            raise ValueError("This scenario requires a positive starting cost and a non-decreasing cost")
        increase_pct = (unit_cost_after - unit_cost_before) / unit_cost_before * 100
        recommendations: list[PricingRecommendation] = []
        weighted_pass_through = 0.0
        total_units = 0.0
        seen_segments: set[str] = set()
        for segment in segment_data:
            if not isinstance(segment, dict):
                raise ValueError("Segment evidence must be an object")
            name = segment.get("segment")
            if (not isinstance(name, str) or not name.strip() or name in seen_segments
                or segment.get("company_id", self.company_id) != self.company_id
                or segment.get("sku", sku) != sku):
                raise ValueError("Segments must be unique and match the tenant and SKU")
            seen_segments.add(name)
            units = segment.get("annual_units", 0)
            if not isinstance(units, (int, float)) or isinstance(units, bool) or not math.isfinite(units) or units < 0:
                raise ValueError("Segment annual units must be nonnegative and finite")
            total_units += units
        if not math.isfinite(total_units) or total_units <= 0:
            raise ValueError("Positive, finite observed unit weights are required for a blended plan")

        for seg in segment_data:
            seg_name = seg.get("segment", "unknown")
            tier = PricingTier(seg.get("pricing_tier", "tier_2"))
            annual_units = seg.get("annual_units", 0)
            ped = seg.get("elasticity_ped")
            comp_prices = seg.get("competitive_prices")
            if ped is not None and (not isinstance(ped, (int, float)) or isinstance(ped, bool) or not math.isfinite(ped)):
                raise ValueError("Segment elasticity must be finite")

            # Determine pass-through strategy for this segment
            strategy, pass_through_pct, staged_days = self._choose_pass_through(
                ped=ped if ped is not None else -1.0, tier=tier, increase_pct=increase_pct
            )

            # Pass through a percentage of the *incremental cost dollars*.
            # Compute margin against actual new costs, never a fictitious
            # "effective cost" that would overstate profitability.
            proposed_price = current_price + (unit_cost_after - unit_cost_before) * pass_through_pct / 100
            change_pct = (proposed_price - current_price) / current_price * 100
            volume_pct = max(-100.0, (ped if ped is not None else -1.0) * change_pct)
            position = "unknown"
            if comp_prices:
                if any(not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0
                       for p in comp_prices):
                    raise ValueError("Competitive prices must be positive and finite")
                comparison = (proposed_price / statistics.mean(comp_prices) - 1) * 100
                position = "above_market" if comparison > 5 else "below_market" if comparison < -5 else "at_market"
            rec = PricingRecommendation(
                sku=sku, segment=seg_name, pricing_tier=tier,
                current_price=current_price, recommended_price=proposed_price,
                price_change_pct=change_pct,
                current_margin_pct=self._margin_pct(current_price, unit_cost_before),
                projected_margin_pct=self._margin_pct(proposed_price, unit_cost_after),
                revenue_impact_estimate=annual_units * (proposed_price * (1 + volume_pct / 100) - current_price),
                volume_impact_estimate_pct=volume_pct,
                confidence=0.0,
                rationale="Unverified supplier/segment scenario only; validate cost, sales, price-posting and approve before action.",
                competitive_position=position,
                company_id=self.company_id,
            )
            rec.pass_through_strategy = strategy
            rec.staged_timeline_days = staged_days
            if not all(math.isfinite(x) for x in (
                rec.recommended_price, rec.projected_margin_pct,
                rec.revenue_impact_estimate, rec.volume_impact_estimate_pct,
            )):
                raise ValueError("Supplier scenario exceeds finite numeric range")
            recommendations.append(rec)

            if total_units > 0:
                weighted_pass_through += pass_through_pct * (annual_units / total_units)

        # Margin impact scenarios
        full_absorption_margin = self._margin_pct(current_price, unit_cost_after)
        full_pass_through_price = current_price + (unit_cost_after - unit_cost_before)
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
            margin_impact_at_full_pass_through=full_pass_margin - current_margin,
            recommended_action_summary=summary,
            company_id=self.company_id,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _validate_inputs(price: float, cost: float, units: float) -> None:
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
               for v in (price, cost, units)):
            raise ValueError("Pricing inputs must be finite numbers")
        if price <= 0 or cost < 0 or units < 0:
            raise ValueError("Price must be positive; cost and units cannot be negative")

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
