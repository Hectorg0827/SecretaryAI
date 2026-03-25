"""
Stage 8 — Cost Reconciliation & Variance Detection.

The margin protection engine. Compares every cost component of the current
shipment against the previous shipment, line by line. Any variance outside
the configured threshold fires an alert with:
  • Per-case impact calculation
  • Annualized margin impact
  • Historical trend (last 5 shipments)
  • Whether a customer pricing adjustment is warranted

This is the module that catches the $0.50/case FOB increase buried in a
Spanish-language invoice — on the first invoice, within hours of receipt.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from .pipeline import (
    CostVariance,
    FreightBooking,
    LandedCostBreakdown,
    WarehouseReceipt,
)
from .freight import ALERT_THRESHOLDS, FREIGHT_COMPONENTS


# ─── Cost Reconciliation Engine ───────────────────────────────────────────────


class CostReconciliationEngine:
    """
    Builds LandedCostBreakdown from receipt + freight + customs data,
    then diffs it against the previous shipment's landed cost ledger entry.
    """

    def __init__(
        self,
        company_id: str,
        target_gross_margin_pct: float = 30.0,
        shipments_per_year: int = 12,
    ):
        self.company_id = company_id
        self.target_margin = target_gross_margin_pct
        self.shipments_per_year = shipments_per_year

    # ── Build landed cost ────────────────────────────────────────────────────

    def build_landed_cost(
        self,
        po_number: str,
        sku: str,
        cases: int,
        supplier_id: str,
        fob_cost_per_case: float,
        freight_booking: Optional[FreightBooking],
        duty_rate_pct: float = 0.0,
        shipment_date: Optional[str] = None,
    ) -> LandedCostBreakdown:
        """
        Assemble a LandedCostBreakdown from all available cost data.
        Freight costs are allocated per case from the booking's cost components.
        """
        lcd = LandedCostBreakdown(
            po_number=po_number,
            sku=sku,
            cases=cases,
            supplier_id=supplier_id,
            fob_cost_per_case=fob_cost_per_case,
            shipment_date=shipment_date or date.today().isoformat(),
        )

        # Duty on FOB value
        lcd.customs_duty_per_case = fob_cost_per_case * (duty_rate_pct / 100)

        # Allocate freight costs across cases
        if freight_booking and freight_booking.cost_components:
            total_cases_in_booking = max(cases, 1)
            for comp in freight_booking.cost_components:
                per_case = comp.amount_usd / total_cases_in_booking
                if comp.component == "ocean_freight":
                    lcd.ocean_freight_per_case = per_case
                elif comp.component == "fuel_surcharge":
                    lcd.fuel_surcharge_per_case = per_case
                elif comp.component == "origin_charges":
                    lcd.origin_charges_per_case = per_case
                elif comp.component == "insurance":
                    lcd.insurance_per_case = per_case
                elif comp.component == "broker_fees":
                    lcd.broker_fees_per_case = per_case
                elif comp.component == "port_charges":
                    lcd.port_charges_per_case = per_case
                elif comp.component == "demurrage":
                    lcd.demurrage_per_case = per_case
                elif comp.component == "drayage":
                    lcd.drayage_per_case = per_case
                elif comp.component == "exam_fees":
                    lcd.exam_fees_per_case = per_case

        return lcd

    # ── Variance Detection ────────────────────────────────────────────────────

    def detect_variances(
        self,
        current: LandedCostBreakdown,
        previous: LandedCostBreakdown,
        selling_price_per_case: float = 0.0,
    ) -> list[CostVariance]:
        """
        Compare current and previous landed cost breakdowns.
        Returns a list of CostVariance objects for any significant changes.
        """
        variances: list[CostVariance] = []

        comparisons: list[tuple[str, float, float]] = [
            ("fob_cost",         current.fob_cost_per_case,          previous.fob_cost_per_case),
            ("ocean_freight",    current.ocean_freight_per_case,     previous.ocean_freight_per_case),
            ("fuel_surcharge",   current.fuel_surcharge_per_case,    previous.fuel_surcharge_per_case),
            ("origin_charges",   current.origin_charges_per_case,    previous.origin_charges_per_case),
            ("insurance",        current.insurance_per_case,         previous.insurance_per_case),
            ("customs_duty",     current.customs_duty_per_case,      previous.customs_duty_per_case),
            ("broker_fees",      current.broker_fees_per_case,       previous.broker_fees_per_case),
            ("port_charges",     current.port_charges_per_case,      previous.port_charges_per_case),
            ("demurrage",        current.demurrage_per_case,         previous.demurrage_per_case),
            ("drayage",          current.drayage_per_case,           previous.drayage_per_case),
            ("exam_fees",        current.exam_fees_per_case,         previous.exam_fees_per_case),
        ]

        for component, curr_val, prev_val in comparisons:
            variance = self._evaluate_component(
                component=component,
                current_value=curr_val,
                previous_value=prev_val,
                cases=current.cases,
                selling_price_per_case=selling_price_per_case,
                po_number=current.po_number,
                sku=current.sku,
            )
            if variance:
                variances.append(variance)

        return variances

    def _evaluate_component(
        self,
        component: str,
        current_value: float,
        previous_value: float,
        cases: int,
        selling_price_per_case: float,
        po_number: str,
        sku: str,
    ) -> Optional[CostVariance]:
        # No change at all
        if abs(current_value - previous_value) < 0.001:
            return None

        # Components that always alert when non-zero (demurrage, exam fees)
        always_alert = component in ("demurrage", "exam_fees")

        threshold = ALERT_THRESHOLDS.get(component, 5.0)
        if previous_value == 0:
            if current_value > 0:
                pct_change = 100.0
            else:
                return None
        else:
            pct_change = ((current_value - previous_value) / previous_value) * 100

        if not always_alert and abs(pct_change) < threshold:
            return None  # Within acceptable variance

        change_usd_per_case = current_value - previous_value
        total_change = change_usd_per_case * cases
        annualized = total_change * self.shipments_per_year

        severity = self._compute_severity(
            component=component,
            pct_change=pct_change,
            annualized_impact=annualized,
            is_always_alert=always_alert,
        )

        # Check if pricing adjustment is needed to maintain margin
        pricing_adjustment_note = ""
        if selling_price_per_case > 0 and change_usd_per_case > 0:
            current_margin = (selling_price_per_case - current_value) / selling_price_per_case
            required_new_price = current_value / (1 - self.target_margin / 100)
            if required_new_price > selling_price_per_case * 1.01:
                pricing_adjustment_note = (
                    f"Customer price adjustment of ${required_new_price - selling_price_per_case:.2f}/case "
                    f"needed to maintain {self.target_margin:.0f}% margin."
                )

        return CostVariance(
            po_number=po_number,
            sku=sku,
            component=component,
            previous_value=previous_value,
            current_value=current_value,
            change_usd=total_change,
            change_pct=pct_change,
            per_case_impact=change_usd_per_case,
            annualized_impact=annualized,
            severity=severity,
        )

    @staticmethod
    def _compute_severity(
        component: str,
        pct_change: float,
        annualized_impact: float,
        is_always_alert: bool,
    ) -> str:
        if is_always_alert:
            return "critical" if abs(annualized_impact) > 1_000 else "alert"
        if abs(annualized_impact) > 20_000:
            return "critical"
        if abs(annualized_impact) > 10_000:
            return "alert"
        if abs(pct_change) > 20 or abs(annualized_impact) > 5_000:
            return "warning"
        return "info"

    # ── Trend Analysis ───────────────────────────────────────────────────────

    def analyze_cost_trends(
        self,
        cost_history: list[LandedCostBreakdown],
        # Ordered oldest-first, most recent last
    ) -> dict:
        """
        Analyze cost trends across the last N shipments.
        Returns trend summary for the monthly Logistics Intelligence Report.
        """
        if len(cost_history) < 2:
            return {"status": "insufficient_data", "shipments_analyzed": len(cost_history)}

        first = cost_history[0]
        last = cost_history[-1]
        period_count = len(cost_history)

        total_change_pct = (
            (last.total_landed_cost_per_case - first.total_landed_cost_per_case)
            / max(first.total_landed_cost_per_case, 0.01)
        ) * 100

        # Break down by component
        component_trends: dict[str, dict] = {}
        for attr, label in [
            ("fob_cost_per_case", "supplier_pricing"),
            ("ocean_freight_per_case", "ocean_freight"),
            ("fuel_surcharge_per_case", "fuel_surcharge"),
            ("customs_duty_per_case", "duties"),
            ("drayage_per_case", "port_and_drayage"),
        ]:
            vals = [getattr(s, attr) for s in cost_history if getattr(s, attr) > 0]
            if len(vals) < 2:
                continue
            change_pct = ((vals[-1] - vals[0]) / max(vals[0], 0.001)) * 100
            avg = statistics.mean(vals)
            component_trends[label] = {
                "first_value": round(vals[0], 4),
                "latest_value": round(vals[-1], 4),
                "change_pct": round(change_pct, 1),
                "trend": "rising" if change_pct > 2 else "falling" if change_pct < -2 else "stable",
                "avg_per_case": round(avg, 4),
            }

        # Biggest driver of total cost change
        drivers = sorted(
            component_trends.items(),
            key=lambda kv: abs(kv[1]["change_pct"]),
            reverse=True,
        )
        biggest_driver = drivers[0][0].replace("_", " ") if drivers else "unknown"

        # Recommendation
        addressable = {k for k, v in component_trends.items() if v["trend"] == "rising"}
        if "ocean_freight" in addressable or "fuel_surcharge" in addressable:
            recommendation = (
                "Freight cost is the primary addressable driver — "
                "consider rebidding the freight contract or consolidating shipments."
            )
        elif "supplier_pricing" in addressable:
            recommendation = (
                "Supplier FOB pricing is trending up. "
                "Schedule a pricing review — use volume consolidation as leverage."
            )
        elif not addressable:
            recommendation = "All major cost components are stable. No action required."
        else:
            recommendation = (
                f"Multiple cost components rising: {', '.join(addressable)}. "
                "Review each with the relevant vendor."
            )

        return {
            "shipments_analyzed": period_count,
            "total_landed_cost_change_pct": round(total_change_pct, 1),
            "first_shipment_date": first.shipment_date,
            "latest_shipment_date": last.shipment_date,
            "component_trends": component_trends,
            "biggest_cost_driver": biggest_driver,
            "recommendation": recommendation,
        }

    # ── Variance Alert Summary ───────────────────────────────────────────────

    def format_variance_alert(
        self,
        variance: CostVariance,
        sku_name: str = "",
        cases: int = 0,
    ) -> str:
        """Format a variance as a human-readable alert message."""
        direction = "increased" if variance.change_usd > 0 else "decreased"
        component_label = FREIGHT_COMPONENTS.get(variance.component, variance.component.replace("_", " ").title())
        msg = (
            f"{component_label} on {sku_name or variance.sku} {direction} "
            f"${abs(variance.change_usd):,.2f} ({variance.change_pct:+.1f}%) "
            f"vs. previous shipment. "
            f"Per-case impact: ${abs(variance.per_case_impact):.4f}. "
            f"Annualized impact if persistent: ${abs(variance.annualized_impact):,.0f}."
        )
        if variance.component in ("demurrage", "exam_fees"):
            msg += " (Unplanned cost — investigate root cause to prevent recurrence.)"
        return msg
