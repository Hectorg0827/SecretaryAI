"""
RulesEngine — Pre-shipment compliance validation and fee calculation.

Core method: can_sell_product_in_state()
  Called BEFORE any shipment is confirmed.
  Returns ComplianceCheckResult with approved flag, issues list, and fees.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .entity_registry import EntityRegistry
from .models import (
    AlcoholProduct,
    BrandRegStatus,
    ComplianceCheckResult,
    ComplianceIssue,
    IssueSeverity,
    LicenseStatus,
    ProductType,
)
from .state_matrix import StateRules, get_state_rules


class RulesEngine:
    """
    Validates compliance for a given company's entity registry against
    the 50-state rules matrix.
    """

    def __init__(self, registry: EntityRegistry) -> None:
        self._registry = registry

    # ── Pre-shipment check ────────────────────────────────────────────────────

    def can_sell_product_in_state(
        self,
        product_id: str,
        state_code: str,
        quantity_cases: int = 1,
    ) -> ComplianceCheckResult:
        """
        Run the full pre-shipment compliance check for a product in a state.

        Returns a ComplianceCheckResult. If any blocker exists, approved=False.
        """
        result = ComplianceCheckResult(
            product_id=product_id,
            state_code=state_code,
            quantity_cases=quantity_cases,
        )

        product = self._registry.get_product(product_id)
        if product is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"Product ID '{product_id}' not found in registry",
                action="Add product to compliance registry",
                category="product",
            ))
            result.approved = False
            return result

        state = get_state_rules(state_code)
        if state is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"Unknown state code '{state_code}'",
                action="Use a valid 2-letter US state code",
                category="state",
            ))
            result.approved = False
            return result

        # Run all checks
        self._check_federal_permit(result, product)
        self._check_cola(result, product)
        self._check_state_license(result, product, state)
        self._check_control_state(result, product, state)
        self._check_brand_registration(result, product, state)
        self._check_state_label(result, product, state)
        self._check_distributor(result, product, state)
        self._check_franchise_law(result, product, state)
        self._calculate_excise_tax(result, product, state, quantity_cases)

        result.total_compliance_cost = sum(result.fees.values())
        result.approved = len(result.blockers) == 0
        return result

    # ── Individual check methods ──────────────────────────────────────────────

    def _check_federal_permit(
        self, result: ComplianceCheckResult, product: AlcoholProduct
    ) -> None:
        from .models import FederalPermitType
        permit = self._registry.get_federal_permit(FederalPermitType.IMPORTER)
        if permit is None:
            permit = self._registry.get_federal_permit(FederalPermitType.IMPORTER_WHOLESALER)
        if permit is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue="No valid TTB Importer's Basic Permit on file",
                action="Obtain TTB Basic Permit before any commercial activity",
                category="federal_permit",
            ))
        elif permit.is_expired:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"TTB Basic Permit expired on {permit.expiration_date}",
                action="Renew TTB Basic Permit immediately",
                category="federal_permit",
            ))
        elif permit.days_until_expiry is not None and permit.days_until_expiry <= 60:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.WARNING,
                issue=f"TTB Basic Permit expires in {permit.days_until_expiry} days",
                action="Initiate TTB permit renewal process",
                category="federal_permit",
            ))

    def _check_cola(
        self, result: ComplianceCheckResult, product: AlcoholProduct
    ) -> None:
        # COLA required for wines, spirits, and malt beverages under FAA Act
        if product.product_type == ProductType.BEER and product.abv_pct < 0.5:
            return  # non-alcohol beer, no COLA needed
        cola = self._registry.get_cola(product.id)
        if cola is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"No TTB Certificate of Label Approval (COLA) on file for {product.name}",
                action="Obtain COLA from TTB before first shipment",
                category="cola",
            ))
        elif cola.is_expired:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"COLA for {product.name} expired on {cola.expiration_date}",
                action="Renew COLA with TTB",
                category="cola",
            ))
        elif product.requires_formula_approval and not cola.formula_approved:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"Formula approval required but not on file for {product.name}",
                action="Submit formula approval through TTB Formulas Online",
                category="cola",
            ))

    def _check_state_license(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
    ) -> None:
        required = getattr(state, f"supplier_permit_required_{product.product_type.value}", True)
        if not required:
            return
        license_ = self._registry.get_state_license(state.state_code, product.product_type)
        if license_ is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"No valid {product.product_type.value} supplier/importer license in {state.state_code}",
                action=f"Apply for {state.permit_types[0] if state.permit_types else 'supplier'} permit in {state.state_code}",
                state_code=state.state_code,
                category="state_license",
            ))
        elif license_.is_expired:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"{state.state_code} {product.product_type.value} license expired on {license_.expiration_date}",
                action=f"Renew {state.state_code} license immediately",
                state_code=state.state_code,
                category="state_license",
            ))

    def _check_control_state(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
    ) -> None:
        is_control = (
            (product.product_type == ProductType.SPIRITS and state.is_control_spirits) or
            (product.product_type == ProductType.WINE and state.is_control_wine) or
            (product.product_type == ProductType.BEER and state.is_control_beer)
        )
        if is_control:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.WARNING,
                issue=(f"{state.state_code} is a control state for {product.product_type.value}. "
                       f"Verify product is listed through state purchasing channels."),
                action=f"Submit product for {state.state_code} state listing/approval through {state.regulator_name}",
                state_code=state.state_code,
                category="control_state",
            ))

    def _check_brand_registration(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
    ) -> None:
        if not state.brand_registration_required:
            return
        reg = self._registry.get_brand_registration(product.id, state.state_code)
        fee = getattr(state, f"brand_reg_fee_{product.product_type.value}", 0.0)
        if reg is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"{product.name} is not registered in {state.state_code}",
                action=f"File brand registration with {state.regulator_name}",
                fee=fee,
                state_code=state.state_code,
                category="brand_registration",
            ))
            if fee:
                result.fees["brand_registration"] = result.fees.get("brand_registration", 0.0) + fee
        elif reg.is_expired:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"{product.name} brand registration in {state.state_code} expired on {reg.expiration_date}",
                action=f"Renew brand registration with {state.regulator_name}",
                fee=fee,
                state_code=state.state_code,
                category="brand_registration",
            ))
            if fee:
                result.fees["brand_registration_renewal"] = (
                    result.fees.get("brand_registration_renewal", 0.0) + fee
                )

    def _check_state_label(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
    ) -> None:
        if not state.state_label_approval_required:
            return
        reg = self._registry.get_brand_registration(product.id, state.state_code)
        if reg is None or not reg.state_label_approval_number:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.BLOCKER,
                issue=f"{state.state_code} requires a state label approval in addition to federal COLA",
                action=f"Submit label for state approval through {state.regulator_name}",
                state_code=state.state_code,
                category="state_label",
            ))

    def _check_distributor(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
    ) -> None:
        self_dist = getattr(state, f"self_distribution_{product.product_type.value}", "no")
        if self_dist == "yes":
            return  # can self-distribute, no need for appointed distributor
        distributor = self._registry.get_distributor(state.state_code, product.product_type)
        if distributor is None:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.WARNING,
                issue=f"No distributor appointed for {product.product_type.value} in {state.state_code}",
                action="Appoint a licensed distributor before shipping",
                state_code=state.state_code,
                category="distributor",
            ))

    def _check_franchise_law(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
    ) -> None:
        if not state.franchise_law.exists:
            return
        distributor = self._registry.get_distributor(state.state_code, product.product_type)
        if distributor and distributor.franchise_law_attached:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.INFO,
                issue=(f"Franchise law is active for your {state.state_code} distributor "
                       f"({distributor.distributor_name})"),
                action=(f"Termination requires: {state.franchise_law.termination} "
                        f"with {state.franchise_law.notice_days} days notice. "
                        f"Do not modify territory without counsel review."),
                state_code=state.state_code,
                category="franchise",
            ))
        elif state.franchise_law.exists:
            result.issues.append(ComplianceIssue(
                severity=IssueSeverity.INFO,
                issue=f"Franchise law exists in {state.state_code}",
                action=(f"Franchise protections may attach upon first sale. "
                        f"Termination: {state.franchise_law.termination}. "
                        f"Review before signing distribution agreement."),
                state_code=state.state_code,
                category="franchise",
            ))

    def _calculate_excise_tax(
        self,
        result: ComplianceCheckResult,
        product: AlcoholProduct,
        state: StateRules,
        quantity_cases: int,
    ) -> None:
        total_gallons = product.gallons_per_case * quantity_cases
        rate_attr = f"{product.product_type.value}_per_gallon"
        rate = getattr(state.excise_tax, rate_attr, 0.0)
        if rate > 0:
            tax = rate * total_gallons
            result.fees["state_excise_tax"] = round(tax, 2)

    # ── Fee calculator ────────────────────────────────────────────────────────

    def calculate_annual_compliance_cost(
        self,
        states_active: list[str],
        products: Optional[list[AlcoholProduct]] = None,
    ) -> dict:
        """
        Estimate total annual compliance cost across given states.
        Uses actual registry data where available, falls back to state matrix fees.
        """
        if products is None:
            products = self._registry.list_products()

        total = {
            "licenses": 0.0,
            "brand_registrations": 0.0,
            "label_approvals": 0.0,
            "grand_total": 0.0,
            "by_state": {},
        }

        for sc in states_active:
            state = get_state_rules(sc)
            if state is None:
                continue
            state_cost = {
                "license_renewals": 0.0,
                "brand_registrations": 0.0,
            }

            # License costs per product type active in state
            types_active = {p.product_type for p in products}
            for ptype in types_active:
                lic = self._registry.get_state_license(sc, ptype)
                if lic:
                    state_cost["license_renewals"] += lic.annual_fee

            # Brand registration costs
            if state.brand_registration_required:
                for p in products:
                    fee = getattr(state, f"brand_reg_fee_{p.product_type.value}", 0.0)
                    state_cost["brand_registrations"] += fee

            total["by_state"][sc] = state_cost
            total["licenses"] += state_cost["license_renewals"]
            total["brand_registrations"] += state_cost["brand_registrations"]

        total["grand_total"] = total["licenses"] + total["brand_registrations"]
        return total

    # ── Franchise risk check ──────────────────────────────────────────────────

    def check_distributor_franchise_risk(self, state_code: str) -> dict:
        """
        Return franchise law risk summary for a state before appointing a distributor.
        """
        state = get_state_rules(state_code)
        if state is None:
            return {"state_code": state_code, "error": "Unknown state"}
        fl = state.franchise_law
        return {
            "state_code": state_code,
            "franchise_law_exists": fl.exists,
            "attachment_trigger": fl.attachment_trigger,
            "termination_restriction": fl.termination,
            "notice_days_required": fl.notice_days,
            "notes": fl.notes,
            "risk_level": (
                "high" if fl.termination in ("good_cause", "prohibited")
                else "medium" if fl.termination == "notice_only"
                else "low"
            ),
        }

    # ── State excise rate lookup ──────────────────────────────────────────────

    def get_state_excise_rate(
        self, state_code: str, product_type: ProductType
    ) -> float:
        state = get_state_rules(state_code)
        if state is None:
            return 0.0
        return getattr(state.excise_tax, f"{product_type.value}_per_gallon", 0.0)
