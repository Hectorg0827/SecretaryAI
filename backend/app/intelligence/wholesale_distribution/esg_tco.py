"""
§24 — ESG Metrics + Total Cost of Ownership.

Tracks environmental, social, and governance (ESG) metrics relevant to
wholesale distribution, and computes Total Cost of Ownership (TCO) for
supplier/product decisions.

ESG metrics (distribution-relevant)
─────────────────────────────────────
  Environmental:
    - Carbon footprint per case shipped (kgCO₂e per case)
    - Scope 1: direct emissions (company-owned fleet)
    - Scope 2: indirect emissions (purchased electricity for warehouses)
    - Scope 3 upstream: supplier transportation + manufacturing
    - Scope 3 downstream: customer delivery miles
    - Packaging waste per unit shipped (kg)
    - Percentage recycled / recyclable packaging
    - Water intensity (gallons per $1k revenue)

  Social:
    - Driver/warehouse safety incidents per 100 employees
    - Supplier audit pass rate (% of suppliers with ethical sourcing cert)
    - Community impact (local sourcing %)

  Governance:
    - On-time supplier payment rate (% within agreed terms)
    - Compliance rate (import documentation accuracy)

Total Cost of Ownership (TCO)
──────────────────────────────
  TCO includes all costs associated with a supplier/SKU over the full lifecycle:
    Purchase price × volume
    + Inbound freight
    + Customs duties + brokerage
    + Warehousing cost (days of stock × holding cost per day)
    + Quality failure cost (return rate × handling + replacement)
    + Carbon cost (at internal carbon price)
    + Working capital cost (DIO × WACC)
    - Volume rebates

  TCO / unit enables true apples-to-apples supplier comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─── ESG Data Structures ─────────────────────────────────────────────────────


@dataclass
class CarbonFootprint:
    """Carbon footprint breakdown for a shipment lane or product category."""
    scope_1_kg_co2e: float = 0.0        # owned fleet emissions
    scope_2_kg_co2e: float = 0.0        # warehouse electricity
    scope_3_upstream_kg_co2e: float = 0.0
    scope_3_downstream_kg_co2e: float = 0.0

    @property
    def total_kg_co2e(self) -> float:
        return (
            self.scope_1_kg_co2e
            + self.scope_2_kg_co2e
            + self.scope_3_upstream_kg_co2e
            + self.scope_3_downstream_kg_co2e
        )

    def to_dict(self) -> dict:
        return {
            "scope_1_kg_co2e": round(self.scope_1_kg_co2e, 3),
            "scope_2_kg_co2e": round(self.scope_2_kg_co2e, 3),
            "scope_3_upstream_kg_co2e": round(self.scope_3_upstream_kg_co2e, 3),
            "scope_3_downstream_kg_co2e": round(self.scope_3_downstream_kg_co2e, 3),
            "total_kg_co2e": round(self.total_kg_co2e, 3),
        }


@dataclass
class ESGSnapshot:
    """Point-in-time ESG metrics for a company or supplier."""
    entity_id: str
    entity_type: str                    # company | supplier | lane | sku

    # Environmental
    carbon_per_case_kg_co2e: float = 0.0
    packaging_waste_kg_per_unit: float = 0.0
    recyclable_packaging_pct: float = 0.0
    water_intensity_gal_per_k_revenue: float = 0.0

    # Social
    safety_incidents_per_100_employees: float = 0.0
    supplier_audit_pass_rate_pct: float = 0.0
    local_sourcing_pct: float = 0.0

    # Governance
    on_time_payment_rate_pct: float = 0.0
    import_compliance_rate_pct: float = 0.0

    # Benchmarks (filled by engine)
    environmental_score: float = 0.0    # 0–100
    social_score: float = 0.0
    governance_score: float = 0.0
    overall_esg_score: float = 0.0
    improvement_opportunities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "environmental": {
                "carbon_per_case_kg_co2e": round(self.carbon_per_case_kg_co2e, 4),
                "packaging_waste_kg_per_unit": round(self.packaging_waste_kg_per_unit, 4),
                "recyclable_packaging_pct": round(self.recyclable_packaging_pct, 1),
                "water_intensity_gal_per_k_revenue": round(self.water_intensity_gal_per_k_revenue, 2),
            },
            "social": {
                "safety_incidents_per_100_employees": round(self.safety_incidents_per_100_employees, 2),
                "supplier_audit_pass_rate_pct": round(self.supplier_audit_pass_rate_pct, 1),
                "local_sourcing_pct": round(self.local_sourcing_pct, 1),
            },
            "governance": {
                "on_time_payment_rate_pct": round(self.on_time_payment_rate_pct, 1),
                "import_compliance_rate_pct": round(self.import_compliance_rate_pct, 1),
            },
            "scores": {
                "environmental": round(self.environmental_score, 1),
                "social": round(self.social_score, 1),
                "governance": round(self.governance_score, 1),
                "overall": round(self.overall_esg_score, 1),
            },
            "improvement_opportunities": self.improvement_opportunities,
        }


# ─── TCO Data Structures ─────────────────────────────────────────────────────


@dataclass
class TCOBreakdown:
    """Full TCO breakdown for one supplier × SKU combination."""
    supplier_id: str
    sku: str
    annual_volume: float                # units per year

    # Cost components (per unit)
    purchase_price_per_unit: float = 0.0
    inbound_freight_per_unit: float = 0.0
    customs_duty_per_unit: float = 0.0
    brokerage_per_unit: float = 0.0
    warehousing_per_unit: float = 0.0   # days_of_stock × holding_rate × unit_cost / 365
    quality_failure_per_unit: float = 0.0  # return_rate × (return_handling + replacement_cost)
    carbon_cost_per_unit: float = 0.0   # kg_co2e × internal_carbon_price
    working_capital_cost_per_unit: float = 0.0  # DIO / 365 × unit_cost × WACC
    volume_rebate_per_unit: float = 0.0  # negative (discount)

    @property
    def total_tco_per_unit(self) -> float:
        return (
            self.purchase_price_per_unit
            + self.inbound_freight_per_unit
            + self.customs_duty_per_unit
            + self.brokerage_per_unit
            + self.warehousing_per_unit
            + self.quality_failure_per_unit
            + self.carbon_cost_per_unit
            + self.working_capital_cost_per_unit
            - self.volume_rebate_per_unit
        )

    @property
    def total_tco_annual(self) -> float:
        return self.total_tco_per_unit * self.annual_volume

    @property
    def non_purchase_cost_pct(self) -> float:
        """What % of TCO is beyond the invoice price."""
        if self.total_tco_per_unit <= 0:
            return 0.0
        hidden_costs = self.total_tco_per_unit - self.purchase_price_per_unit
        return (hidden_costs / self.total_tco_per_unit) * 100

    def to_dict(self) -> dict:
        return {
            "supplier_id": self.supplier_id,
            "sku": self.sku,
            "annual_volume": self.annual_volume,
            "cost_per_unit": {
                "purchase_price": round(self.purchase_price_per_unit, 4),
                "inbound_freight": round(self.inbound_freight_per_unit, 4),
                "customs_duty": round(self.customs_duty_per_unit, 4),
                "brokerage": round(self.brokerage_per_unit, 4),
                "warehousing": round(self.warehousing_per_unit, 4),
                "quality_failure": round(self.quality_failure_per_unit, 4),
                "carbon_cost": round(self.carbon_cost_per_unit, 4),
                "working_capital": round(self.working_capital_cost_per_unit, 4),
                "volume_rebate": round(-self.volume_rebate_per_unit, 4),
                "total": round(self.total_tco_per_unit, 4),
            },
            "total_annual_tco": round(self.total_tco_annual, 2),
            "non_purchase_cost_pct": round(self.non_purchase_cost_pct, 1),
        }


@dataclass
class SupplierTCOComparison:
    """Side-by-side TCO comparison of alternative suppliers for a SKU."""
    sku: str
    suppliers: list[TCOBreakdown]
    recommended_supplier_id: str
    savings_vs_current: float           # annual USD savings vs current supplier
    recommendation_rationale: str

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "recommended_supplier_id": self.recommended_supplier_id,
            "annual_savings_vs_current": round(self.savings_vs_current, 2),
            "rationale": self.recommendation_rationale,
            "supplier_comparison": [s.to_dict() for s in sorted(
                self.suppliers, key=lambda x: x.total_tco_per_unit
            )],
        }


# ─── ESG + TCO Engine ─────────────────────────────────────────────────────────


class ESGTCOEngine:
    """
    Computes ESG scores and TCO for wholesale distribution supplier/product decisions.
    """

    # Industry benchmark values (wholesale distribution, 2024)
    CARBON_BENCHMARK_KG_PER_CASE = 0.8      # kg CO₂e per case — industry median
    INTERNAL_CARBON_PRICE_USD_PER_TON = 25  # internal shadow price

    def __init__(self, company_id: str, wacc_pct: float = 10.0):
        self.company_id = company_id
        self.wacc = wacc_pct / 100

    # ── ESG Scoring ──────────────────────────────────────────────────────────

    def score_esg(
        self,
        entity_id: str,
        entity_type: str,
        carbon_per_case: float,
        packaging_waste_kg_per_unit: float,
        recyclable_packaging_pct: float,
        water_intensity: float,
        safety_incidents: float,
        supplier_audit_pass_rate: float,
        local_sourcing_pct: float,
        on_time_payment_rate: float,
        import_compliance_rate: float,
    ) -> ESGSnapshot:
        snap = ESGSnapshot(
            entity_id=entity_id,
            entity_type=entity_type,
            carbon_per_case_kg_co2e=carbon_per_case,
            packaging_waste_kg_per_unit=packaging_waste_kg_per_unit,
            recyclable_packaging_pct=recyclable_packaging_pct,
            water_intensity_gal_per_k_revenue=water_intensity,
            safety_incidents_per_100_employees=safety_incidents,
            supplier_audit_pass_rate_pct=supplier_audit_pass_rate,
            local_sourcing_pct=local_sourcing_pct,
            on_time_payment_rate_pct=on_time_payment_rate,
            import_compliance_rate_pct=import_compliance_rate,
        )

        # ── Environmental score (0–100) ───────────────────────────────────────
        # Carbon (weight: 40%)
        carbon_score = max(0, 100 - (carbon_per_case / self.CARBON_BENCHMARK_KG_PER_CASE) * 50)
        # Packaging (weight: 35%)
        packaging_score = (recyclable_packaging_pct / 100) * 80 + max(0, 20 - packaging_waste_kg_per_unit * 10)
        # Water (weight: 25%)
        water_score = max(0, 100 - water_intensity * 2)

        snap.environmental_score = (
            carbon_score * 0.40 + packaging_score * 0.35 + water_score * 0.25
        )

        # ── Social score (0–100) ─────────────────────────────────────────────
        safety_score = max(0, 100 - safety_incidents * 20)
        audit_score = supplier_audit_pass_rate
        local_score = local_sourcing_pct

        snap.social_score = (
            safety_score * 0.50 + audit_score * 0.35 + local_score * 0.15
        )

        # ── Governance score (0–100) ──────────────────────────────────────────
        snap.governance_score = (
            on_time_payment_rate * 0.50 + import_compliance_rate * 0.50
        )

        snap.overall_esg_score = (
            snap.environmental_score * 0.40
            + snap.social_score * 0.35
            + snap.governance_score * 0.25
        )

        # ── Improvement opportunities ──────────────────────────────────────────
        opps: list[str] = []
        if carbon_per_case > self.CARBON_BENCHMARK_KG_PER_CASE * 1.5:
            opps.append(
                f"Carbon footprint {carbon_per_case:.2f} kg CO₂e/case is "
                f"{((carbon_per_case / self.CARBON_BENCHMARK_KG_PER_CASE - 1) * 100):.0f}% "
                "above industry median — investigate consolidation and modal shift"
            )
        if recyclable_packaging_pct < 70:
            opps.append(
                f"Only {recyclable_packaging_pct:.0f}% recyclable packaging — "
                "target 80%+ for major retailer compliance"
            )
        if safety_incidents > 2.0:
            opps.append(
                f"Safety incident rate {safety_incidents:.1f}/100 employees exceeds BLS "
                "wholesale distribution average (1.8) — review safety program"
            )
        if supplier_audit_pass_rate < 80:
            opps.append(
                f"Only {supplier_audit_pass_rate:.0f}% of suppliers pass ethical sourcing audit — "
                "high reputational and regulatory risk"
            )
        if import_compliance_rate < 95:
            opps.append(
                f"Import compliance rate {import_compliance_rate:.0f}% — "
                "documentation errors increase customs delays and exam risk"
            )

        snap.improvement_opportunities = opps
        return snap

    def carbon_cost_per_unit(self, kg_co2e_per_unit: float) -> float:
        """USD cost at internal carbon shadow price."""
        return kg_co2e_per_unit * self.INTERNAL_CARBON_PRICE_USD_PER_TON / 1000

    # ── TCO Calculation ──────────────────────────────────────────────────────

    def calculate_tco(
        self,
        supplier_id: str,
        sku: str,
        annual_volume: float,
        purchase_price: float,
        inbound_freight_per_unit: float,
        customs_duty_rate_pct: float,       # % of purchase price
        brokerage_per_unit: float,
        days_inventory_outstanding: float,
        holding_cost_rate_pct: float,       # annual % of unit cost
        return_rate_pct: float,
        return_handling_cost: float,
        replacement_cost_per_return: float,
        kg_co2e_per_unit: float,
        volume_rebate_pct: float = 0.0,
    ) -> TCOBreakdown:
        """Compute all-in TCO for one supplier × SKU combination."""
        breakdown = TCOBreakdown(
            supplier_id=supplier_id,
            sku=sku,
            annual_volume=annual_volume,
            purchase_price_per_unit=purchase_price,
        )

        breakdown.inbound_freight_per_unit = inbound_freight_per_unit
        breakdown.customs_duty_per_unit = purchase_price * (customs_duty_rate_pct / 100)
        breakdown.brokerage_per_unit = brokerage_per_unit

        # Warehousing: (DIO / 365) × unit_cost × holding_rate
        effective_cost = purchase_price + breakdown.customs_duty_per_unit + inbound_freight_per_unit
        breakdown.warehousing_per_unit = (
            days_inventory_outstanding / 365 * effective_cost * holding_cost_rate_pct / 100
        )

        # Quality failure: return_rate × (handling + replacement)
        breakdown.quality_failure_per_unit = (return_rate_pct / 100) * (
            return_handling_cost + replacement_cost_per_return
        )

        # Carbon cost
        breakdown.carbon_cost_per_unit = self.carbon_cost_per_unit(kg_co2e_per_unit)

        # Working capital: DIO × unit_cost × WACC / 365
        breakdown.working_capital_cost_per_unit = (
            days_inventory_outstanding / 365 * effective_cost * self.wacc
        )

        # Volume rebate (positive rebate reduces TCO)
        breakdown.volume_rebate_per_unit = purchase_price * (volume_rebate_pct / 100)

        return breakdown

    def compare_suppliers(
        self,
        sku: str,
        suppliers: list[dict],
        current_supplier_id: str,
    ) -> SupplierTCOComparison:
        """
        Compare multiple supplier TCOs and recommend the lowest-cost option.
        Each supplier dict must include all fields required by calculate_tco().
        """
        tco_breakdowns: list[TCOBreakdown] = []
        for s in suppliers:
            tco = self.calculate_tco(
                supplier_id=s["supplier_id"],
                sku=sku,
                annual_volume=s.get("annual_volume", 0),
                purchase_price=s.get("purchase_price", 0),
                inbound_freight_per_unit=s.get("inbound_freight_per_unit", 0),
                customs_duty_rate_pct=s.get("customs_duty_rate_pct", 0),
                brokerage_per_unit=s.get("brokerage_per_unit", 0),
                days_inventory_outstanding=s.get("days_inventory_outstanding", 30),
                holding_cost_rate_pct=s.get("holding_cost_rate_pct", 25),
                return_rate_pct=s.get("return_rate_pct", 1),
                return_handling_cost=s.get("return_handling_cost", 15),
                replacement_cost_per_return=s.get("replacement_cost_per_return", 0),
                kg_co2e_per_unit=s.get("kg_co2e_per_unit", 0),
                volume_rebate_pct=s.get("volume_rebate_pct", 0),
            )
            tco_breakdowns.append(tco)

        best = min(tco_breakdowns, key=lambda t: t.total_tco_per_unit)
        current = next(
            (t for t in tco_breakdowns if t.supplier_id == current_supplier_id),
            tco_breakdowns[0] if tco_breakdowns else None,
        )

        savings = (
            (current.total_tco_per_unit - best.total_tco_per_unit) * best.annual_volume
            if current and current.supplier_id != best.supplier_id
            else 0.0
        )

        if current and current.supplier_id == best.supplier_id:
            rationale = (
                f"Current supplier {current_supplier_id} has the lowest TCO at "
                f"${best.total_tco_per_unit:.4f}/unit — no change recommended."
            )
        else:
            rationale = (
                f"Switching to {best.supplier_id} saves ${savings:,.0f}/year on {sku}. "
                f"TCO ${best.total_tco_per_unit:.4f}/unit vs "
                f"${current.total_tco_per_unit:.4f}/unit (current). "
                f"Non-purchase costs are "
                f"{best.non_purchase_cost_pct:.0f}% of total TCO — "
                "factor these into any price negotiation."
            )

        return SupplierTCOComparison(
            sku=sku,
            suppliers=tco_breakdowns,
            recommended_supplier_id=best.supplier_id,
            savings_vs_current=savings,
            recommendation_rationale=rationale,
        )
