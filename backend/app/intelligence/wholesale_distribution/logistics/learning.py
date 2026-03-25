"""
Stage 9 — Learning & Refinement Engine.

Every completed procurement cycle feeds back into:
  • Lead time prediction improvement (per supplier, origin, route, season)
  • Demand pattern confirmation (seasonal indices, new account impact)
  • Cost trend analysis (12-month rolling trend per cost component)
  • Supplier performance scoring (lead time reliability, defect rate, responsiveness)
  • Broker performance scoring (clearance speed, hold rate)

The system literally gets smarter with every shipment.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

from .pipeline import LandedCostBreakdown, WarehouseReceipt


# ─── Lead Time Record ─────────────────────────────────────────────────────────


@dataclass
class LeadTimeRecord:
    """
    Stage-by-stage lead time for one completed procurement cycle.
    Tagged by all relevant dimensions for ML-quality predictions.
    """
    po_number: str
    supplier_id: str
    origin_country: str
    origin_port: str
    destination_port: str
    carrier: str = ""
    broker: str = ""
    product_category: str = ""
    month: int = 0          # 1–12 (season indicator)
    year: int = 0

    # Stage durations (days)
    po_to_ship: Optional[float] = None
    ship_to_arrival: Optional[float] = None
    arrival_to_clearance: Optional[float] = None
    clearance_to_warehouse: Optional[float] = None
    total: Optional[float] = None

    had_customs_hold: bool = False
    total_demurrage: float = 0.0
    predicted_total: Optional[float] = None  # what we predicted at PO time

    def variance_days(self) -> Optional[float]:
        if self.total is not None and self.predicted_total is not None:
            return self.total - self.predicted_total
        return None

    def to_dict(self) -> dict:
        return {
            "po_number": self.po_number,
            "supplier_id": self.supplier_id,
            "origin_country": self.origin_country,
            "carrier": self.carrier,
            "month": self.month,
            "stages": {
                "po_to_ship": self.po_to_ship,
                "ship_to_arrival": self.ship_to_arrival,
                "arrival_to_clearance": self.arrival_to_clearance,
                "clearance_to_warehouse": self.clearance_to_warehouse,
                "total": self.total,
            },
            "had_customs_hold": self.had_customs_hold,
            "demurrage": self.total_demurrage,
            "predicted": self.predicted_total,
            "variance_days": self.variance_days(),
        }


# ─── Supplier Performance Score ───────────────────────────────────────────────


@dataclass
class SupplierPerformanceScore:
    supplier_id: str
    supplier_name: str
    shipment_count: int = 0

    # Lead time reliability
    avg_lead_time_days: float = 0.0
    lead_time_std: float = 0.0
    lead_time_reliability_pct: float = 0.0  # % within ±10% of quoted

    # Responsiveness
    avg_response_hours: float = 0.0        # hours to confirm PO
    follow_up_needed_pct: float = 0.0      # % of POs requiring follow-up

    # Quality
    return_rate_pct: float = 0.0
    defect_rate_pct: float = 0.0

    # Pricing
    avg_fob_cost_trend_pct: float = 0.0    # annual % cost increase
    price_change_frequency: float = 0.0    # # price changes per year

    # Overall score (0–100)
    overall_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "shipment_count": self.shipment_count,
            "lead_time": {
                "avg_days": round(self.avg_lead_time_days, 1),
                "std_days": round(self.lead_time_std, 1),
                "reliability_pct": round(self.lead_time_reliability_pct, 1),
            },
            "responsiveness": {
                "avg_confirmation_hours": round(self.avg_response_hours, 1),
                "follow_up_needed_pct": round(self.follow_up_needed_pct, 1),
            },
            "quality": {
                "return_rate_pct": round(self.return_rate_pct, 2),
                "defect_rate_pct": round(self.defect_rate_pct, 2),
            },
            "pricing": {
                "cost_trend_pct_annual": round(self.avg_fob_cost_trend_pct, 1),
                "price_changes_per_year": round(self.price_change_frequency, 1),
            },
            "overall_score": round(self.overall_score, 1),
        }


# ─── Seasonal Index Builder ───────────────────────────────────────────────────


class SeasonalIndexBuilder:
    """
    Builds 52-week seasonal demand indices from historical sales data.
    Phase 1: identify pattern
    Phase 2 (2nd year): confirm pattern
    Phase 3 (3rd year): confident seasonal adjustment
    """

    def build_indices(
        self,
        daily_sales: list[float],
        # Ordered oldest to newest, at least 365 days for meaningful pattern
    ) -> dict:
        if len(daily_sales) < 182:  # need at least 6 months
            return {
                "status": "insufficient_data",
                "min_days_needed": 182,
                "days_available": len(daily_sales),
                "indices": [1.0] * 52,
            }

        years_of_data = len(daily_sales) / 365
        confidence = min(0.9, years_of_data / 3)

        # Aggregate into 52 buckets
        weekly_totals = [0.0] * 52
        weekly_counts = [0] * 52
        for i, v in enumerate(daily_sales):
            week = (i % 365) // 7
            week = min(week, 51)
            weekly_totals[week] += v
            weekly_counts[week] += 1

        weekly_avgs = [
            weekly_totals[w] / max(weekly_counts[w], 1)
            for w in range(52)
        ]
        overall_avg = statistics.mean(weekly_avgs)
        if overall_avg == 0:
            return {"status": "no_demand", "indices": [1.0] * 52}

        raw_indices = [avg / overall_avg for avg in weekly_avgs]

        # Smooth with a 3-week rolling average to avoid spiky noise
        smoothed = []
        for i in range(52):
            neighbors = [raw_indices[(i + j) % 52] for j in range(-1, 2)]
            smoothed.append(round(statistics.mean(neighbors), 3))

        status = (
            "confident" if years_of_data >= 2.5
            else "confirmatory" if years_of_data >= 1.5
            else "provisional"
        )

        peak_week = smoothed.index(max(smoothed))
        trough_week = smoothed.index(min(smoothed))

        return {
            "status": status,
            "years_of_data": round(years_of_data, 1),
            "confidence": round(confidence, 2),
            "indices": smoothed,
            "peak_week": peak_week + 1,    # 1-indexed
            "trough_week": trough_week + 1,
            "peak_multiplier": round(max(smoothed), 2),
            "trough_multiplier": round(min(smoothed), 2),
        }


# ─── Learning Engine ─────────────────────────────────────────────────────────


class LearningEngine:
    """
    Aggregates completed cycle data into improved predictions for future cycles.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id
        self._seasonal_builder = SeasonalIndexBuilder()

    def compute_lead_time_stats(
        self,
        records: list[LeadTimeRecord],
        supplier_id: Optional[str] = None,
        origin_country: Optional[str] = None,
        month: Optional[int] = None,
    ) -> dict:
        """
        Compute updated lead time statistics for a supplier/origin/season slice.
        Returns stats used to update reorder point calculations.
        """
        filtered = [
            r for r in records
            if (supplier_id is None or r.supplier_id == supplier_id)
            and (origin_country is None or r.origin_country == origin_country)
            and (month is None or abs(r.month - month) <= 1)  # ±1 month
            and r.total is not None
        ]

        if len(filtered) < 3:
            return {
                "status": "insufficient_data",
                "sample_size": len(filtered),
                "supplier_id": supplier_id,
            }

        totals = [r.total for r in filtered]
        mean = statistics.mean(totals)
        std = statistics.stdev(totals)
        p50 = self._percentile(totals, 50)
        p80 = self._percentile(totals, 80)
        p95 = self._percentile(totals, 95)

        # Trend: is lead time getting better or worse?
        trend = "stable"
        if len(filtered) >= 5:
            recent = statistics.mean([r.total for r in filtered[-3:]])
            older = statistics.mean([r.total for r in filtered[:3]])
            if recent > older * 1.1:
                trend = "worsening"
            elif recent < older * 0.9:
                trend = "improving"

        # Stage breakdown averages
        stage_avgs = {}
        for stage_attr in ["po_to_ship", "ship_to_arrival", "arrival_to_clearance", "clearance_to_warehouse"]:
            vals = [getattr(r, stage_attr) for r in filtered if getattr(r, stage_attr) is not None]
            if vals:
                stage_avgs[stage_attr] = round(statistics.mean(vals), 1)

        return {
            "status": "ok",
            "sample_size": len(filtered),
            "supplier_id": supplier_id,
            "origin_country": origin_country,
            "month": month,
            "mean_days": round(mean, 1),
            "std_days": round(std, 1),
            "p50_days": round(p50, 1),
            "p80_days": round(p80, 1),   # use this as the planning lead time
            "p95_days": round(p95, 1),
            "trend": trend,
            "stage_averages": stage_avgs,
            "prediction_accuracy": self._compute_accuracy(filtered),
        }

    def score_supplier(
        self,
        supplier_id: str,
        supplier_name: str,
        lead_time_records: list[LeadTimeRecord],
        response_hours: list[float],    # hours from PO sent to confirmation
        cost_history: list[LandedCostBreakdown],
        defect_rate_pct: float = 0.0,
        return_rate_pct: float = 0.0,
    ) -> SupplierPerformanceScore:
        """Compute a comprehensive supplier performance score."""
        score = SupplierPerformanceScore(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            shipment_count=len(lead_time_records),
        )

        # Lead time reliability
        if lead_time_records:
            totals = [r.total for r in lead_time_records if r.total is not None]
            if totals:
                score.avg_lead_time_days = statistics.mean(totals)
                score.lead_time_std = statistics.stdev(totals) if len(totals) > 1 else 0.0
                # Reliability: % within ±10% of the average
                reliable = sum(1 for t in totals if abs(t - score.avg_lead_time_days) / max(score.avg_lead_time_days, 1) <= 0.10)
                score.lead_time_reliability_pct = reliable / len(totals) * 100

        # Responsiveness
        if response_hours:
            score.avg_response_hours = statistics.mean(response_hours)
            score.follow_up_needed_pct = sum(1 for h in response_hours if h > 72) / len(response_hours) * 100

        # Quality
        score.return_rate_pct = return_rate_pct
        score.defect_rate_pct = defect_rate_pct

        # Pricing trend
        if len(cost_history) >= 2:
            prices = [c.fob_cost_per_case for c in cost_history if c.fob_cost_per_case > 0]
            if len(prices) >= 2:
                annual_pct = ((prices[-1] - prices[0]) / max(prices[0], 0.001)) * 100
                score.avg_fob_cost_trend_pct = annual_pct
                changes = sum(1 for i in range(1, len(prices)) if abs(prices[i] - prices[i-1]) > 0.001)
                score.price_change_frequency = changes / max(len(prices) / 12, 1)

        # Compute overall score (weighted)
        lt_score = min(100, score.lead_time_reliability_pct)
        resp_score = max(0, 100 - (score.avg_response_hours / 72 * 30))
        quality_score = max(0, 100 - score.defect_rate_pct * 10 - score.return_rate_pct * 5)
        pricing_score = max(0, 100 - abs(score.avg_fob_cost_trend_pct) * 2)

        score.overall_score = (
            lt_score * 0.35 + resp_score * 0.25 + quality_score * 0.25 + pricing_score * 0.15
        )
        return score

    def build_seasonal_indices(
        self,
        sku: str,
        daily_sales: list[float],
    ) -> dict:
        """Build seasonal demand indices for a SKU from its sales history."""
        result = self._seasonal_builder.build_indices(daily_sales)
        result["sku"] = sku
        return result

    def monthly_logistics_report(
        self,
        lead_time_records: list[LeadTimeRecord],
        cost_history_by_sku: dict[str, list[LandedCostBreakdown]],
        supplier_scores: list[SupplierPerformanceScore],
    ) -> dict:
        """
        Generate the monthly Logistics Intelligence Report.
        Covers cost trends, lead time trends, and supplier performance.
        """
        # Lead time summary
        if lead_time_records:
            totals = [r.total for r in lead_time_records if r.total is not None]
            avg_lt = round(statistics.mean(totals), 1) if totals else None
            variances = [r.variance_days() for r in lead_time_records if r.variance_days() is not None]
            avg_variance = round(statistics.mean(variances), 1) if variances else None
        else:
            avg_lt = None
            avg_variance = None

        # Cost trends across all SKUs
        total_cost_changes: list[float] = []
        for sku, history in cost_history_by_sku.items():
            if len(history) >= 2:
                first_cost = history[0].total_landed_cost_per_case
                last_cost = history[-1].total_landed_cost_per_case
                if first_cost > 0:
                    total_cost_changes.append(
                        (last_cost - first_cost) / first_cost * 100
                    )

        portfolio_cost_change = (
            round(statistics.mean(total_cost_changes), 1) if total_cost_changes else 0.0
        )

        # Top and bottom suppliers
        sorted_suppliers = sorted(supplier_scores, key=lambda s: s.overall_score, reverse=True)
        top_suppliers = [s.to_dict() for s in sorted_suppliers[:3]]
        bottom_suppliers = [s.to_dict() for s in sorted_suppliers[-2:] if s.overall_score < 70]

        return {
            "period": "last_12_months",
            "lead_time_summary": {
                "avg_days": avg_lt,
                "avg_variance_vs_predicted": avg_variance,
                "shipments_analyzed": len(lead_time_records),
            },
            "portfolio_landed_cost_change_pct": portfolio_cost_change,
            "top_performing_suppliers": top_suppliers,
            "underperforming_suppliers": bottom_suppliers,
            "recommendation": self._monthly_recommendation(
                portfolio_cost_change, avg_variance, bottom_suppliers
            ),
        }

    @staticmethod
    def _monthly_recommendation(
        cost_change: float, avg_variance: Optional[float], bottom_suppliers: list[dict]
    ) -> str:
        parts: list[str] = []
        if cost_change > 5:
            parts.append(f"Portfolio landed cost up {cost_change:.1f}% — review pricing strategy.")
        if avg_variance and avg_variance > 5:
            parts.append(
                f"Lead times running {avg_variance:.0f} days over forecast — "
                "review safety stock levels and increase planning buffers."
            )
        if bottom_suppliers:
            names = ", ".join(s.get("supplier_name", "") for s in bottom_suppliers[:2])
            parts.append(f"Supplier performance alerts: {names} — review relationship or qualify alternatives.")
        return " ".join(parts) or "Operations on track — no significant issues detected."

    @staticmethod
    def _percentile(data: list[float], p: int) -> float:
        sorted_data = sorted(data)
        idx = (p / 100) * (len(sorted_data) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(sorted_data) - 1)
        frac = idx - lo
        return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])

    @staticmethod
    def _compute_accuracy(records: list[LeadTimeRecord]) -> dict:
        variances = [r.variance_days() for r in records if r.variance_days() is not None]
        if not variances:
            return {"status": "no_predictions_available"}
        mae = statistics.mean([abs(v) for v in variances])
        bias = statistics.mean(variances)
        within_5_days = sum(1 for v in variances if abs(v) <= 5) / len(variances) * 100
        return {
            "mae_days": round(mae, 1),
            "bias_days": round(bias, 1),
            "within_5_days_pct": round(within_5_days, 1),
        }
