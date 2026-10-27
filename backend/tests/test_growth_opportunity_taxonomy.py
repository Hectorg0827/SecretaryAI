"""Typed, role-scoped Chat 5 opportunity drilldown handoff tests."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from app.intelligence.growth.opportunity_taxonomy import (
    GrowthSignal, PolicyScope, classify_growth_signal,
)


NOW = datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)


def _signal(**kwargs):
    value = GrowthSignal(
        company_id="synthetic-tenant", entity_ref="synthetic-sku",
        signal_code="margin_breach", department="pricing", metric_value=D("9.5"),
        source_quality="verified", source_record_id="synthetic-ledger",
        source_revision="rev-1", observed_at=NOW - timedelta(seconds=30),
    )
    return replace(value, **kwargs)


def _scope(**kwargs):
    value = PolicyScope(
        "synthetic-tenant", "manager", frozenset({"synthetic-sku"}), "decision-1",
    )
    return replace(value, **kwargs)


def _classify(signal=None, scope=None, seconds=300):
    return classify_growth_signal(signal or _signal(), scope=scope or _scope(),
                                  as_of=NOW, max_age_seconds=seconds)


def test_verified_margin_signal_maps_to_auditable_draft_only_red_alert():
    result = _classify()
    assert result.section == "red_alerts"
    assert result.status == "needs_review"
    assert result.display_metric == D("9.5")
    assert result.next_action_code == "review_margin_waterfall"
    assert result.approval_required and not result.execution_claimed


def test_all_classified_statuses_use_gate_a_digest_entry_vocabulary():
    allowed = {"open", "resolved", "needs_review", "source_unavailable"}
    for signal in (_signal(), _signal(source_quality="partial"),
                   _signal(source_quality="unavailable")):
        result = _classify(signal)
        assert result.status in allowed
        assert result.approval_required is True
        assert result.execution_claimed is False


def test_missing_compliance_source_never_downgrades_critical_alert():
    signal = _signal(signal_code="compliance_renewal_due", department="compliance",
                     source_quality="unavailable")
    result = _classify(signal)
    assert result.section == "red_alerts"
    assert result.status == "source_unavailable"
    assert result.display_metric is None
    assert result.next_action_code == "verify_source_evidence"


def test_verified_portfolio_stockout_is_an_operations_review_not_a_disposal_action():
    signal = _signal(signal_code="portfolio_stockout_with_demand",
                     department="portfolio", metric_value=D("0"))
    result = _classify(signal, _scope(effective_role="back_office"))
    assert result.section == "red_alerts"
    assert result.next_action_code == "review_supply_availability"
    assert result.approval_required and not result.execution_claimed


def test_partial_or_stale_data_cannot_be_shown_as_verified_metric():
    for signal in (_signal(source_quality="partial"),
                   _signal(observed_at=NOW - timedelta(minutes=10)),
                   _signal(source_revision=""),
                   _signal(observed_at=NOW + timedelta(minutes=1))):
        result = _classify(signal)
        assert result.status == "source_unavailable"
        assert result.display_metric is None


def test_tenant_role_record_and_policy_scope_all_fail_closed():
    for scope in (_scope(company_id="other"), _scope(effective_role="sales_rep"),
                  _scope(permitted_entity_refs=frozenset()),
                  _scope(policy_decision_id="")):
        with pytest.raises(PermissionError):
            _classify(scope=scope)


def test_hr_detail_not_visible_to_existing_general_roles():
    signal = _signal(signal_code="hr_training_due", department="hr")
    with pytest.raises(PermissionError):
        _classify(signal)
    result = _classify(signal, _scope(effective_role="hr_admin"))
    assert result.department == "hr"
    assert not result.execution_claimed


@pytest.mark.parametrize("change", [
    {"signal_code": "freeform_model_claim"},
    {"department": "hr"},
    {"source_quality": "unknown"},
    {"metric_value": D("NaN")},
    {"observed_at": datetime(2026, 10, 26, 22, 0)},
])
def test_invalid_signal_contract_is_rejected(change):
    with pytest.raises((ValueError, PermissionError)):
        _classify(_signal(**change))
