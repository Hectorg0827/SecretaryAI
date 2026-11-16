"""Company-erasure review packet tests: zero DB/network and zero DELETE.

The "complete" state is deliberately NOT an execution authorization.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.company_erasure.preflight import (
    ErasureRequest, LegalEvidence, ErasureApproval, ScopeReview,
    ExternalAuditReceipt, ReviewPacket, evaluate_erasure_packet,
)

NOW = datetime(2026, 11, 16, 22, 30, tzinfo=timezone.utc)
COMPANY = "11111111-1111-4111-8111-111111111121"
COMPANY_B = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
REQUESTER = "44444444-4444-4444-8444-444444444444"
LEGAL_REVIEWER = "55555555-5555-4555-8555-555555555555"
SECURITY_REVIEWER = "66666666-6666-4666-8666-666666666666"
SCOPE_REVIEWER = "77777777-7777-4777-8777-777777777777"


def packet() -> ReviewPacket:
    r = ErasureRequest(
        request_id=REQUEST, company_id=COMPANY, requester_id=REQUESTER,
        submitted_at=NOW-timedelta(hours=1),
        expires_at=NOW+timedelta(hours=1), status="submitted",
    )
    retention = LegalEvidence(
        company_id=COMPANY, outcome="clear", jurisdictions=("US-NY",),
        policy_reference="policy-vetted-by-counsel", verifier_id=LEGAL_REVIEWER,
        verified_at=NOW-timedelta(minutes=15), source_verified=True,
    )
    hold = replace(retention, policy_reference="hold-clearance-ref")
    legal = ErasureApproval(
        company_id=COMPANY, approver_id=LEGAL_REVIEWER, capacity="legal",
        identity_source="trusted_workforce_idp", evidence_reference="review-legal-1",
        recorded_at=NOW-timedelta(minutes=10), identity_verified=True,
    )
    security = replace(legal, approver_id=SECURITY_REVIEWER,
                       capacity="security", evidence_reference="review-sec-2")
    scope = ScopeReview(
        company_id=COMPANY, approved_systems=("database", "backups", "vendors"),
        vendor_and_backup_disposition="reviewed-scope-evidence",
        reviewer_id=SCOPE_REVIEWER, reviewed_at=NOW-timedelta(minutes=5),
        source_verified=True,
    )
    receipt = ExternalAuditReceipt(
        request_id=REQUEST, company_id=COMPANY, evidence_id="worm-record-1",
        digest_sha256="a" * 64, external_store="independent_worm_archive",
        externally_verified=True, immutable=True,
        recorded_at=NOW-timedelta(minutes=3),
    )
    return ReviewPacket(r, retention, hold, (legal, security), scope, receipt)


def assert_blocked(case: ReviewPacket, reason: str):
    result = evaluate_erasure_packet(case, now=NOW)
    assert result.status == "BLOCKED"
    assert reason in result.reasons
    assert result.permits_deletion is False


def test_complete_packet_is_still_not_authorized_to_delete():
    result = evaluate_erasure_packet(packet(), now=NOW)
    assert result.status == "REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED"
    assert result.reasons == ()
    assert result.permits_deletion is False


@pytest.mark.parametrize("new_status", ["withdrawn", "cancelled"])
def test_cancelled_or_withdrawn_request_denied(new_status):
    p = packet()
    assert_blocked(replace(p, request=replace(p.request, status=new_status)),
                   "request_not_active")


def test_expired_or_future_or_overlong_request_denied():
    p = packet()
    assert_blocked(replace(p, request=replace(p.request,
                         expires_at=NOW-timedelta(seconds=1))),
                   "request_expired_or_future")
    assert_blocked(replace(p, request=replace(p.request,
                         submitted_at=NOW+timedelta(minutes=1))),
                   "request_expired_or_future")
    assert_blocked(replace(p, request=replace(p.request,
                         expires_at=NOW+timedelta(days=3))),
                   "request_window_too_long")


def test_retention_hold_and_mismatched_tenant_block():
    p = packet()
    assert_blocked(replace(p, legal_hold=replace(p.legal_hold, outcome="hold")),
                   "hold_not_cleared")
    assert_blocked(replace(p, retention=replace(p.retention, outcome="unknown")),
                   "retention_not_cleared")
    assert_blocked(replace(p, retention=replace(p.retention,
                         company_id=COMPANY_B)), "retention_tenant_mismatch")
    assert_blocked(replace(p, legal_hold=replace(p.legal_hold,
                         source_verified=False)), "hold_source_unverified")


def test_no_unverified_or_stale_legal_evidence():
    p = packet()
    assert_blocked(replace(p, retention=replace(p.retention,
                         policy_reference="")), "retention_source_unverified")
    assert_blocked(replace(p, retention=replace(p.retention,
                         jurisdictions=())), "retention_source_unverified")
    assert_blocked(replace(p, legal_hold=replace(p.legal_hold,
                         verified_at=NOW-timedelta(days=2))), "hold_stale")


def test_approvals_must_be_independent_two_roles_and_fresh():
    p = packet()
    assert_blocked(replace(p, approvals=(p.approvals[0],)),
                   "two_independent_approvals_required")
    assert_blocked(replace(p, approvals=(p.approvals[0], p.approvals[0])),
                   "duplicate_approver")
    assert_blocked(replace(p, approvals=(
        p.approvals[0], replace(p.approvals[1], approver_id=REQUESTER)
    )), "self_approval_forbidden")
    assert_blocked(replace(p, approvals=(
        p.approvals[0], replace(p.approvals[1], revoked=True)
    )), "approval_unverified_or_revoked")
    assert_blocked(replace(p, approvals=(
        p.approvals[0], replace(p.approvals[1], identity_verified=False)
    )), "approval_unverified_or_revoked")
    assert_blocked(replace(p, approvals=(
        p.approvals[0], replace(p.approvals[1],
                               identity_source="user_metadata")
    )), "approval_unverified_or_revoked")
    assert_blocked(replace(p, approvals=(
        p.approvals[0], replace(p.approvals[1], company_id=COMPANY_B)
    )), "approver_tenant_mismatch")
    assert_blocked(replace(p, approvals=(
        p.approvals[0], replace(p.approvals[1],
                               recorded_at=NOW-timedelta(days=2))
    )), "approval_stale_or_outside_request")


def test_scope_vendor_backup_and_second_tenant_checks():
    p = packet()
    assert_blocked(replace(p, scope=replace(p.scope, company_id=COMPANY_B)),
                   "scope_tenant_mismatch")
    assert_blocked(replace(p, scope=replace(p.scope,
                         vendor_and_backup_disposition="")), "scope_unverified")
    assert_blocked(replace(p, scope=replace(p.scope,
                         source_verified=False)), "scope_unverified")


def test_external_receipt_must_be_independent_immutable_and_scoped():
    p = packet()
    assert_blocked(replace(p, external_receipt=replace(p.external_receipt,
                         external_store="public.supabase_audit_events")),
                   "independent_immutable_receipt_missing")
    assert_blocked(replace(p, external_receipt=replace(p.external_receipt,
                         externally_verified=False)),
                   "independent_immutable_receipt_missing")
    assert_blocked(replace(p, external_receipt=replace(p.external_receipt,
                         immutable=False)),
                   "independent_immutable_receipt_missing")
    assert_blocked(replace(p, external_receipt=replace(p.external_receipt,
                         digest_sha256="a"*63)),
                   "independent_immutable_receipt_missing")
    assert_blocked(replace(p, external_receipt=replace(p.external_receipt,
                         company_id=COMPANY_B)),
                   "external_receipt_scope_mismatch")
    assert_blocked(replace(p, external_receipt=replace(p.external_receipt,
                         request_id=COMPANY_B)),
                   "external_receipt_scope_mismatch")


def test_invalid_ids_and_untrusted_clock_never_pass():
    p = packet()
    assert_blocked(replace(p, request=replace(p.request,
                         company_id="not-a-uuid")), "request_identity_invalid")
    result = evaluate_erasure_packet(p, now=datetime(2026, 11, 16, 22, 30))
    assert result.status == "BLOCKED"
    assert result.reasons == ("clock_not_trusted",)
    assert result.permits_deletion is False


def test_evaluator_has_no_execution_surface():
    import inspect
    import app.company_erasure.preflight as gate

    source = inspect.getsource(gate)
    # The literal module has no execution connectors or SQL statements.
    # Test private design contract, not the status string.
    assert "import asyncpg" not in source
    assert "import supabase" not in source
    assert "execute_sql" not in source
    assert "DELETE FROM " not in source
    assert "drop table " not in source.lower()
    assert not hasattr(gate, "execute_erasure")


@pytest.mark.parametrize("bad_field", ["receipt_digest_type", "policy_reference_type",
                                        "missing_request", "approval_timestamp_type"])
def test_malformed_untrusted_field_types_fail_closed(bad_field):
    p = packet()
    if bad_field == "receipt_digest_type":
        p = replace(p, external_receipt=replace(p.external_receipt, digest_sha256=1))
    elif bad_field == "policy_reference_type":
        p = replace(p, legal_hold=replace(p.legal_hold, policy_reference=None))
    elif bad_field == "missing_request":
        p = replace(p, request=None)
    else:
        p = replace(p, approvals=(replace(p.approvals[0], recorded_at=None), p.approvals[1]))
    result = evaluate_erasure_packet(p, now=NOW)
    assert result.status == "BLOCKED"
    assert result.permits_deletion is False
