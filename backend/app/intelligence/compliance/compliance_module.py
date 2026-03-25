"""
ComplianceModule — Top-level orchestrator for the SecretaryAI Compliance Engine.

Mirrors the WholesaleDistributionModule pattern: one class, one interface.
The API layer calls ComplianceModule methods; it never imports sub-engines directly.

Instantiate once per company session:
    module = ComplianceModule(company_id="acme-123", config=ComplianceConfig())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .alert_system import AlertSystem
from .entity_registry import EntityRegistry
from .models import (
    AlcoholProduct,
    BrandRegistration,
    COLARecord,
    ComplianceAlert,
    ComplianceCheckResult,
    ComplianceCostEstimate,
    ComplianceDeadline,
    DailyDigest,
    DistributorRelationship,
    FederalPermit,
    ProductType,
    StateLicense,
)
from .rules_engine import RulesEngine
from .state_matrix import STATE_MATRIX, get_state_rules, TIER_1_STATES


# ─── Configuration ────────────────────────────────────────────────────────────


@dataclass
class ComplianceConfig:
    """Company-level compliance settings."""
    # Alert lookahead window for expiration checks
    expiration_lookahead_days: int = 180
    # Deadline lookahead for reporting deadlines
    deadline_lookahead_days: int = 30
    # States currently active (subset of 50)
    active_states: list[str] = field(default_factory=list)
    # Notification email map: role → email list
    role_email_map: dict[str, list[str]] = field(default_factory=dict)
    # Company name for report headers
    company_name: str = ""


# ─── ComplianceModule ─────────────────────────────────────────────────────────


class ComplianceModule:
    """
    End-to-end compliance engine for a national alcohol importer/wholesaler.

    Wires together:
      - EntityRegistry  (licenses, products, COLAs, registrations, distributors)
      - RulesEngine     (pre-shipment checks, fee calculations, franchise risk)
      - AlertSystem     (expiration alerts, deadline tracking, daily digest)
    """

    def __init__(self, company_id: str, config: Optional[ComplianceConfig] = None) -> None:
        self.company_id = company_id
        self._config = config or ComplianceConfig()
        self._registry = EntityRegistry(company_id=company_id)
        self._rules = RulesEngine(registry=self._registry)
        self._alerts = AlertSystem(registry=self._registry)

    # ── Entity management ─────────────────────────────────────────────────────

    def add_federal_permit(self, permit: FederalPermit) -> FederalPermit:
        return self._registry.upsert_federal_permit(permit)

    def add_cola(self, cola: COLARecord) -> COLARecord:
        return self._registry.upsert_cola(cola)

    def add_product(self, product: AlcoholProduct) -> AlcoholProduct:
        return self._registry.upsert_product(product)

    def add_state_license(self, license_: StateLicense) -> StateLicense:
        return self._registry.upsert_state_license(license_)

    def add_brand_registration(self, reg: BrandRegistration) -> BrandRegistration:
        return self._registry.upsert_brand_registration(reg)

    def add_distributor(self, distributor: DistributorRelationship) -> DistributorRelationship:
        return self._registry.upsert_distributor(distributor)

    # ── Lookups ───────────────────────────────────────────────────────────────

    def get_product(self, product_id: str) -> Optional[AlcoholProduct]:
        return self._registry.get_product(product_id)

    def get_product_by_sku(self, sku: str) -> Optional[AlcoholProduct]:
        return self._registry.get_product_by_sku(sku)

    def list_products(self) -> list[AlcoholProduct]:
        return self._registry.list_products()

    def list_state_licenses(self) -> list[StateLicense]:
        return self._registry.list_state_licenses()

    def list_brand_registrations(self) -> list[BrandRegistration]:
        return self._registry.list_brand_registrations()

    def list_federal_permits(self) -> list[FederalPermit]:
        return self._registry.list_federal_permits()

    def list_distributors(self) -> list[DistributorRelationship]:
        return self._registry.list_distributors()

    def list_colas(self) -> list[COLARecord]:
        return self._registry.list_colas()

    # ── Compliance checks ─────────────────────────────────────────────────────

    def check_shipment(
        self,
        product_id: str,
        state_code: str,
        quantity_cases: int = 1,
    ) -> ComplianceCheckResult:
        """
        Pre-shipment compliance check.
        Returns ComplianceCheckResult with approved flag, issues, and fees.
        Call this BEFORE confirming any shipment or PO.
        """
        return self._rules.can_sell_product_in_state(
            product_id=product_id,
            state_code=state_code,
            quantity_cases=quantity_cases,
        )

    def check_shipment_batch(
        self,
        shipments: list[dict],
    ) -> list[ComplianceCheckResult]:
        """
        Run pre-shipment checks for multiple shipments at once.
        Each dict: {product_id, state_code, quantity_cases}
        """
        return [
            self._rules.can_sell_product_in_state(
                product_id=s["product_id"],
                state_code=s["state_code"],
                quantity_cases=s.get("quantity_cases", 1),
            )
            for s in shipments
        ]

    def check_distributor_risk(self, state_code: str) -> dict:
        """Return franchise law risk summary before appointing a distributor."""
        return self._rules.check_distributor_franchise_risk(state_code)

    # ── Alerts & deadlines ────────────────────────────────────────────────────

    def get_alerts(self, lookahead_days: Optional[int] = None) -> list[ComplianceAlert]:
        """Return all active compliance alerts."""
        days = lookahead_days or self._config.expiration_lookahead_days
        return self._alerts.check_expirations(lookahead_days=days)

    def get_deadlines(self, days_ahead: Optional[int] = None) -> list[ComplianceDeadline]:
        """Return upcoming filing/reporting deadlines."""
        days = days_ahead or self._config.deadline_lookahead_days
        return self._alerts.get_upcoming_deadlines(days_ahead=days)

    def get_daily_digest(self) -> DailyDigest:
        """Generate the daily compliance digest."""
        digest = self._alerts.generate_daily_digest()
        # Enrich with cost estimate
        active = self._config.active_states or self._registry.states_with_active_license()
        if active:
            cost_raw = self._rules.calculate_annual_compliance_cost(
                states_active=active,
                products=self._registry.list_products(),
            )
            digest.cost_estimate_q = ComplianceCostEstimate(
                licenses=cost_raw["licenses"],
                brand_registrations=cost_raw["brand_registrations"],
                grand_total=cost_raw["grand_total"],
                by_state=cost_raw["by_state"],
            )
        return digest

    # ── Fee & cost analysis ───────────────────────────────────────────────────

    def estimate_annual_costs(
        self,
        states_active: Optional[list[str]] = None,
    ) -> dict:
        """
        Estimate total annual compliance cost for operating in given states.
        """
        active = states_active or self._config.active_states or \
                 self._registry.states_with_active_license()
        return self._rules.calculate_annual_compliance_cost(
            states_active=active,
            products=self._registry.list_products(),
        )

    def get_excise_rate(self, state_code: str, product_type: ProductType) -> float:
        """Return the state excise tax rate per US gallon."""
        return self._rules.get_state_excise_rate(state_code, product_type)

    # ── State rules reference ─────────────────────────────────────────────────

    def get_state_rules(self, state_code: str) -> Optional[dict]:
        """Return the full rules matrix row for a state as a dict."""
        rules = get_state_rules(state_code)
        if rules is None:
            return None
        return {
            "state_code": rules.state_code,
            "state_name": rules.state_name,
            "regulator_name": rules.regulator_name,
            "regulator_url": rules.regulator_url,
            "is_control_spirits": rules.is_control_spirits,
            "is_control_wine": rules.is_control_wine,
            "is_control_beer": rules.is_control_beer,
            "brand_registration_required": rules.brand_registration_required,
            "brand_reg_fees": {
                "wine": rules.brand_reg_fee_wine,
                "spirits": rules.brand_reg_fee_spirits,
                "beer": rules.brand_reg_fee_beer,
            },
            "state_label_approval_required": rules.state_label_approval_required,
            "price_posting_required": {
                "wine": rules.price_posting_required_wine,
                "spirits": rules.price_posting_required_spirits,
                "beer": rules.price_posting_required_beer,
            },
            "post_and_hold_days": rules.post_and_hold_days,
            "franchise_law": {
                "exists": rules.franchise_law.exists,
                "attachment_trigger": rules.franchise_law.attachment_trigger,
                "termination": rules.franchise_law.termination,
                "notice_days": rules.franchise_law.notice_days,
                "notes": rules.franchise_law.notes,
            },
            "self_distribution": {
                "wine": rules.self_distribution_wine,
                "spirits": rules.self_distribution_spirits,
                "beer": rules.self_distribution_beer,
            },
            "excise_tax_per_gallon": {
                "wine": rules.excise_tax.wine_per_gallon,
                "spirits": rules.excise_tax.spirits_per_gallon,
                "beer": rules.excise_tax.beer_per_gallon,
            },
            "reporting_frequency": rules.reporting_frequency,
            "local_restrictions": rules.local_restrictions,
            "local_notes": rules.local_notes,
            "record_retention_years": rules.record_retention_years,
            "priority_tier": rules.priority_tier,
            "permit_types": rules.permit_types,
        }

    def list_all_states(self) -> list[dict]:
        """Return summary of all 50 states + DC from the rules matrix."""
        result = []
        for sc, rules in STATE_MATRIX.items():
            result.append({
                "state_code": sc,
                "state_name": rules.state_name,
                "is_control_spirits": rules.is_control_spirits,
                "is_control_wine": rules.is_control_wine,
                "brand_registration_required": rules.brand_registration_required,
                "priority_tier": rules.priority_tier,
                "regulator_url": rules.regulator_url,
            })
        return sorted(result, key=lambda x: x["state_name"])

    # ── Registry status ───────────────────────────────────────────────────────

    def get_registry_summary(self) -> dict:
        return self._registry.summary()

    def get_overall_status(self) -> dict:
        """
        High-level compliance health for the company.
        Used for the dashboard header and API status endpoint.
        """
        alerts = self.get_alerts(lookahead_days=90)
        critical_count = sum(1 for a in alerts if a.priority.value == "critical")
        warning_count = sum(1 for a in alerts if a.priority.value == "warning")
        deadlines_7 = self.get_deadlines(days_ahead=7)
        active_states = self._registry.states_with_active_license()
        products = self._registry.list_products()

        # Per-state status
        state_status: dict[str, str] = {}
        for sc in active_states:
            sc_alerts = [a for a in alerts if a.state_code == sc]
            if any(a.priority.value == "critical" for a in sc_alerts):
                state_status[sc] = "critical"
            elif any(a.priority.value == "warning" for a in sc_alerts):
                state_status[sc] = "warning"
            else:
                state_status[sc] = "ok"

        overall = (
            "critical" if critical_count > 0
            else "warning" if warning_count > 0
            else "ok"
        )

        return {
            "overall": overall,
            "critical_alerts": critical_count,
            "warning_alerts": warning_count,
            "active_states": len(active_states),
            "total_products": len(products),
            "deadlines_due_7_days": len(deadlines_7),
            "state_status": state_status,
            "registry": self._registry.summary(),
        }

    # ── Module description ────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Return a human-readable description of the module's capabilities."""
        return {
            "module": "ComplianceModule",
            "company_id": self.company_id,
            "layers": [
                "EntityRegistry: Federal permits, COLAs, state licenses, brand registrations, distributors",
                "RulesEngine: 50-state matrix, pre-shipment validation, fee calculation, franchise risk",
                "AlertSystem: Expiration alerts, filing deadlines, daily digest",
            ],
            "states_in_matrix": len(STATE_MATRIX),
            "tier_1_states": sorted(TIER_1_STATES),
            "capabilities": [
                "can_sell_product_in_state",
                "check_shipment_batch",
                "check_distributor_risk",
                "get_alerts",
                "get_deadlines",
                "get_daily_digest",
                "estimate_annual_costs",
                "get_excise_rate",
                "get_state_rules",
                "list_all_states",
                "get_overall_status",
            ],
            "config": {
                "expiration_lookahead_days": self._config.expiration_lookahead_days,
                "deadline_lookahead_days": self._config.deadline_lookahead_days,
                "active_states": self._config.active_states,
            },
        }
