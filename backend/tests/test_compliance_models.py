"""
Tests for compliance engine data models (dataclasses, enums, computed properties).
"""
import pytest
from datetime import date, timedelta

from app.intelligence.compliance.models import (
    AlcoholProduct,
    BrandRegistration,
    BrandRegStatus,
    COLARecord,
    ComplianceAlert,
    ComplianceCheckResult,
    ComplianceCostEstimate,
    ComplianceDeadline,
    ComplianceIssue,
    DailyDigest,
    DistributorRelationship,
    FederalPermit,
    FederalPermitType,
    IssueSeverity,
    AlertPriority,
    DeadlineType,
    LicenseStatus,
    ProductType,
    StateLicense,
    TerminationRestriction,
)


# ─── Enums ─────────────────────────────────────────────────────────────────────

class TestEnums:
    def test_product_type_values(self):
        assert ProductType.WINE.value == "wine"
        assert ProductType.SPIRITS.value == "spirits"
        assert ProductType.BEER.value == "beer"
        assert ProductType.MALT.value == "malt"

    def test_license_status_values(self):
        assert LicenseStatus.ACTIVE.value == "active"
        assert LicenseStatus.EXPIRED.value == "expired"
        assert LicenseStatus.PENDING.value == "pending"
        assert LicenseStatus.REVOKED.value == "revoked"
        assert LicenseStatus.SUSPENDED.value == "suspended"

    def test_brand_reg_status_values(self):
        assert BrandRegStatus.ACTIVE.value == "active"
        assert BrandRegStatus.PENDING.value == "pending"
        assert BrandRegStatus.EXPIRED.value == "expired"
        assert BrandRegStatus.NOT_REQUIRED.value == "not_required"

    def test_issue_severity_values(self):
        assert IssueSeverity.BLOCKER.value == "blocker"
        assert IssueSeverity.WARNING.value == "warning"
        assert IssueSeverity.INFO.value == "info"

    def test_alert_priority_values(self):
        assert AlertPriority.CRITICAL.value == "critical"
        assert AlertPriority.WARNING.value == "warning"
        assert AlertPriority.INFO.value == "info"

    def test_federal_permit_type_values(self):
        assert FederalPermitType.IMPORTER.value == "importer"
        assert FederalPermitType.WHOLESALER.value == "wholesaler"
        assert FederalPermitType.IMPORTER_WHOLESALER.value == "importer_wholesaler"
        assert FederalPermitType.PRODUCER.value == "producer"

    def test_termination_restriction_values(self):
        assert TerminationRestriction.NONE.value == "none"
        assert TerminationRestriction.NOTICE_ONLY.value == "notice_only"
        assert TerminationRestriction.GOOD_CAUSE.value == "good_cause"
        assert TerminationRestriction.PROHIBITED.value == "prohibited"

    def test_deadline_type_values(self):
        assert DeadlineType.TAX_REPORT.value == "tax_report"
        assert DeadlineType.PRICE_POSTING.value == "price_posting"
        assert DeadlineType.LICENSE_RENEWAL.value == "license_renewal"


# ─── AlcoholProduct ────────────────────────────────────────────────────────────

class TestAlcoholProduct:
    def test_default_instantiation(self):
        p = AlcoholProduct()
        assert p.product_type == ProductType.WINE
        assert p.abv_pct == 0.0
        assert p.container_size_ml == 750.0
        assert p.cases_per_container == 56

    def test_auto_id_generated(self):
        p1 = AlcoholProduct()
        p2 = AlcoholProduct()
        assert p1.id != p2.id
        assert len(p1.id) == 36  # UUID format

    def test_gallons_per_case_750ml(self):
        p = AlcoholProduct(container_size_ml=750.0)
        # 12 bottles * 750 ml / 3785.41 ml per gallon
        expected = (12 * 750.0) / 3785.41
        assert abs(p.gallons_per_case - expected) < 0.001

    def test_gallons_per_case_1500ml(self):
        p = AlcoholProduct(container_size_ml=1500.0)
        expected = (12 * 1500.0) / 3785.41
        assert abs(p.gallons_per_case - expected) < 0.001

    def test_custom_fields(self):
        p = AlcoholProduct(
            sku="TEST-001",
            name="Test Wine",
            product_type=ProductType.SPIRITS,
            abv_pct=40.0,
            country_of_origin="France",
        )
        assert p.sku == "TEST-001"
        assert p.name == "Test Wine"
        assert p.product_type == ProductType.SPIRITS
        assert p.abv_pct == 40.0
        assert p.country_of_origin == "France"


# ─── FederalPermit ─────────────────────────────────────────────────────────────

class TestFederalPermit:
    def test_default_instantiation(self):
        permit = FederalPermit()
        assert permit.permit_type == FederalPermitType.IMPORTER
        assert permit.status == LicenseStatus.ACTIVE
        assert permit.expiration_date is None

    def test_no_expiry_date_not_expired(self):
        permit = FederalPermit(expiration_date=None)
        assert permit.is_expired is False
        assert permit.days_until_expiry is None

    def test_expired_permit(self):
        past_date = date.today() - timedelta(days=1)
        permit = FederalPermit(expiration_date=past_date)
        assert permit.is_expired is True
        assert permit.days_until_expiry == -1

    def test_active_permit_future_expiry(self):
        future_date = date.today() + timedelta(days=30)
        permit = FederalPermit(expiration_date=future_date)
        assert permit.is_expired is False
        assert permit.days_until_expiry == 30

    def test_days_until_expiry_calculation(self):
        future_date = date.today() + timedelta(days=90)
        permit = FederalPermit(expiration_date=future_date)
        assert permit.days_until_expiry == 90


