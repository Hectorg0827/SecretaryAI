"""Contract tests: a compliance checklist never authorizes a shipment."""

from datetime import date, timedelta

from app.intelligence.compliance.evidence_gate import (
    REQUIRED_TOPICS,
    ReadinessStatus,
    RegulatoryEvidence,
    assess_compliance_readiness,
)
from app.intelligence.compliance.models import (
    AlcoholProduct,
    BrandRegistration,
    BrandRegStatus,
    COLARecord,
    ComplianceCheckResult,
    ComplianceIssue,
    FederalPermit,
    FederalPermitType,
    IssueSeverity,
    LicenseStatus,
    ProductType,
    StateLicense,
)
from app.intelligence.compliance.entity_registry import EntityRegistry
from app.intelligence.compliance.rules_engine import RulesEngine


TODAY = date(2026, 10, 26)


def _check(approved=True):
    return ComplianceCheckResult(
        approved=approved, product_id="synthetic-sku", state_code="NY", quantity_cases=8,
    )


def _source(topic, **overrides):
    kwargs = dict(
        company_id="synthetic-tenant", state_code="NY", topic=topic,
        source_url="https://example.gov/current-regulation",
        source_revision="revision-5", effective_on=TODAY - timedelta(days=60),
        reviewed_on=TODAY - timedelta(days=1), review_record_id="audit-record-1",
    )
    kwargs.update(overrides)
    return RegulatoryEvidence(**kwargs)


def test_checklist_pass_alone_is_not_approval():
    result = assess_compliance_readiness("synthetic-tenant", _check(), [], as_of=TODAY)
    assert result.status is ReadinessStatus.SOURCE_REVIEW_REQUIRED
    assert not result.authorized_to_ship
    assert set(result.missing_topics) == set(REQUIRED_TOPICS)


def test_missing_or_stale_rule_revision_requires_source_review():
    evidence = [_source(t) for t in REQUIRED_TOPICS]
    evidence[0] = _source(REQUIRED_TOPICS[0], reviewed_on=TODAY - timedelta(days=181))
    result = assess_compliance_readiness("synthetic-tenant", _check(), evidence, as_of=TODAY)
    assert result.status is ReadinessStatus.SOURCE_REVIEW_REQUIRED
    assert result.missing_topics == (REQUIRED_TOPICS[0],)


def test_wrong_jurisdiction_future_review_and_missing_record_do_not_count():
    bad = [
        _source("federal_permit", state_code="NJ"),
        _source("federal_label", reviewed_on=TODAY + timedelta(days=1)),
        _source("state_tax", review_record_id=""),
        _source("state_license", source_url="http://example.gov/rule"),
        _source("state_distribution", company_id="other-tenant"),
    ]
    result = assess_compliance_readiness("synthetic-tenant", _check(), bad, as_of=TODAY)
    assert set(bad_item.topic for bad_item in bad).issubset(result.missing_topics)


def test_all_evidence_still_requires_authenticated_human_review():
    result = assess_compliance_readiness(
        "synthetic-tenant", _check(), [_source(t) for t in REQUIRED_TOPICS], as_of=TODAY,
    )
    assert result.status is ReadinessStatus.HUMAN_REVIEW_REQUIRED
    assert result.authorized_to_ship is False


def test_checklist_blocker_takes_priority_over_documentation():
    check = _check()
    check.issues.append(ComplianceIssue(severity=IssueSeverity.BLOCKER, category="license"))
    result = assess_compliance_readiness(
        "synthetic-tenant", check, [_source(t) for t in REQUIRED_TOPICS], as_of=TODAY,
    )
    assert result.status is ReadinessStatus.BLOCKED
    assert not result.authorized_to_ship


def test_invalid_shipment_quantity_is_blocked_even_if_legacy_approved():
    check = _check()
    check.quantity_cases = 0
    result = assess_compliance_readiness("synthetic-tenant", check, [], as_of=TODAY)
    assert result.status is ReadinessStatus.BLOCKED
    assert not result.authorized_to_ship


def _registry():
    registry = EntityRegistry("synthetic-tenant")
    registry.upsert_product(AlcoholProduct(id="sku", name="Synthetic Wine", product_type=ProductType.WINE))
    registry.upsert_federal_permit(FederalPermit(
        id="permit", permit_type=FederalPermitType.IMPORTER, permit_number="synthetic-permit",
    ))
    registry.upsert_cola(COLARecord(id="cola", product_id="sku", cola_number="synthetic-cola"))
    registry.upsert_state_license(StateLicense(
        id="license", state_code="NY", product_types=[ProductType.WINE],
    ))
    registry.upsert_brand_registration(BrandRegistration(
        id="brand", product_id="sku", state_code="NY",
    ))
    return registry


def test_legacy_rules_block_zero_quantity_instead_of_negative_fee():
    result = RulesEngine(_registry()).can_sell_product_in_state("sku", "NY", -3)
    assert not result.approved
    assert any(issue.category == "quantity" for issue in result.blockers)
    assert result.total_compliance_cost == 0


def test_revoked_federal_permit_is_not_treated_as_active():
    registry = _registry()
    registry.upsert_federal_permit(FederalPermit(
        id="permit", permit_type=FederalPermitType.IMPORTER,
        permit_number="synthetic-permit", status=LicenseStatus.REVOKED,
    ))
    result = RulesEngine(registry).can_sell_product_in_state("sku", "NY")
    assert not result.approved
    assert any(issue.category == "federal_permit" for issue in result.blockers)


def test_suspended_cola_is_not_treated_as_active():
    registry = _registry()
    registry.upsert_cola(COLARecord(
        id="cola", product_id="sku", status=LicenseStatus.SUSPENDED,
    ))
    result = RulesEngine(registry).can_sell_product_in_state("sku", "NY")
    assert not result.approved
    assert any(issue.category == "cola" for issue in result.blockers)


def test_revoked_state_license_is_not_treated_as_active():
    registry = _registry()
    registry.upsert_state_license(StateLicense(
        id="license", state_code="NY", product_types=[ProductType.WINE],
        status=LicenseStatus.REVOKED,
    ))
    result = RulesEngine(registry).can_sell_product_in_state("sku", "NY")
    assert not result.approved
    assert any(issue.category == "state_license" for issue in result.blockers)


def test_pending_brand_registration_is_not_treated_as_active():
    registry = _registry()
    registry.upsert_brand_registration(BrandRegistration(
        id="brand", product_id="sku", state_code="NY", status=BrandRegStatus.PENDING,
    ))
    result = RulesEngine(registry).can_sell_product_in_state("sku", "NY")
    assert not result.approved
    assert any(issue.category == "brand_registration" for issue in result.blockers)
