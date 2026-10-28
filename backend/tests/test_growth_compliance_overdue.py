"""Synthetic overdue-renewal alerts: fail closed on recorded past dates."""

from datetime import date, timedelta

import pytest

from app.intelligence.compliance.alert_system import AlertSystem
from app.intelligence.compliance.entity_registry import EntityRegistry
from app.intelligence.compliance.models import (
    AlcoholProduct,
    AlertPriority,
    BrandRegistration,
    COLARecord,
    DistributorRelationship,
    FederalPermit,
    LicenseStatus,
    ProductType,
    StateLicense,
)


def _registry_with_overdue_records() -> EntityRegistry:
    registry = EntityRegistry(company_id="synthetic-tenant")
    registry.upsert_product(AlcoholProduct(
        id="synthetic-product", sku="synthetic-sku", name="Synthetic product",
        product_type=ProductType.WINE,
    ))
    registry.upsert_federal_permit(FederalPermit(
        id="synthetic-federal", permit_number="synthetic-permit",
        status=LicenseStatus.ACTIVE,
        expiration_date=date.today() - timedelta(days=365),
    ))
    registry.upsert_cola(COLARecord(
        id="synthetic-cola", product_id="synthetic-product",
        cola_number="synthetic-cola-number",
        expiration_date=date.today() - timedelta(days=3),
    ))
    registry.upsert_state_license(StateLicense(
        id="synthetic-license", state_code="NY", license_type="Synthetic",
        license_number="synthetic-license-number",
        expiration_date=date.today() - timedelta(days=21),
    ))
    registry.upsert_brand_registration(BrandRegistration(
        id="synthetic-brand", product_id="synthetic-product", state_code="NY",
        registration_number="synthetic-registration",
        expiration_date=date.today() - timedelta(days=48),
    ))
    registry.upsert_distributor(DistributorRelationship(
        id="synthetic-contract", state_code="NY", distributor_name="Synthetic distributor",
        contract_end_date=date.today() - timedelta(days=90),
    ))
    return registry


def test_all_recorded_overdue_items_remain_critical_after_milestones():
    alerts = AlertSystem(_registry_with_overdue_records()).check_expirations(lookahead_days=0)
    assert len(alerts) == 5
    assert {a.alert_type for a in alerts} == {
        "federal_permit_expiry", "cola_expiry", "state_license_expiry",
        "brand_registration_expiry", "distributor_contract_expiry",
    }
    assert all(a.days_until < 0 and a.priority == AlertPriority.CRITICAL for a in alerts)
    assert all("expired" in a.message and "ago" in a.message for a in alerts)
    assert [a.days_until for a in alerts] == sorted(a.days_until for a in alerts)


def test_expired_state_remains_critical_in_daily_digest():
    digest = AlertSystem(_registry_with_overdue_records()).generate_daily_digest()
    assert digest.state_status["NY"] == "critical"
    assert any(a.alert_type == "state_license_expiry" for a in digest.critical_alerts)


def test_unverified_or_missing_active_license_never_appears_healthy():
    registry = EntityRegistry(company_id="synthetic-tenant")
    registry.upsert_state_license(StateLicense(
        id="synthetic-pending-license", state_code="FL", license_type="Synthetic",
        license_number="synthetic-pending", status=LicenseStatus.PENDING,
        expiration_date=None,
    ))
    digest = AlertSystem(registry).generate_daily_digest()
    assert digest.state_status["FL"] == "critical"


def test_expiring_today_is_not_described_as_negative_or_future_days():
    registry = EntityRegistry(company_id="synthetic-tenant")
    registry.upsert_federal_permit(FederalPermit(
        id="synthetic-today", permit_number="synthetic-today",
        expiration_date=date.today(),
    ))
    [alert] = AlertSystem(registry).check_expirations(lookahead_days=0)
    assert "expires today" in alert.message
    assert alert.priority == AlertPriority.CRITICAL


def test_negative_lookahead_is_invalid():
    with pytest.raises(ValueError):
        AlertSystem(EntityRegistry(company_id="synthetic-tenant")).check_expirations(-1)
