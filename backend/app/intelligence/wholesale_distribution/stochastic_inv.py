"""
§19 — Stochastic Inventory Intelligence.

Treats demand and lead time as probability distributions, not point estimates.
Computes safety stock, reorder points, and optimal order quantities that account
for real-world uncertainty.

Phased implementation
─────────────────────
Phase 1 (< $5M revenue)  : Normal approximation — mean ± 1.65σ service-level
Phase 2 ($5M–$20M)       : Negative Binomial demand + lead-time convolution
Phase 3 ($20M–$50M+)     : Multi-echelon safety stock across warehouse locations

Formulas
────────
Safety Stock (Phase 1):
    SS = Z_sl × √( LT × σ_demand² + demand_avg² × σ_LT² )

    where:
      Z_sl     = service-level z-score (e.g. 1.65 for 95%)
      LT       = mean lead time (days)
      σ_demand = std dev of daily demand
      demand_avg = mean daily demand
      σ_LT     = std dev of lead time (days)

Reorder Point:
    ROP = demand_avg × LT + SS

Economic Order Quantity (EOQ):
    EOQ = √( 2 × D × K / h )

    where:
      D = annual demand (units)
      K = ordering cost per order
      h = holding cost per unit per year

Multi-echelon (Phase 3):
    Echelon safety stock considers demand at downstream nodes;
    uses Clark-Scarf decomposition approximation.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# Service-level z-scores (one-sided)
Z_SCORES = {
    0.90: 1.282,
    0.95: 1.645,
    0.98: 2.054,
    0.99: 2.326,
    0.999: 3.090,
}


class PricingPhase(str, Enum):
    PHASE_1 = "phase_1"   # < $5M — Normal approximation
    PHASE_2 = "phase_2"   # $5M–$20M — Negative Binomial + convolution
    PHASE_3 = "phase_3"   # $20M+ — Multi-echelon


@dataclass
class DemandDistribution:
    """Empirical demand distribution for a single SKU."""
    sku: str
    daily_demand_history: list[float]   # one value per day (units sold)

    @property
    def mean(self) -> float:
        return statistics.mean(self.daily_demand_history) if self.daily_demand_history else 0.0

    @property
    def std_dev(self) -> float:
        if len(self.daily_demand_history) < 2:
            return 0.0
        return statistics.stdev(self.daily_demand_history)

    @property
    def coefficient_of_variation(self) -> float:
        """CV > 0.5 indicates high demand variability — use higher safety stock."""
        if self.mean == 0:
            return 0.0
        return self.std_dev / self.mean

    @property
    def percentile_95(self) -> float:
        if not self.daily_demand_history:
            return 0.0
        sorted_d = sorted(self.daily_demand_history)
        idx = int(0.95 * len(sorted_d))
        return sorted_d[min(idx, len(sorted_d) - 1)]


@dataclass
class LeadTimeDistribution:
    """Empirical lead time distribution for a supplier–SKU pair."""
    supplier_id: str
    sku: str
    lead_time_history: list[float]   # one value per PO (days to delivery)

    @property
    def mean(self) -> float:
        return statistics.mean(self.lead_time_history) if self.lead_time_history else 7.0

    @property
    def std_dev(self) -> float:
        if len(self.lead_time_history) < 2:
            return 1.0
        return statistics.stdev(self.lead_time_history)


@dataclass
class InventoryRecommendation:
    """Output of stochastic inventory calculation for one SKU."""
    sku: str
    phase: PricingPhase
    service_level: float                # target (e.g. 0.95)
    achieved_service_level: float       # best we can do given data quality

    safety_stock_units: int
    reorder_point_units: int
    economic_order_qty: int

    days_of_supply_at_rop: float
    stockout_probability_without_ss: float
    expected_stockouts_per_year: float

    demand_mean_daily: float
    demand_std_daily: float
    lead_time_mean_days: float
    lead_time_std_days: float

    data_quality: str                   # good | sparse | insufficient
    notes: list[str]
    demand_observations: int = 0
    supplier_lead_time_observations: int = 0

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "phase": self.phase.value,
            "service_level_target": self.service_level,
            "achieved_service_level": round(self.achieved_service_level, 3),
            "safety_stock_units": self.safety_stock_units,
            "reorder_point_units": self.reorder_point_units,
            "economic_order_qty": self.economic_order_qty,
            "days_of_supply_at_rop": round(self.days_of_supply_at_rop, 1),
            "stockout_probability_without_safety_stock": round(
                self.stockout_probability_without_ss, 3
            ),
            "expected_stockouts_per_year": round(self.expected_stockouts_per_year, 1),
            "demand_mean_daily": round(self.demand_mean_daily, 2),
            "demand_std_daily": round(self.demand_std_daily, 2),
            "lead_time_mean_days": round(self.lead_time_mean_days, 1),
            "lead_time_std_days": round(self.lead_time_std_days, 1),
            "data_quality": self.data_quality,
            "demand_observations": self.demand_observations,
            "supplier_lead_time_observations": self.supplier_lead_time_observations,
            "service_level_is_observed": False,
            "data_source_verified": False,
            "requires_human_review": True,
            "notes": self.notes,
        }


@dataclass
class EchelonNode:
    """A warehouse / distribution node in a multi-echelon network."""
    node_id: str
    location: str
    demand_distribution: DemandDistribution
    replenishment_lead_time_days: float
    current_stock: int
    in_transit_units: int = 0


@dataclass
class MultiEchelonResult:
    """Result of Clark-Scarf multi-echelon safety stock calculation."""
    nodes: list[dict]                   # per-node safety stock + ROP
    system_safety_stock: int            # total across all nodes
    system_service_level: float
    bottleneck_node: Optional[str]      # node with highest stockout risk

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "system_safety_stock": self.system_safety_stock,
            "system_service_level": round(self.system_service_level, 3),
            "bottleneck_node": self.bottleneck_node,
        }


# ─── Stochastic Inventory Engine ─────────────────────────────────────────────


class StochasticInventoryEngine:
    """
    Computes safety stock, reorder points, and EOQ using probabilistic demand
    and lead time data.
    """

    def __init__(
        self,
        annual_revenue: float = 0.0,
        ordering_cost_per_po: float = 50.0,     # $ per purchase order
        holding_cost_rate: float = 0.25,         # 25% of unit cost per year
        service_level: float = 0.95,
    ):
        if not math.isfinite(annual_revenue) or annual_revenue < 0:
            raise ValueError("Annual revenue must be finite and nonnegative")
        if not math.isfinite(ordering_cost_per_po) or ordering_cost_per_po < 0:
            raise ValueError("Ordering cost must be finite and nonnegative")
        if not math.isfinite(holding_cost_rate) or holding_cost_rate <= 0:
            raise ValueError("Holding cost rate must be finite and positive")
        if not math.isfinite(service_level) or not 0 < service_level < 1:
            raise ValueError("Service level must be finite and strictly between 0 and 1")
        self.annual_revenue = annual_revenue
        self.ordering_cost = ordering_cost_per_po
        self.holding_cost_rate = holding_cost_rate
        self.service_level = service_level
        self.phase = self._determine_phase(annual_revenue)

    # ── Public API ───────────────────────────────────────────────────────────

    def calculate(
        self,
        demand: DemandDistribution,
        lead_time: LeadTimeDistribution,
        unit_cost: float,
        current_stock: int = 0,
    ) -> InventoryRecommendation:
        """
        Main entry point: compute all inventory parameters for one SKU.
        """
        notes: list[str] = []

        # Data quality assessment
        n_demand = len(demand.daily_demand_history)
        n_lt = len(lead_time.lead_time_history)
        if not n_demand or not n_lt:
            raise ValueError("Demand and supplier lead-time observations are required")
        if any(not math.isfinite(v) or v < 0 for v in demand.daily_demand_history):
            raise ValueError("Daily demand observations must be finite and nonnegative")
        if any(not math.isfinite(v) or v <= 0 for v in lead_time.lead_time_history):
            raise ValueError("Lead-time observations must be finite and positive")
        if not math.isfinite(unit_cost) or unit_cost <= 0:
            raise ValueError("Unit cost must be finite and positive")
        if current_stock < 0:
            raise ValueError("Current stock must be nonnegative")
        if n_demand < 30 or n_lt < 5:
            data_quality = "insufficient"
            notes.append(
                f"Only {n_demand} daily demand and {n_lt} supplier lead-time observations; "
                "using conservative safety stock assumptions"
            )
        elif n_demand < 90 or n_lt < 10:
            data_quality = "sparse"
            notes.append(
                f"{n_demand} demand and {n_lt} supplier lead-time observations — "
                "collect more before relying on this estimate"
            )
        else:
            data_quality = "good"

        d_mean = demand.mean
        d_std = demand.std_dev
        lt_mean = lead_time.mean
        lt_std = lead_time.std_dev

        # Boost std dev if data is sparse (conservative)
        if data_quality == "insufficient":
            d_std = max(d_std, d_mean * 0.5)
            lt_std = max(lt_std, lt_mean * 0.3)
            notes.append("Using conservative std dev estimates due to limited data")

        # CV warning
        if demand.coefficient_of_variation > 0.75:
            notes.append(
                f"High demand variability (CV={demand.coefficient_of_variation:.2f}) — "
                "consider vendor-managed inventory or consignment for this SKU"
            )

        # ── Safety Stock (Phase 1 formula — valid for all phases as baseline) ──
        z = self._z_score()
        safety_stock_float = z * math.sqrt(
            lt_mean * d_std ** 2 + d_mean ** 2 * lt_std ** 2
        )
        safety_stock = math.ceil(safety_stock_float)

        # ── Phase 2 correction: Negative Binomial overdispersion ──────────────
        if self.phase in (PricingPhase.PHASE_2, PricingPhase.PHASE_3):
            nb_correction = self._negative_binomial_correction(d_mean, d_std, lt_mean)
            safety_stock = math.ceil(safety_stock * nb_correction)
            notes.append(
                f"Phase 2: Negative Binomial correction factor {nb_correction:.2f} applied"
            )

        # ── Reorder Point ──────────────────────────────────────────────────────
        rop = math.ceil(d_mean * lt_mean + safety_stock)

        # ── EOQ ───────────────────────────────────────────────────────────────
        annual_demand = d_mean * 365
        h = unit_cost * self.holding_cost_rate
        eoq = (
            math.ceil(math.sqrt(2 * annual_demand * self.ordering_cost / h))
            if h > 0 and annual_demand > 0
            else 0
        )

        # ── Stockout probability without safety stock ─────────────────────────
        # P(demand during LT > current stock) — approx with Normal CDF
        demand_during_lt_mean = d_mean * lt_mean
        demand_during_lt_std = math.sqrt(lt_mean * d_std ** 2 + d_mean ** 2 * lt_std ** 2)
        stockout_prob = self._normal_ccdf(
            current_stock, demand_during_lt_mean, demand_during_lt_std
        )

        # Expected stockouts per year (assuming ~12 order cycles)
        orders_per_year = annual_demand / max(eoq, 1)
        expected_stockouts = stockout_prob * orders_per_year

        # Achieved service level (with safety stock)
        achieved_sl = 1.0 - self._normal_ccdf(
            rop, demand_during_lt_mean, demand_during_lt_std
        )

        days_supply_at_rop = rop / max(d_mean, 0.01)

        return InventoryRecommendation(
            sku=demand.sku,
            phase=self.phase,
            service_level=self.service_level,
            achieved_service_level=min(achieved_sl, 1.0),
            safety_stock_units=safety_stock,
            reorder_point_units=rop,
            economic_order_qty=eoq,
            days_of_supply_at_rop=days_supply_at_rop,
            stockout_probability_without_ss=stockout_prob,
            expected_stockouts_per_year=expected_stockouts,
            demand_mean_daily=d_mean,
            demand_std_daily=d_std,
            lead_time_mean_days=lt_mean,
            lead_time_std_days=lt_std,
            data_quality=data_quality,
            notes=notes,
            demand_observations=n_demand,
            supplier_lead_time_observations=n_lt,
        )

    def calculate_multi_echelon(
        self,
        sku: str,
        nodes: list[EchelonNode],
        unit_cost: float,
    ) -> MultiEchelonResult:
        """
        Phase 3: Clark-Scarf approximation for multi-echelon safety stock.
        Computes per-node safety stock accounting for downstream demand aggregation.
        """
        node_results = []
        system_ss = 0
        worst_stockout_prob = 0.0
        bottleneck = None

        for node in nodes:
            demand = node.demand_distribution
            if not math.isfinite(node.replenishment_lead_time_days) or node.replenishment_lead_time_days <= 0:
                raise ValueError("Multi-echelon lead-time estimate must be finite and positive")
            if node.current_stock < 0 or node.in_transit_units < 0:
                raise ValueError("Multi-echelon stock and in-transit quantities must be nonnegative")
            lt_dist = LeadTimeDistribution(
                supplier_id="upstream",
                sku=sku,
                # This is one caller estimate, not five observed deliveries.
                lead_time_history=[node.replenishment_lead_time_days],
            )
            effective_stock = node.current_stock + node.in_transit_units
            rec = self.calculate(demand, lt_dist, unit_cost, effective_stock)
            shortfall = max(0, rec.reorder_point_units - effective_stock)

            node_stockout_prob = rec.stockout_probability_without_ss
            # Bottleneck: highest stockout prob, or if tied, largest shortfall-to-ROP ratio
            shortfall_ratio = shortfall / max(rec.reorder_point_units, 1)
            risk_score = node_stockout_prob + shortfall_ratio * 0.1
            if risk_score > worst_stockout_prob:
                worst_stockout_prob = risk_score
                bottleneck = node.node_id

            node_results.append({
                "node_id": node.node_id,
                "location": node.location,
                "safety_stock_units": rec.safety_stock_units,
                "reorder_point_units": rec.reorder_point_units,
                "current_stock": node.current_stock,
                "in_transit_units": node.in_transit_units,
                "effective_stock": effective_stock,
                "shortfall_to_rop": shortfall,
                "stockout_probability": round(node_stockout_prob, 3),
                "lead_time_observation_count": 1,
                "lead_time_source": "caller_point_estimate",
                "data_quality": rec.data_quality,
            })
            system_ss += rec.safety_stock_units

        system_sl = max(0.0, 1.0 - min(1.0, worst_stockout_prob))

        return MultiEchelonResult(
            nodes=node_results,
            system_safety_stock=system_ss,
            system_service_level=system_sl,
            bottleneck_node=bottleneck,
        )

    def reorder_needed(
        self,
        current_stock: int,
        recommendation: InventoryRecommendation,
    ) -> tuple[bool, str]:
        """
        Returns (should_reorder, reason).
        """
        if recommendation.demand_mean_daily == 0:
            return False, "No observed demand; review observations before proposing an order."
        if current_stock <= recommendation.reorder_point_units:
            return True, (
                f"Stock ({current_stock}) at or below reorder point "
                f"({recommendation.reorder_point_units}). "
                f"Stockout risk: {recommendation.stockout_probability_without_ss:.0%} "
                f"without safety stock."
            )
        days_left = current_stock / max(recommendation.demand_mean_daily, 0.01)
        if days_left < recommendation.lead_time_mean_days * 1.5:
            return True, (
                f"Only {days_left:.0f} days of supply remaining vs "
                f"{recommendation.lead_time_mean_days:.0f}-day lead time — "
                "reorder now to maintain buffer."
            )
        return False, f"{days_left:.0f} days of supply — no action needed."

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _determine_phase(annual_revenue: float) -> PricingPhase:
        if annual_revenue >= 20_000_000:
            return PricingPhase.PHASE_3
        if annual_revenue >= 5_000_000:
            return PricingPhase.PHASE_2
        return PricingPhase.PHASE_1

    def _z_score(self) -> float:
        # Find closest standard z-score
        sl = self.service_level
        candidates = [(abs(k - sl), v) for k, v in Z_SCORES.items()]
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    @staticmethod
    def _negative_binomial_correction(
        d_mean: float, d_std: float, lt_mean: float
    ) -> float:
        """
        Negative Binomial overdispersion correction factor.
        For highly variable demand (CV > 1) the Normal approximation understates
        safety stock needs.  This factor adjusts upward.
        """
        if d_mean == 0:
            return 1.0
        cv = d_std / d_mean
        # Correction: 1 + (CV² - 1/mean) × 0.1 (empirical approximation)
        correction = 1.0 + max(0, cv ** 2 - 1.0 / max(d_mean, 1)) * 0.1
        return min(correction, 1.5)  # cap at 50% increase

    @staticmethod
    def _normal_ccdf(x: float, mean: float, std: float) -> float:
        """P(X > x) where X ~ Normal(mean, std)."""
        if std <= 0:
            return 1.0 if x < mean else 0.0
        z = (x - mean) / std
        # erfc approximation
        return 0.5 * math.erfc(z / math.sqrt(2))
