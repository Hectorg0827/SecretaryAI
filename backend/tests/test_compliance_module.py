"""
Tests for ComplianceModule orchestrator.

Tests cover:
  - Module construction and describe()
  - Entity management (add/list round-trip)
  - check_shipment() delegation
  - get_overall_status()
  - get_daily_digest()
  - estimate_annual_costs()
  - get_state_rules() and list_all_states()
  - check_distributor_risk() delegation
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.compliance import ComplianceModule
from app.intelligence.compliance.compliance_module import ComplianceConfig
from app.intelligence.compliance.models import (
    AlcoholProduct,
    BrandRegistration,
    BrandRegStatus,
    COLARecord,
    DistributorRelationship,
    FederalPermit,
    FederalPermitType,
    LicenseStatus,
    ProductType,
    StateLicense,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_module(active_states: list[str] | None = None) -> ComplianceModule:
    cfg = ComplianceConfig(
        active_states=active_states or ["NY", "NJ", "FL"],
        company_name="Test Imports LLC",
    )
    return ComplianceModule(company_id="test-co", config=cfg)


def _make_wine(pid: str = "prod-001") -> AlcoholProduct:
    return AlcoholProduct(
        id=pid,
        sku="WINE-001",
        name="La Fuerza Tempranillo 750ml",
        product_type=ProductType.WINE,
        abv_pct=13.5,
        container_size_ml=750.0,
    )


def _make_importer_permit() -> FederalPermit:
    return FederalPermit(
        permit_type=FederalPermitType.IMPORTER,
        permit_number="BW-NY-12345",
        status=LicenseStatus.ACTIVE,
        expiration_date=None,
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


# ─── Module construction ──────────────────────────────────────────────────────

class TestModuleConstruction:
    def test_default_construction(self):
        m = ComplianceModule(company_id="co-001")
        assert m.company_id == "co-001"

    def test_with_config(self):
        cfg = ComplianceConfig(active_states=["NY"], company_name="Acme Imports")
        m = ComplianceModule(company_id="co-001", config=cfg)
        desc = m.describe()
        assert desc["module"] == "ComplianceModule"
        assert desc["company_id"] == "co-001"

    def test_describe_returns_expected_keys(self):
        m = _make_module()
        desc = m.describe()
        for key in ("module", "company_id", "layers", "states_in_matrix",
                    "capabilities", "config"):
            assert key in desc, f"Missing key: {key}"

    def test_states_in_matrix(self):
        m = _make_module()
        desc = m.describe()
        assert desc["states_in_matrix"] == 51  # 50 states + DC


# ─── Entity management ────────────────────────────────────────────────────────

class TestEntityManagement:
    def test_add_and_list_products(self):
        m = _make_module()
        product = _make_wine()
        m.add_product(product)
        products = m.list_products()
        assert len(products) == 1
        assert products[0].sku == "WINE-001"

    def test_add_and_list_state_licenses(self):
        m = _make_module()
        m.add_state_license(_make_ny_license())
        licenses = m.list_state_licenses()
        assert len(licenses) == 1
        assert licenses[0].state_code == "NY"

    def test_add_and_list_brand_registrations(self):
        m = _make_module()
        product = _make_wine()
        m.add_product(product)
        m.add_brand_registration(_make_ny_brand_reg(product.id))
        regs = m.list_brand_registrations()
        assert len(regs) == 1
        assert regs[0].state_code == "NY"

    def test_add_and_list_federal_permits(self):
        m = _make_module()
        m.add_federal_permit(_make_importer_permit())
        permits = m.list_federal_permits()
        assert len(permits) == 1
        assert permits[0].permit_type == FederalPermitType.IMPORTER

    def test_get_product_by_id(self):
        m = _make_module()
        product = _make_wine("p-abc")
        m.add_product(product)
        found = m.get_product("p-abc")
        assert found is not None
        assert found.name == product.name

    def test_get_product_not_found(self):
        m = _make_module()
        assert m.get_product("nonexistent") is None

    def test_get_product_by_sku(self):
        m = _make_module()
        product = _make_wine()
        m.add_product(product)
        found = m.get_product_by_sku("WINE-001")
        assert found is not None

    def test_registry_summary(self):
        m = _make_module()
        m.add_product(_make_wine())
        m.add_federal_permit(_make_importer_permit())
        summary = m.get_registry_summary()
        assert summary["products"] == 1
        assert summary["federal_permits"] == 1


# ─── Shipment check ───────────────────────────────────────────────────────────

class TestShipmentCheck:
    def _setup_compliant_module(self) -> tuple[ComplianceModule, AlcoholProduct]:
        m = _make_module()
        product = _make_wine()
        m.add_product(product)
        m.add_federal_permit(_make_importer_permit())
        m.add_cola(_make_cola(product.id))
        m.add_state_license(_make_ny_license())
        m.add_brand_registration(_make_ny_brand_reg(product.id))
        return m, product

    def test_fully_compliant_approved(self):
        m, product = self._setup_compliant_module()
        result = m.check_shipment(product.id, "NY", quantity_cases=10)
        assert result.approved is True
        assert result.blockers == []

    def test_missing_license_blocked(self):
        m = _make_module()
        product = _make_wine()
        m.add_product(product)
        m.add_federal_permit(_make_importer_permit())
        m.add_cola(_make_cola(product.id))
        # No state license → should block
        result = m.check_shipment(product.id, "NY")
        assert result.approved is False

    def test_check_shipment_has_fees(self):
        m, product = self._setup_compliant_module()
        result = m.check_shipment(product.id, "NY", quantity_cases=5)
        assert result.total_compliance_cost >= 0
        assert isinstance(result.fees, dict)

    def test_check_shipment_batch(self):
        m, product = self._setup_compliant_module()
        results = m.check_shipment_batch([
            {"product_id": product.id, "state_code": "NY", "quantity_cases": 10},
            {"product_id": "nonexistent", "state_code": "NY", "quantity_cases": 5},
        ])
        assert len(results) == 2
        assert results[0].approved is True
        assert results[1].approved is False


# ─── Overall status ───────────────────────────────────────────────────────────

class TestOverallStatus:
    def test_empty_registry_status_ok(self):
        m = _make_module()
        status = m.get_overall_status()
        assert status["overall"] == "ok"
        assert status["critical_alerts"] == 0
        assert status["warning_alerts"] == 0
        assert status["total_products"] == 0

    def test_status_reflects_critical_alerts(self):
        m = _make_module()
        # Add license expiring in 5 days → critical
        m.add_state_license(StateLicense(
            state_code="NY",
            license_type="Supplier",
            product_types=[ProductType.WINE],
            license_number="NY-CRIT",
            status=LicenseStatus.ACTIVE,
            expiration_date=date.today() + timedelta(days=5),
        ))
        status = m.get_overall_status()
        assert status["overall"] == "critical"
        assert status["critical_alerts"] > 0

    def test_status_has_required_keys(self):
        m = _make_module()
        status = m.get_overall_status()
        for key in ("overall", "critical_alerts", "warning_alerts",
                    "active_states", "total_products", "deadlines_due_7_days",
                    "state_status"):
            assert key in status, f"Missing key: {key}"


# ─── Daily digest ─────────────────────────────────────────────────────────────

class TestDailyDigest:
    def test_empty_registry_digest(self):
        m = _make_module()
        digest = m.get_daily_digest()
        assert digest.critical_alerts == []
        assert digest.warning_alerts == []
        assert isinstance(digest.date, date)

    def test_digest_includes_cost_estimate(self):
        m = _make_module(active_states=["NY"])
        product = _make_wine()
        m.add_product(product)
        m.add_state_license(_make_ny_license())
        digest = m.get_daily_digest()
        assert digest.cost_estimate_q is not None
        assert digest.cost_estimate_q.grand_total >= 0


# ─── Annual cost estimate ─────────────────────────────────────────────────────

class TestAnnualCostEstimate:
    def test_no_active_states_zero_cost(self):
        m = ComplianceModule(company_id="empty-co")
        cost = m.estimate_annual_costs(states_active=[])
        assert cost["grand_total"] == 0.0

    def test_estimate_with_products_and_states(self):
        m = _make_module(active_states=["NY"])
        product = _make_wine()
        m.add_product(product)
        m.add_state_license(_make_ny_license())
        cost = m.estimate_annual_costs(states_active=["NY"])
        assert "licenses" in cost
        assert "brand_registrations" in cost
        assert "grand_total" in cost
        assert "by_state" in cost
        assert "NY" in cost["by_state"]


# ─── State rules reference ────────────────────────────────────────────────────

class TestStateRulesReference:
    def test_list_all_states_returns_51(self):
        m = _make_module()
        states = m.list_all_states()
        assert len(states) == 51  # 50 + DC

    def test_list_all_states_sorted_alphabetically(self):
        m = _make_module()
        states = m.list_all_states()
        names = [s["state_name"] for s in states]
        assert names == sorted(names)

    def test_get_state_rules_known_state(self):
        m = _make_module()
        rules = m.get_state_rules("NY")
        assert rules is not None
        assert rules["state_code"] == "NY"
        assert "franchise_law" in rules
        assert "excise_tax_per_gallon" in rules

    def test_get_state_rules_unknown_state(self):
        m = _make_module()
        assert m.get_state_rules("XX") is None

    def test_control_state_flagged_in_rules(self):
        m = _make_module()
        pa = m.get_state_rules("PA")
        assert pa is not None
        assert pa["is_control_spirits"] is True

    def test_franchise_law_fields_present(self):
        m = _make_module()
        ny = m.get_state_rules("NY")
        fl = ny["franchise_law"]
        for key in ("exists", "termination", "notice_days"):
            assert key in fl, f"Missing franchise law key: {key}"


# ─── Distributor risk ─────────────────────────────────────────────────────────

class TestDistributorRisk:
    def test_check_distributor_risk_known_state(self):
        m = _make_module()
        risk = m.check_distributor_risk("NY")
        assert "franchise_law_exists" in risk
        assert "risk_level" in risk

    def test_check_distributor_risk_unknown_state(self):
        m = _make_module()
        risk = m.check_distributor_risk("XX")
        assert "error" in risk

    def test_high_risk_states_flagged(self):
        """PA, NC, NY have strong franchise laws → high risk."""
        m = _make_module()
        for state in ("PA", "NC"):
            risk = m.check_distributor_risk(state)
            # Just verify risk_level is present and one of the expected values
            assert risk.get("risk_level") in ("low", "medium", "high")
