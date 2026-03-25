"""
Stage 1 — Demand Monitoring & Reorder Intelligence.

Two supply chain profiles:
  Profile A (Overseas): probabilistic forecasting, 80th-percentile lead time,
                        service-level safety stock per SKU tier
  Profile B (Domestic): velocity monitoring, days-of-supply trigger,
                        supplier-responsiveness-adjusted buffer

Every triggered reorder generates a complete ReorderDecisionPackage.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Optional

from .pipeline import (
    DemandProjection,
    ReorderDecisionPackage,
    SKUProfile,
    SupplyChainType,
    UrgencyLevel,
)

# Service level z-scores (one-tailed)
_Z = {0.88: 1.175, 0.93: 1.476, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326}

# Urgency thresholds: days of net supply remaining
_URGENCY_THRESHOLDS = {
    SupplyChainType.OVERSEAS: {"red": 0.9, "yellow": 1.25},   # multiples of lead time
    SupplyChainType.DOMESTIC: {"red": 1.2, "yellow": 2.0},    # multiples of lead time
}


class ReorderEngine:
    """
    Monitors a portfolio of SKUs and generates ReorderDecisionPackages
    for any that require replenishment action.
    """

    def __init__(
        self,
        company_id: str,
        default_freight_cost_per_case: float = 4.50,
        default_duty_rate_pct: float = 0.0,
    ):
        self.company_id = company_id
        self.default_freight_cost_per_case = default_freight_cost_per_case
        self.default_duty_rate_pct = default_duty_rate_pct

    # ── Public API ───────────────────────────────────────────────────────────

    def evaluate_sku(
        self,
        profile: SKUProfile,
        daily_sales_history: list[float],       # units/cases sold per day, newest last
        lead_time_history: list[float],         # days per completed cycle, newest last
        seasonal_indices: Optional[list[float]] = None,  # 52 weekly multipliers
        last_order_reference: str = "",
        last_fob_price: Optional[float] = None,
        last_freight_cost_per_case: Optional[float] = None,
        shipments_per_year: int = 12,
    ) -> Optional[ReorderDecisionPackage]:
        """
        Evaluate one SKU.  Returns a ReorderDecisionPackage if action is needed,
        or None if stock level is comfortable.
        """
        if not daily_sales_history:
            return None

        # ── 1. Compute demand statistics ─────────────────────────────────────
        trailing_90 = daily_sales_history[-90:] if len(daily_sales_history) >= 90 else daily_sales_history
        avg_daily = statistics.mean(trailing_90)
        std_daily = statistics.stdev(trailing_90) if len(trailing_90) > 1 else avg_daily * 0.3

        # ── 2. Compute lead time statistics ──────────────────────────────────
        if lead_time_history:
            lt_mean = statistics.mean(lead_time_history)
            lt_std = statistics.stdev(lead_time_history) if len(lead_time_history) > 1 else lt_mean * 0.15
            lt_p80 = self._percentile(lead_time_history, 80)
        else:
            lt_mean = 60.0 if profile.supply_chain_type == SupplyChainType.OVERSEAS else 3.0
            lt_std = lt_mean * 0.15
            lt_p80 = lt_mean * 1.2

        planning_lt = lt_p80  # use 80th percentile as planning lead time

        # ── 3. Safety stock ──────────────────────────────────────────────────
        z = _Z.get(profile.service_level_target, 1.645)
        safety_stock = math.ceil(
            z * math.sqrt(planning_lt * std_daily ** 2 + avg_daily ** 2 * lt_std ** 2)
        )

        # ── 4. Net available inventory ───────────────────────────────────────
        net_available = profile.stock_on_hand - profile.stock_committed + profile.stock_in_transit
        days_of_supply = net_available / max(avg_daily, 0.001)

        # ── 5. Reorder point ─────────────────────────────────────────────────
        rop = avg_daily * planning_lt + safety_stock

        # ── 6. Urgency ───────────────────────────────────────────────────────
        thresholds = _URGENCY_THRESHOLDS[profile.supply_chain_type]
        urgency = self._compute_urgency(
            days_of_supply=days_of_supply,
            lead_time_days=planning_lt,
            net_available=net_available,
            rop=rop,
            thresholds=thresholds,
        )

        if urgency == UrgencyLevel.GREEN and net_available > rop * 1.5:
            return None  # comfortable — no action needed

        # ── 7. Demand projection (14 weeks) ──────────────────────────────────
        projection = self._build_projection(
            avg_daily=avg_daily,
            std_daily=std_daily,
            seasonal_indices=seasonal_indices,
        )

        # ── 8. Recommended order quantity ────────────────────────────────────
        # Cover demand through planning LT + safety stock, minus in-transit
        demand_during_lt = avg_daily * planning_lt
        already_covered = max(0, profile.stock_in_transit - (rop - profile.stock_on_hand))
        raw_qty = max(0, math.ceil(demand_during_lt + safety_stock - max(net_available, 0)))
        raw_qty = max(raw_qty, profile.minimum_order_cases)

        # Round to full pallet
        if profile.cases_per_pallet > 0:
            raw_qty = math.ceil(raw_qty / profile.cases_per_pallet) * profile.cases_per_pallet

        # Container fill analysis (overseas only)
        container_fill_pct = 0.0
        container_suggestion = ""
        if profile.supply_chain_type == SupplyChainType.OVERSEAS:
            cap = profile.cases_per_40hc_container or 1_800
            container_fill_pct = (raw_qty / cap) * 100
            if 50 <= container_fill_pct < 80:
                fill_gap = cap * 0.90 - raw_qty
                container_suggestion = (
                    f"Order fills {container_fill_pct:.0f}% of a 40HC container. "
                    f"Adding ~{fill_gap:.0f} cases of other SKUs from this supplier "
                    f"would reach 90% fill and reduce freight cost per case by "
                    f"~{self.default_freight_cost_per_case * (1 - container_fill_pct / 90) :.2f}."
                )
            elif container_fill_pct > 95:
                container_suggestion = "Order is near full container — optimal freight economics."

        # ── 9. Cost estimate ─────────────────────────────────────────────────
        fob = last_fob_price or profile.unit_cost_fob
        freight = last_freight_cost_per_case or self.default_freight_cost_per_case
        duty = fob * (self.default_duty_rate_pct / 100)
        landed = fob + freight + duty

        price_change_flag = bool(last_fob_price and abs(last_fob_price - profile.unit_cost_fob) > 0.001)
        price_change_note = ""
        if price_change_flag:
            delta = last_fob_price - profile.unit_cost_fob
            price_change_note = (
                f"Price changed from ${profile.unit_cost_fob:.4f} to ${last_fob_price:.4f} "
                f"({'+'  if delta > 0 else ''}{delta:.4f}/case). Confirm before sending PO."
            )

        # ── 10. Stockout date ────────────────────────────────────────────────
        stockout_date = None
        if avg_daily > 0 and net_available > 0:
            stockout_days = net_available / avg_daily
            stockout_date = (date.today() + timedelta(days=stockout_days)).isoformat()

        # ── 11. Reasoning ────────────────────────────────────────────────────
        reasoning = self._build_reasoning(
            profile=profile,
            avg_daily=avg_daily,
            planning_lt=planning_lt,
            safety_stock=safety_stock,
            raw_qty=raw_qty,
            net_available=net_available,
            rop=rop,
        )

        pkg = ReorderDecisionPackage(
            sku_profile=profile,
            urgency=urgency,
            net_available=max(0, net_available),
            days_of_supply=max(0, days_of_supply),
            projected_stockout_date=stockout_date,
            demand_projection=projection,
            avg_daily_demand=avg_daily,
            recommended_qty=raw_qty,
            recommended_qty_reasoning=reasoning,
            container_fill_pct=container_fill_pct,
            container_suggestion=container_suggestion,
            expected_lead_time_days=lt_mean,
            lead_time_p80_days=lt_p80,
            estimated_fob_cost=fob * raw_qty,
            estimated_freight_cost=freight * raw_qty,
            estimated_landed_cost=landed * raw_qty,
            last_order_reference=last_order_reference,
            price_change_flag=price_change_flag,
            price_change_note=price_change_note,
        )
        return pkg

    def build_reorder_queue(
        self,
        sku_data: list[dict],
    ) -> list[ReorderDecisionPackage]:
        """
        Evaluate a full portfolio and return all packages that need action,
        sorted by urgency (RED first).
        """
        packages: list[ReorderDecisionPackage] = []

        for item in sku_data:
            profile = item.get("profile")
            if not isinstance(profile, SKUProfile):
                continue
            pkg = self.evaluate_sku(
                profile=profile,
                daily_sales_history=item.get("daily_sales_history", []),
                lead_time_history=item.get("lead_time_history", []),
                seasonal_indices=item.get("seasonal_indices"),
                last_order_reference=item.get("last_order_reference", ""),
                last_fob_price=item.get("last_fob_price"),
                last_freight_cost_per_case=item.get("last_freight_cost_per_case"),
                shipments_per_year=item.get("shipments_per_year", 12),
            )
            if pkg:
                packages.append(pkg)

        urgency_order = {UrgencyLevel.RED: 0, UrgencyLevel.YELLOW: 1, UrgencyLevel.GREEN: 2}
        packages.sort(key=lambda p: urgency_order[p.urgency])
        return packages

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_urgency(
        days_of_supply: float,
        lead_time_days: float,
        net_available: int,
        rop: float,
        thresholds: dict,
    ) -> UrgencyLevel:
        # Immediate stockout risk
        if net_available <= 0:
            return UrgencyLevel.RED
        # Below safety stock even if lead time is minimum
        if days_of_supply < lead_time_days * thresholds["red"]:
            return UrgencyLevel.RED
        if days_of_supply < lead_time_days * thresholds["yellow"]:
            return UrgencyLevel.YELLOW
        # Below ROP (standard reorder trigger)
        if net_available < rop:
            return UrgencyLevel.YELLOW
        return UrgencyLevel.GREEN

    @staticmethod
    def _build_projection(
        avg_daily: float,
        std_daily: float,
        seasonal_indices: Optional[list[float]] = None,
    ) -> DemandProjection:
        """Build a 14-week demand projection with confidence bounds."""
        today = date.today()
        weekly: list[float] = []
        low: list[float] = []
        high: list[float] = []

        for w in range(14):
            # Determine week number for seasonal adjustment
            week_num = (today + timedelta(weeks=w)).isocalendar()[1] - 1  # 0-indexed
            seasonal_mult = 1.0
            if seasonal_indices and len(seasonal_indices) >= 52:
                seasonal_mult = seasonal_indices[week_num % 52]

            base = avg_daily * 7 * seasonal_mult
            weekly.append(round(base, 1))
            low.append(round(max(0, base - std_daily * 7 * 1.28), 1))   # ~10th pct
            high.append(round(base + std_daily * 7 * 1.28, 1))           # ~90th pct

        return DemandProjection(
            weekly_demand=weekly,
            weekly_demand_low=low,
            weekly_demand_high=high,
            seasonal_adjustment_applied=seasonal_indices is not None,
            confidence=0.75 if len(weekly) == 14 else 0.5,
        )

    @staticmethod
    def _build_reasoning(
        profile: SKUProfile,
        avg_daily: float,
        planning_lt: float,
        safety_stock: int,
        raw_qty: int,
        net_available: int,
        rop: float,
    ) -> str:
        lt_label = (
            f"{planning_lt:.0f} days (80th percentile of {profile.supply_chain_type.value} lead time)"
        )
        return (
            f"Net available: {net_available} cases. "
            f"Avg daily demand: {avg_daily:.1f} cases/day. "
            f"Planning lead time: {lt_label}. "
            f"Safety stock: {safety_stock} cases "
            f"(service level target: {profile.service_level_target:.0%}). "
            f"Reorder point: {rop:.0f} cases. "
            f"Recommended order: {raw_qty} cases "
            f"(covers {planning_lt:.0f}-day lead time + safety stock, "
            f"rounded to full pallet of {profile.cases_per_pallet})."
        )

    @staticmethod
    def _percentile(data: list[float], p: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = (p / 100) * (len(sorted_data) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(sorted_data) - 1)
        frac = idx - lo
        return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])