# ─── COLARecord ────────────────────────────────────────────────────────────────

class TestCOLARecord:
    def test_default_instantiation(self):
        cola = COLARecord()
        assert cola.product_type == ProductType.WINE
        assert cola.status == LicenseStatus.ACTIVE
        assert cola.formula_approved is False

    def test_no_expiry_not_expired(self):
        cola = COLARecord(expiration_date=None)
        assert cola.is_expired is False
        assert cola.days_until_expiry is None

    def test_expired_cola(self):
        past_date = date.today() - timedelta(days=1)
        cola = COLARecord(expiration_date=past_date)
        assert cola.is_expired is True

    def test_upcoming_expiry(self):
        future_date = date.today() + timedelta(days=30)
        cola = COLARecord(expiration_date=future_date)
        assert cola.is_expired is False
        assert cola.days_until_expiry == 30


# ─── StateLicense ──────────────────────────────────────────────────────────────

class TestStateLicense:
    def test_default_instantiation(self):
        lic = StateLicense()
        assert lic.status == LicenseStatus.ACTIVE
        assert lic.renewal_window_days == 90
        assert lic.annual_fee == 0.0

    def test_expired_license(self):
        past_date = date.today() - timedelta(days=1)
        lic = StateLicense(expiration_date=past_date)
        assert lic.is_expired is True

    def test_active_license(self):
        future_date = date.today() + timedelta(days=30)
        lic = StateLicense(expiration_date=future_date)
        assert lic.is_expired is False
        assert lic.days_until_expiry == 30

    def test_product_types_list(self):
        lic = StateLicense(product_types=[ProductType.WINE, ProductType.SPIRITS])
        assert ProductType.WINE in lic.product_types
        assert ProductType.SPIRITS in lic.product_types


# ─── BrandRegistration ─────────────────────────────────────────────────────────

class TestBrandRegistration:
    def test_default_instantiation(self):
        reg = BrandRegistration()
        assert reg.status == BrandRegStatus.ACTIVE
        assert reg.registration_fee == 0.0

    def test_expired_registration(self):
        past_date = date.today() - timedelta(days=1)
        reg = BrandRegistration(expiration_date=past_date)
        assert reg.is_expired is True

    def test_active_registration(self):
        future_date = date.today() + timedelta(days=30)
        reg = BrandRegistration(expiration_date=future_date)
        assert reg.is_expired is False
        assert reg.days_until_expiry == 30


# ─── DistributorRelationship ───────────────────────────────────────────────────

class TestDistributorRelationship:
    def test_default_instantiation(self):
        dist = DistributorRelationship()
        assert dist.termination_restriction == TerminationRestriction.NONE
        assert dist.franchise_law_attached is False
        assert dist.contract_end_date is None

    def test_days_until_contract_end_none(self):
        dist = DistributorRelationship(contract_end_date=None)
        assert dist.days_until_contract_end is None

    def test_days_until_contract_end(self):
        future_date = date.today() + timedelta(days=60)
        dist = DistributorRelationship(contract_end_date=future_date)
        assert dist.days_until_contract_end == 60


# ─── ComplianceCheckResult ─────────────────────────────────────────────────────

class TestComplianceCheckResult:
    def test_default_instantiation(self):
        result = ComplianceCheckResult()
        assert result.approved is False
        assert result.issues == []
        assert result.fees == {}

    def test_blockers_filter(self):
        issue_blocker = ComplianceIssue(severity=IssueSeverity.BLOCKER, issue="Missing license")
        issue_warning = ComplianceIssue(severity=IssueSeverity.WARNING, issue="Expiring soon")
        result = ComplianceCheckResult(issues=[issue_blocker, issue_warning])
        assert len(result.blockers) == 1
        assert result.blockers[0].issue == "Missing license"

    def test_warnings_filter(self):
        issue_blocker = ComplianceIssue(severity=IssueSeverity.BLOCKER, issue="Missing license")
        issue_warning = ComplianceIssue(severity=IssueSeverity.WARNING, issue="Expiring soon")
        result = ComplianceCheckResult(issues=[issue_blocker, issue_warning])
        assert len(result.warnings) == 1
        assert result.warnings[0].issue == "Expiring soon"


# ─── ComplianceCostEstimate ────────────────────────────────────────────────────

class TestComplianceCostEstimate:
    def test_default_instantiation(self):
        est = ComplianceCostEstimate()
        assert est.grand_total == 0.0
        assert est.licenses == 0.0
        assert est.brand_registrations == 0.0
        assert est.by_state == {}

    def test_custom_values(self):
        est = ComplianceCostEstimate(
            licenses=1000.0,
            brand_registrations=500.0,
            grand_total=1500.0,
            by_state={"NY": {"license_renewals": 1000.0}},
        )
        assert est.grand_total == 1500.0
        assert "NY" in est.by_state


# ─── DailyDigest ──────────────────────────────────────────────────────────────

class TestDailyDigest:
    def test_default_instantiation(self):
        digest = DailyDigest()
        assert digest.date == date.today()
        assert digest.critical_alerts == []
        assert digest.warning_alerts == []
        assert digest.upcoming_deadlines == []
        assert digest.state_status == {}

    def test_with_alerts(self):
        alert = ComplianceAlert(
            alert_type="state_license_expiry",
            priority=AlertPriority.CRITICAL,
            state_code="NY",
            item_name="NY License",
            days_until=7,
        )
        digest = DailyDigest(critical_alerts=[alert])
        assert len(digest.critical_alerts) == 1
        assert digest.critical_alerts[0].state_code == "NY"
