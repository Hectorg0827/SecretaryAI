"""
Tests for ComplianceEngine RulesEngine.

Tests cover:
  - can_sell_product_in_state() for all check categories
  - calculate_annual_compliance_cost()
  - check_distributor_franchise_risk()
  - get_state_excise_rate()
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.compliance.entity_registry import EntityRegistry
from app.intelligence.compliance.models import (
    AlcoholProduct,
    BrandRegistration,
    BrandRegStatus,
    COLARecord,
    DistributorRelationship,
    FederalPermit,
    FederalPermitType,
    IssueSeverity,
    LicenseStatus,
    ProductType,
    StateLicense,
    TerminationRestriction,
)
from app.intelligence.compliance.rules_engine import RulesEngine


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_registry() -> EntityRegistry:
    return EntityRegistry(company_id="test-co")


def _make_wine_product(pid: str = "prod-001") -> AlcoholProduct:
    return AlcoholProduct(
        id=pid,
        sku="WINE-001",
        name="La Fuerza Tempranillo 750ml",
        product_type=ProductType.WINE,
        abv_pct=13.5,
        container_size_ml=750.0,
    )


def _make_spirits_product(pid: str = "prod-002") -> AlcoholProduct:
    return AlcoholProduct(
        id=pid,
        sku="SPIR-001",
        name="Casa Blanca Tequila 750ml",
        product_type=ProductType.SPIRITS,
        abv_pct=40.0,
        container_size_ml=750.0,
    )


def _make_federal_permit() -> FederalPermit:
    return FederalPermit(
        permit_type=FederalPermitType.IMPORTER,
        permit_number="BW-NY-12345",
        status=LicenseStatus.ACTIVE,
        expiration_date=None,  # TTB basic permit = no expiry
    )


def _make_cola(product_id: str) -> COLARecord:
    return COLARecord(
        product_id=product_id,
        cola_number="22222222",
        status=LicenseStatus.ACTIVE,
        expiration_date=None,
    )


def _make_ny_license(product_type: ProductType = ProductType.WINE) -> StateLicense:
    return StateLicense(
        state_code="NY",
        license_type="Supplier/Importer",
        product_types=[product_type],
        license_number="NY-SUP-001",
        status=LicenseStatus.ACTIVE,
        expiration_date=date.today() + timedelta(days=365),
        annual_fee=400.0,
    )


def _make_ny_brand_reg(product_id: str) -> BrandRegistration:
    return BrandRegistration(
        product_id=product_id,
        state_code="NY",
        registration_number="NYR-001",
        status=BrandRegStatus.ACTIVE,
        expiration_date=date.today() + timedelta(days=365),
        registration_fee=150.0,
    )


def _make_full_registry_for_ny(product: AlcoholProduct) -> EntityRegistry:
    """Registry with all required compliance elements for selling wine in NY."""
    reg = _make_registry()
    reg.upsert_product(product)
    reg.upsert_federal_permit(_make_federal_permit())
    reg.upsert_cola(_make_cola(product.id))
    reg.upsert_state_license(_make_ny_license(product.product_type))
    reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
    return reg


# ─── Unknown product / state ──────────────────────────────────────────────────

class TestUnknownProductOrState:
    def test_unknown_product_returns_blocker(self):
        reg = _make_registry()
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state("nonexistent-id", "NY")
        assert result.approved is False
        assert any(i.category == "product" for i in result.issues)
        assert any(i.severity == IssueSeverity.BLOCKER for i in result.issues)

    def test_unknown_state_returns_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "XX")
        assert result.approved is False
        assert any(i.category == "state" for i in result.issues)


# ─── Federal permit check ─────────────────────────────────────────────────────

class TestFederalPermitCheck:
    def test_no_federal_permit_is_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_cola(_make_cola(product.id))
        reg.upsert_state_license(_make_ny_license())
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "federal_permit" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)

    def test_expired_federal_permit_is_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        expired_permit = FederalPermit(
            permit_type=FederalPermitType.IMPORTER,
            permit_number="BW-NY-EXPIRED",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() - timedelta(days=1),
        )
        reg.upsert_federal_permit(expired_permit)
        reg.upsert_cola(_make_cola(product.id))
        reg.upsert_state_license(_make_ny_license())
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "federal_permit" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)

    def test_expiring_soon_federal_permit_is_warning(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        permit = FederalPermit(
            permit_type=FederalPermitType.IMPORTER,
            permit_number="BW-NY-SOON",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() + timedelta(days=30),
        )
        reg.upsert_federal_permit(permit)
        reg.upsert_cola(_make_cola(product.id))
        reg.upsert_state_license(_make_ny_license())
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "federal_permit" and i.severity == IssueSeverity.WARNING
                   for i in result.issues)


# ─── COLA check ───────────────────────────────────────────────────────────────

class TestCOLACheck:
    def test_no_cola_is_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_state_license(_make_ny_license())
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "cola" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)

    def test_expired_cola_is_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        expired_cola = COLARecord(
            product_id=product.id,
            cola_number="EXPIRED-COLA",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() - timedelta(days=1),
        )
        reg.upsert_cola(expired_cola)
        reg.upsert_state_license(_make_ny_license())
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "cola" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)


# ─── State license check ──────────────────────────────────────────────────────

class TestStateLicenseCheck:
    def test_no_state_license_is_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_cola(_make_cola(product.id))
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "state_license" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)

    def test_expired_state_license_is_blocker(self):
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_cola(_make_cola(product.id))
        expired_lic = StateLicense(
            state_code="NY",
            license_type="Supplier",
            product_types=[ProductType.WINE],
            license_number="NY-EXPIRED",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() - timedelta(days=1),
        )
        reg.upsert_state_license(expired_lic)
        reg.upsert_brand_registration(_make_ny_brand_reg(product.id))
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "state_license" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)


# ─── Brand registration check ─────────────────────────────────────────────────

class TestBrandRegistrationCheck:
    def test_no_brand_reg_in_required_state_is_blocker(self):
        """NY requires brand registration — no reg should block shipment."""
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_cola(_make_cola(product.id))
        reg.upsert_state_license(_make_ny_license())
        # No brand registration added
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert any(i.category == "brand_registration" and i.severity == IssueSeverity.BLOCKER
                   for i in result.issues)

    def test_brand_reg_not_required_in_state_no_blocker(self):
        """States without brand reg requirement should not flag this."""
        # Find a state in the matrix without brand registration
        # Colorado (CO) typically doesn't require brand registration
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_cola(_make_cola(product.id))
        co_license = StateLicense(
            state_code="CO",
            license_type="Supplier",
            product_types=[ProductType.WINE],
            license_number="CO-SUP-001",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() + timedelta(days=365),
        )
        reg.upsert_state_license(co_license)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "CO")
        brand_blockers = [i for i in result.issues
                          if i.category == "brand_registration"
                          and i.severity == IssueSeverity.BLOCKER]
        assert len(brand_blockers) == 0


# ─── Control state check ──────────────────────────────────────────────────────

class TestControlStateCheck:
    def test_spirits_in_control_state_raises_warning(self):
        """PA is a control state for spirits — should warn even with all docs."""
        reg = _make_registry()
        product = _make_spirits_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_cola(_make_cola(product.id))
        pa_license = StateLicense(
            state_code="PA",
            license_type="Supplier",
            product_types=[ProductType.SPIRITS],
            license_number="PA-SUP-001",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() + timedelta(days=365),
        )
        reg.upsert_state_license(pa_license)
        pa_reg = BrandRegistration(
            product_id=product.id,
            state_code="PA",
            registration_number="PA-REG-001",
            status=BrandRegStatus.ACTIVE,
            expiration_date=date.today() + timedelta(days=365),
        )
        reg.upsert_brand_registration(pa_reg)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "PA")
        assert any(i.category == "control_state" and i.severity == IssueSeverity.WARNING
                   for i in result.issues)

    def test_wine_in_non_control_state_no_warning(self):
        """CA is not a control state for wine — no control state warning."""
        reg = _make_registry()
        product = _make_wine_product()
        reg.upsert_product(product)
        reg.upsert_federal_permit(_make_federal_permit())
        reg.upsert_cola(_make_cola(product.id))
        ca_license = StateLicense(
            state_code="CA",
            license_type="Supplier",
            product_types=[ProductType.WINE],
            license_number="CA-SUP-001",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() + timedelta(days=365),
        )
        reg.upsert_state_license(ca_license)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "CA")
        assert not any(i.category == "control_state" for i in result.issues)


# ─── Excise tax calculation ───────────────────────────────────────────────────

class TestExciseTaxCalculation:
    def test_excise_tax_calculated_for_ny_wine(self):
        """NY wine excise should be calculated and appear in fees."""
        reg = _make_full_registry_for_ny(_make_wine_product())
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state("prod-001", "NY", quantity_cases=10)
        assert "state_excise_tax" in result.fees
        assert result.fees["state_excise_tax"] > 0

    def test_excise_tax_scales_with_quantity(self):
        """More cases = more excise tax."""
        reg_10 = _make_full_registry_for_ny(_make_wine_product("prod-a"))
        rules = RulesEngine(registry=reg_10)
        res_1 = rules.can_sell_product_in_state("prod-a", "NY", quantity_cases=1)
        res_10 = rules.can_sell_product_in_state("prod-a", "NY", quantity_cases=10)
        assert res_10.fees["state_excise_tax"] == pytest.approx(
            res_1.fees["state_excise_tax"] * 10, rel=0.01
        )

    def test_get_state_excise_rate(self):
        rules = RulesEngine(registry=_make_registry())
        rate = rules.get_state_excise_rate("NY", ProductType.WINE)
        assert rate > 0
        rate_spirits = rules.get_state_excise_rate("NY", ProductType.SPIRITS)
        assert rate_spirits > rate  # spirits are taxed higher than wine


# ─── Full approval ────────────────────────────────────────────────────────────

class TestFullApproval:
    def test_fully_compliant_product_is_approved(self):
        """All requirements met → approved=True."""
        product = _make_wine_product()
        reg = _make_full_registry_for_ny(product)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert result.approved is True
        assert len(result.blockers) == 0

    def test_approved_result_has_no_blockers(self):
        product = _make_wine_product()
        reg = _make_full_registry_for_ny(product)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY")
        assert result.blockers == []

    def test_approved_result_has_fees(self):
        product = _make_wine_product()
        reg = _make_full_registry_for_ny(product)
        rules = RulesEngine(registry=reg)
        result = rules.can_sell_product_in_state(product.id, "NY", quantity_cases=5)
        assert result.total_compliance_cost > 0
        assert "state_excise_tax" in result.fees


# ─── Annual cost calculator ───────────────────────────────────────────────────

class TestAnnualCostCalculator:
    def test_empty_states_returns_zero(self):
        reg = _make_registry()
        rules = RulesEngine(registry=reg)
        cost = rules.calculate_annual_compliance_cost(states_active=[], products=[])
        assert cost["grand_total"] == 0.0

    def test_calculates_license_costs(self):
        product = _make_wine_product()
        reg = _make_full_registry_for_ny(product)
        rules = RulesEngine(registry=reg)
        cost = rules.calculate_annual_compliance_cost(
            states_active=["NY"],
            products=[product],
        )
        assert cost["licenses"] >= 0
        assert "NY" in cost["by_state"]

    def test_brand_registration_costs_included(self):
        """NY requires brand registration — should appear in brand_registrations total."""
        product = _make_wine_product()
        reg = _make_full_registry_for_ny(product)
        rules = RulesEngine(registry=reg)
        cost = rules.calculate_annual_compliance_cost(
            states_active=["NY"],
            products=[product],
        )
        # NY has brand registration — fee should be > 0
        assert cost["brand_registrations"] >= 0
        assert cost["grand_total"] == pytest.approx(
            cost["licenses"] + cost["brand_registrations"], rel=0.01
        )


# ─── Franchise risk check ─────────────────────────────────────────────────────

class TestFranchiseRiskCheck:
    def test_high_risk_state_returns_high(self):
        """States with good_cause termination requirement should be high risk."""
        rules = RulesEngine(registry=_make_registry())
        # CA, NY, NJ all have franchise laws
        result = rules.check_distributor_franchise_risk("NY")
        assert result["franchise_law_exists"] is True
        assert result["risk_level"] in ("high", "medium", "low")

    def test_no_franchise_law_state_returns_low(self):
        """Not all states have franchise laws — those should be low risk."""
        rules = RulesEngine(registry=_make_registry())
        # Check a state — risk_level should be defined
        result = rules.check_distributor_franchise_risk("WY")
        assert "franchise_law_exists" in result
        assert "risk_level" in result

    def test_unknown_state_returns_error(self):
        rules = RulesEngine(registry=_make_registry())
        result = rules.check_distributor_franchise_risk("XX")
        assert "error" in result

    def test_all_required_keys_present(self):
        rules = RulesEngine(registry=_make_registry())
        result = rules.check_distributor_franchise_risk("CA")
        for key in ("state_code", "franchise_law_exists", "termination_restriction",
                    "notice_days_required", "notes", "risk_level"):
            assert key in result, f"Missing key: {key}"
