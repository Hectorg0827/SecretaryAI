"""Tests for §21 — Governance, Security & Access Control."""
import pytest
from app.intelligence.wholesale_distribution.governance import (
    Action,
    AnomalyDetector,
    AnomalyType,
    DataLevel,
    GovernanceEngine,
    Role,
    check_permission,
    classify_field,
)


class TestDataClassification:
    def test_api_key_is_restricted(self):
        assert classify_field("api_key") == DataLevel.RESTRICTED

    def test_customer_email_is_confidential(self):
        assert classify_field("customer_email") == DataLevel.CONFIDENTIAL

    def test_inventory_count_is_internal(self):
        assert classify_field("inventory_count") == DataLevel.INTERNAL

    def test_product_name_is_public(self):
        assert classify_field("product_name") == DataLevel.PUBLIC

    def test_unknown_field_defaults_to_internal(self):
        assert classify_field("some_random_field_xyz") == DataLevel.INTERNAL


class TestAuthorityMatrix:
    def test_viewer_can_view_internal(self):
        allowed, _ = check_permission(Role.VIEWER, Action.VIEW, DataLevel.INTERNAL)
        assert allowed

    def test_viewer_cannot_view_confidential(self):
        allowed, _ = check_permission(Role.VIEWER, Action.VIEW, DataLevel.CONFIDENTIAL)
        assert not allowed

    def test_viewer_cannot_draft(self):
        allowed, _ = check_permission(Role.VIEWER, Action.DRAFT, DataLevel.PUBLIC)
        assert not allowed

    def test_analyst_can_draft_confidential(self):
        allowed, _ = check_permission(Role.ANALYST, Action.DRAFT, DataLevel.CONFIDENTIAL)
        assert allowed

    def test_analyst_cannot_view_restricted(self):
        allowed, _ = check_permission(Role.ANALYST, Action.VIEW, DataLevel.RESTRICTED)
        assert not allowed

    def test_manager_can_approve_confidential(self):
        allowed, _ = check_permission(Role.MANAGER, Action.APPROVE, DataLevel.CONFIDENTIAL)
        assert allowed

    def test_manager_cannot_delete_restricted(self):
        allowed, _ = check_permission(Role.MANAGER, Action.DELETE, DataLevel.RESTRICTED)
        assert not allowed

    def test_executive_can_view_restricted(self):
        allowed, _ = check_permission(Role.EXECUTIVE, Action.VIEW, DataLevel.RESTRICTED)
        assert allowed

    def test_system_admin_can_configure(self):
        allowed, _ = check_permission(Role.SYSTEM_ADMIN, Action.CONFIGURE, DataLevel.RESTRICTED)
        assert allowed

    def test_reason_returned_on_deny(self):
        allowed, reason = check_permission(Role.VIEWER, Action.EXECUTE, DataLevel.INTERNAL)
        assert not allowed
        assert reason != ""


class TestAnomalyDetector:
    @pytest.fixture
    def detector(self):
        return AnomalyDetector()

    def test_bulk_export_above_threshold(self, detector):
        anomaly = detector.detect_bulk_export(
            user_id="u-1", export_count_last_hour=10, data_level=DataLevel.CONFIDENTIAL
        )
        assert anomaly is not None
        assert anomaly.anomaly_type == AnomalyType.BULK_EXPORT
        assert anomaly.auto_block is True  # confidential data

    def test_bulk_export_below_threshold_no_anomaly(self, detector):
        anomaly = detector.detect_bulk_export(
            user_id="u-2", export_count_last_hour=2, data_level=DataLevel.INTERNAL
        )
        assert anomaly is None

    def test_off_hours_access_flagged(self, detector):
        anomaly = detector.detect_off_hours_access(
            user_id="u-3", access_hour=2, data_level=DataLevel.CONFIDENTIAL
        )
        assert anomaly is not None
        assert anomaly.anomaly_type == AnomalyType.OFF_HOURS_ACCESS

    def test_business_hours_no_anomaly(self, detector):
        anomaly = detector.detect_off_hours_access(
            user_id="u-4", access_hour=10, data_level=DataLevel.CONFIDENTIAL
        )
        assert anomaly is None

    def test_known_pattern_no_anomaly(self, detector):
        anomaly = detector.detect_off_hours_access(
            user_id="u-5", access_hour=2, data_level=DataLevel.CONFIDENTIAL,
            is_known_pattern=True,
        )
        assert anomaly is None

    def test_role_escalation_blocked(self, detector):
        anomaly = detector.detect_role_escalation_attempt(
            user_id="u-6",
            role=Role.VIEWER,
            requested_action=Action.DELETE,
            data_level=DataLevel.CONFIDENTIAL,
        )
        assert anomaly is not None
        assert anomaly.auto_block is True

    def test_permitted_action_no_anomaly(self, detector):
        anomaly = detector.detect_role_escalation_attempt(
            user_id="u-7",
            role=Role.MANAGER,
            requested_action=Action.APPROVE,
            data_level=DataLevel.CONFIDENTIAL,
        )
        assert anomaly is None

    def test_rapid_modification_flagged(self, detector):
        anomaly = detector.detect_rapid_modification(
            user_id="u-8", modifications_last_minute=50
        )
        assert anomaly is not None
        assert anomaly.severity == "critical"
        assert anomaly.auto_block is True

    def test_normal_modification_rate_ok(self, detector):
        anomaly = detector.detect_rapid_modification(
            user_id="u-9", modifications_last_minute=5
        )
        assert anomaly is None

    def test_failed_auth_spike_detected(self, detector):
        anomaly = detector.detect_failed_auth_spike(
            user_id="u-10", failed_attempts_last_5_min=15
        )
        assert anomaly is not None
        assert anomaly.auto_block is True


class TestGovernanceEngine:
    @pytest.fixture
    def gov(self):
        return GovernanceEngine(company_id="co-test")

    def test_check_access_analyst_view_confidential(self, gov):
        allowed, _ = gov.check_access("u-1", "analyst", "view", "customer_name")
        assert allowed

    def test_check_access_viewer_blocked_on_confidential(self, gov):
        allowed, reason = gov.check_access("u-2", "viewer", "view", "gross_margin")
        assert not allowed
        assert reason != ""

    def test_filter_response_redacts_restricted(self, gov):
        data = {
            "product_name": "Widget A",
            "gross_margin": 0.35,
            "api_key": "sk-secret-key",
        }
        filtered = gov.filter_for_role(data, "analyst")
        assert filtered["product_name"] == "Widget A"
        assert filtered["gross_margin"] == 0.35      # confidential — analyst can see
        assert filtered["api_key"] == "[REDACTED]"   # restricted — analyst cannot

    def test_filter_response_viewer_redacts_confidential(self, gov):
        data = {
            "product_name": "Widget B",
            "customer_email": "john@example.com",
        }
        filtered = gov.filter_for_role(data, "viewer")
        assert filtered["product_name"] == "Widget B"
        assert filtered["customer_email"] == "[REDACTED]"

    def test_retention_policy_returned(self, gov):
        policy = gov.get_retention_policy("orders")
        assert policy["retention_years"] == 7
        assert policy["legal_hold_capable"] is True
