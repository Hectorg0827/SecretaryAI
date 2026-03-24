"""
WholesaleDistributionModule — the "Lego piece" for the importer/distributor vertical.

This is the single entry point for all wholesale distribution intelligence.
It wires together all §18–§25 sub-modules and exposes a unified interface
that the SecretaryAI agent layer calls.

Design: "Lego set" architecture.
    ┌─────────────────────────────────────────┐
    │         IndustryRegistry                │
    │  vertical="wholesale_distribution"  ────┼──► WholesaleDistributionModule
    │  vertical="retail"              ────────┼──► RetailModule (future)
    │  vertical="manufacturing"       ────────┼──► ManufacturingModule (future)
    └─────────────────────────────────────────┘

Each module exposes the same interface (IndustryModuleBase protocol).
The agent layer never calls sub-modules directly — it always goes through
the registered vertical module.

Usage:
    from app.intelligence.industry_registry import get_industry_module
    module = get_industry_module("wholesale_distribution", company_id=..., config={...})

    # Run full intelligence suite
    report = await module.full_analysis(data)

    # Or individual capabilities
    actions = module.reorder_actions(inventory_snapshot)
    pricing = module.pricing_recommendations(sku, segment_data)
    causal  = module.customer_drop_analysis(customer_id, history)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .action_schema import (
    ActionImpact,
    ActionObject,
    ActionType,
    AutonomyEngine,
    Guardrails,
    Tier,
)
from .causal_reasoning import CausalReasoningEngine
from .dynamic_pricing import DynamicPricingEngine, ElasticityEstimate, PricingTier
from .esg_tco import ESGTCOEngine
from .governance import GovernanceEngine, Role, Action
from .returns_mgmt import ReturnsManagementEngine, ReturnRecord
from .scor_toc import SCORToCEngine, ProcessNode, SCORToCReport
from .stochastic_inv import (
    DemandDistribution,
    LeadTimeDistribution,
    StochasticInventoryEngine,
)


@dataclass
class CompanyConfig:
    """Configuration for the wholesale distribution module."""
    company_id: str
    annual_revenue: float = 0.0
    autonomy_tier: Tier = 1
    target_gross_margin_pct: float = 30.0
    service_level_target: float = 0.95
    ordering_cost_per_po: float = 50.0
    holding_cost_rate_pct: float = 25.0
    internal_carbon_price_usd_per_ton: float = 25.0
    wacc_pct: float = 10.0
    guardrails: Optional[dict] = None     # override defaults

    def build_guardrails(self) -> Guardrails:
        if self.guardrails:
            return Guardrails(**{
                k: v for k, v in self.guardrails.items()
                if hasattr(Guardrails, k)
            })
        return Guardrails()


class WholesaleDistributionModule:
    """
    Top-level industry logic module for importers and wholesale distributors.

    Instantiate once per company per request (or cache it).
    All sub-engines are lazy-initialized on first use.
    """

    VERTICAL_ID = "wholesale_distribution"
    DISPLAY_NAME = "Importer / Wholesale Distributor"

    def __init__(self, company_id: str, config: dict | None = None):
        cfg_dict = config or {}
        self.config = CompanyConfig(
            company_id=company_id,
            annual_revenue=cfg_dict.get("annual_revenue", 0.0),
            autonomy_tier=cfg_dict.get("autonomy_tier", 1),
            target_gross_margin_pct=cfg_dict.get("target_gross_margin_pct", 30.0),
            service_level_target=cfg_dict.get("service_level_target", 0.95),
            ordering_cost_per_po=cfg_dict.get("ordering_cost_per_po", 50.0),
            holding_cost_rate_pct=cfg_dict.get("holding_cost_rate_pct", 25.0),
            internal_carbon_price_usd_per_ton=cfg_dict.get(
                "internal_carbon_price_usd_per_ton", 25.0
            ),
            wacc_pct=cfg_dict.get("wacc_pct", 10.0),
            guardrails=cfg_dict.get("guardrails"),
        )

        # Sub-engines (initialized immediately — all are lightweight)
        self.autonomy = AutonomyEngine(
            tier=self.config.autonomy_tier,
            guardrails=self.config.build_guardrails(),
        )
        self.causal = CausalReasoningEngine(company_id=company_id)
        self.inventory = StochasticInventoryEngine(
            annual_revenue=self.config.annual_revenue,
            ordering_cost_per_po=self.config.ordering_cost_per_po,
            holding_cost_rate=self.config.holding_cost_rate_pct / 100,
            service_level=self.config.service_level_target,
        )
        self.scor_toc = SCORToCEngine(company_id=company_id)
        self.pricing = DynamicPricingEngine(
            company_id=company_id,
            target_gross_margin_pct=self.config.target_gross_margin_pct,
        )
        self.esg_tco = ESGTCOEngine(
            company_id=company_id,
            wacc_pct=self.config.wacc_pct,
        )
        self.returns = ReturnsManagementEngine(company_id=company_id)
        self.governance = GovernanceEngine(company_id=company_id)

    # ── Inventory Intelligence ────────────────────────────────────────────────

    def reorder_actions(
        self,
        inventory_snapshot: list[dict],
        # [{sku, current_stock, unit_cost, daily_demand_history, lead_time_history}]
    ) -> list[dict]:
        """
        Evaluate every SKU in the snapshot and return ActionObjects for any
        that need reordering, ranked by urgency.
        """
        actions: list[dict] = []

        for item in inventory_snapshot:
            sku = item.get("sku", "")
            current_stock = item.get("current_stock", 0)
            unit_cost = item.get("unit_cost", 0.0)

            demand = DemandDistribution(
                sku=sku,
                daily_demand_history=item.get("daily_demand_history", [1.0]),
            )
            lt = LeadTimeDistribution(
                supplier_id=item.get("supplier_id", ""),
                sku=sku,
                lead_time_history=item.get("lead_time_history", [7.0]),
            )

            rec = self.inventory.calculate(demand, lt, unit_cost, current_stock)
            needs_reorder, reason = self.inventory.reorder_needed(current_stock, rec)

            if not needs_reorder:
                continue

            from datetime import datetime, timezone, timedelta
            expiry = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

            order_qty = rec.economic_order_qty
            order_cost = order_qty * unit_cost

            action = ActionObject(
                type=ActionType.REORDER,
                title=f"Reorder {sku} — {reason[:60]}",
                description=(
                    f"SKU {sku} has {current_stock} units (reorder point: "
                    f"{rec.reorder_point_units}). {reason} "
                    f"Recommended order quantity: {order_qty} units."
                ),
                confidence=0.85 if rec.data_quality == "good" else 0.60,
                risk="medium" if current_stock <= rec.safety_stock_units else "low",
                impact=ActionImpact(
                    cost_delta=order_cost,
                    units=order_qty,
                    days_of_supply_change=rec.days_of_supply_at_rop,
                ),
                expiration=expiry,
                context={
                    "sku": sku,
                    "current_stock": current_stock,
                    "reorder_point": rec.reorder_point_units,
                    "safety_stock": rec.safety_stock_units,
                    "eoq": rec.economic_order_qty,
                    "lead_time_days": rec.lead_time_mean_days,
                    "stockout_probability": rec.stockout_probability_without_ss,
                    "inventory_recommendation": rec.to_dict(),
                },
            )
            action.record("created", actor="wholesale_distribution_module")

            disposition = self.autonomy.evaluate(action)
            actions.append(disposition)

        # Sort by risk (critical first)
        risk_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        actions.sort(
            key=lambda d: risk_order.get(d["action"]["risk"], 0), reverse=True
        )
        return actions

    # ── Causal Analysis ───────────────────────────────────────────────────────

    def customer_drop_analysis(
        self,
        customer_id: str,
        customer_name: str,
        order_history: list[dict],
        days_since_last_order: int,
        recent_returns: list[dict] | None = None,
        pricing_changes: list[dict] | None = None,
        competitor_signals: list[dict] | None = None,
    ) -> dict:
        """Analyse why a customer has gone quiet and what to do about it."""
        analysis = self.causal.analyse_customer_drop(
            customer_id=customer_id,
            customer_name=customer_name,
            order_history=order_history,
            recent_returns=recent_returns or [],
            pricing_changes=pricing_changes or [],
            competitor_signals=competitor_signals or [],
            days_since_last_order=days_since_last_order,
        )
        return analysis.to_dict()

    # ── Pricing ───────────────────────────────────────────────────────────────

    def pricing_recommendations(
        self,
        sku: str,
        unit_cost: float,
        current_price: float,
        annual_units: float,
        segment_data: list[dict],
        # [{segment, pricing_tier, price_change_events, annual_units, competitive_prices}]
    ) -> list[dict]:
        """Generate pricing recommendations per customer segment for a SKU."""
        recs: list[dict] = []
        for seg in segment_data:
            elasticity = self.pricing.estimate_elasticity(
                segment=seg.get("segment", "default"),
                sku_category=sku,
                price_change_events=seg.get("price_change_events", []),
            )
            tier = PricingTier(seg.get("pricing_tier", "tier_2"))
            rec = self.pricing.recommend_price(
                sku=sku,
                segment=seg.get("segment", "default"),
                pricing_tier=tier,
                current_price=current_price,
                unit_cost=unit_cost,
                annual_units=seg.get("annual_units", annual_units),
                elasticity=elasticity,
                competitive_prices=seg.get("competitive_prices"),
            )
            recs.append(rec.to_dict())
        return recs

    # ── Returns ───────────────────────────────────────────────────────────────

    def returns_dashboard(
        self,
        returns: list[ReturnRecord],
        total_revenue: float,
        supplier_names: dict[str, str] | None = None,
    ) -> dict:
        """Compute full returns dashboard metrics."""
        # Classify any unclassified returns
        for r in returns:
            if r.category is None:
                r.category = self.returns.classify_return(r)
            if r.disposition is None:
                r.disposition = self.returns.recommend_disposition(r)

        metrics = self.returns.compute_metrics(returns, total_revenue, supplier_names=supplier_names)
        return metrics.to_dict()

    def generate_supplier_claim(
        self,
        supplier_id: str,
        supplier_name: str,
        returns: list[ReturnRecord],
        total_units_received: int,
        period_start: str,
        period_end: str,
        defect_threshold_pct: float = 1.0,
    ) -> dict | None:
        claim = self.returns.generate_supplier_claim(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            returns=returns,
            total_units_received=total_units_received,
            period_start=period_start,
            period_end=period_end,
            agreed_defect_threshold_pct=defect_threshold_pct,
        )
        return claim.to_dict() if claim else None

    # ── SCOR + ToC ────────────────────────────────────────────────────────────

    def supply_chain_analysis(
        self,
        orders: list[dict],
        inventory_value: float,
        cogs: float,
        revenue: float,
        ar_balance: float,
        ap_balance: float,
        supply_chain_costs: float,
        supplier_lead_times: list[float],
        process_nodes: list[ProcessNode] | None = None,
    ) -> dict:
        """Full SCOR metrics + Theory of Constraints analysis."""
        report: SCORToCReport = self.scor_toc.full_analysis(
            orders=orders,
            inventory_value=inventory_value,
            cogs=cogs,
            revenue=revenue,
            ar_balance=ar_balance,
            ap_balance=ap_balance,
            supply_chain_costs=supply_chain_costs,
            supplier_lead_times=supplier_lead_times,
            process_nodes=process_nodes or [],
        )
        return report.to_dict()

    # ── ESG / TCO ─────────────────────────────────────────────────────────────

    def esg_snapshot(self, **kwargs: Any) -> dict:
        """Compute ESG score for the company or a supplier."""
        snap = self.esg_tco.score_esg(**kwargs)
        return snap.to_dict()

    def supplier_tco_comparison(
        self, sku: str, suppliers: list[dict], current_supplier_id: str
    ) -> dict:
        """Compare total cost of ownership across alternative suppliers."""
        comparison = self.esg_tco.compare_suppliers(sku, suppliers, current_supplier_id)
        return comparison.to_dict()

    # ── Governance ────────────────────────────────────────────────────────────

    def check_access(
        self, user_id: str, role_str: str, action_str: str, field_name: str
    ) -> tuple[bool, str]:
        """Check whether a user can perform an action on a data field."""
        role = Role(role_str)
        action = Action(action_str)
        return self.governance.check_access(user_id, role, action, field_name)

    def filter_for_role(self, data: dict, role_str: str) -> dict:
        """Strip fields the user's role cannot see."""
        role = Role(role_str)
        return self.governance.filter_response(data, role)

    # ── Metadata ─────────────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Return metadata about this module for the registry."""
        return {
            "vertical_id": self.VERTICAL_ID,
            "display_name": self.DISPLAY_NAME,
            "autonomy_tier": self.config.autonomy_tier,
            "annual_revenue": self.config.annual_revenue,
            "capabilities": [
                "reorder_actions",
                "customer_drop_analysis",
                "pricing_recommendations",
                "returns_dashboard",
                "supplier_quality_claim",
                "supply_chain_analysis",
                "esg_snapshot",
                "supplier_tco_comparison",
                "access_control",
                "data_governance",
            ],
            "sub_modules": [
                "action_schema (§20)",
                "causal_reasoning (§18)",
                "stochastic_inv (§19)",
                "scor_toc (§22)",
                "dynamic_pricing (§23)",
                "esg_tco (§24)",
                "returns_mgmt (§25)",
                "governance (§21)",
            ],
        }
