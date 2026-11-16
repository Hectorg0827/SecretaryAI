"""Review-only gate for company-erasure case packets.

This module has no database, network, audit-writer, or DELETE capability.
Its positive result means only that a *packet of claimed evidence* is complete
enough for independent security/legal review. It is not an authorization token.
Callers MUST verify identities, holds, provenance, and receipt signatures from
trusted systems before relying on any value here. Never use it to execute SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class ErasureRequest:
    request_id: str
    company_id: str
    requester_id: str
    submitted_at: datetime
    expires_at: datetime
    status: Literal["submitted", "withdrawn", "cancelled"]


@dataclass(frozen=True)
class LegalEvidence:
    company_id: str
    outcome: Literal["clear", "hold", "unknown"]
    jurisdictions: tuple[str, ...]
    policy_reference: str
    verifier_id: str
    verified_at: datetime
    source_verified: bool


@dataclass(frozen=True)
class ErasureApproval:
    company_id: str
    approver_id: str
    capacity: Literal["legal", "security"]
    identity_source: str
    evidence_reference: str
    recorded_at: datetime
    identity_verified: bool
    revoked: bool = False


@dataclass(frozen=True)
class ScopeReview:
    company_id: str
    approved_systems: tuple[str, ...]
    vendor_and_backup_disposition: str
    reviewer_id: str
    reviewed_at: datetime
    source_verified: bool


@dataclass(frozen=True)
class ExternalAuditReceipt:
    request_id: str
    company_id: str
    evidence_id: str
    digest_sha256: str
    external_store: str
    externally_verified: bool
    immutable: bool
    recorded_at: datetime


@dataclass(frozen=True)
class ReviewPacket:
    request: ErasureRequest
    retention: LegalEvidence
    legal_hold: LegalEvidence
    approvals: tuple[ErasureApproval, ...]
    scope: ScopeReview
    external_receipt: ExternalAuditReceipt


@dataclass(frozen=True)
class ReviewAssessment:
    # There is intentionally NO "authorized"/"execute"/"approved" state.
    status: Literal["BLOCKED", "REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED"]
    reasons: tuple[str, ...]

    @property
    def permits_deletion(self) -> bool:
        """Intentionally permanent: even complete evidence cannot execute."""
        return False


_MAX_PACKET_LIFETIME = timedelta(hours=24)
_ALLOWED_EXTERNAL_STORES = frozenset(
    {"independent_worm_archive", "independent_compliance_vault"}
)
_EXPECTED_IDENTITY_SOURCE = "trusted_workforce_idp"


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and (
        value.utcoffset() is not None
    )


def _valid_uuid(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return UUID(value).int != 0
    except (ValueError, AttributeError, TypeError):
        return False


def evaluate_erasure_packet(
    packet: ReviewPacket, *, now: datetime
) -> ReviewAssessment:
    """Fail closed even if untrusted callers supply malformed field types."""
    try:
        return _evaluate_erasure_packet(packet, now=now)
    except (AttributeError, TypeError, ValueError, OverflowError):
        # No exception path can ever be interpreted as approval or deletion.
        return ReviewAssessment("BLOCKED", ("malformed_evidence",))


def _evaluate_erasure_packet(
    packet: ReviewPacket, *, now: datetime
) -> ReviewAssessment:
    """Validate packet completeness only, never authorize actual erasure.

    This is not a trusted identity, retention, or signature-verification
    service: those integrations must supply independently verified evidence.
    """
    if not _aware(now):
        return ReviewAssessment("BLOCKED", ("clock_not_trusted",))
    now = now.astimezone(timezone.utc)
    request = packet.request
    reasons: set[str] = set()

    if not (_valid_uuid(request.request_id)
            and _valid_uuid(request.company_id)
            and _valid_uuid(request.requester_id)):
        reasons.add("request_identity_invalid")
    if request.status != "submitted":
        reasons.add("request_not_active")
    if not _aware(request.submitted_at) or not _aware(request.expires_at):
        reasons.add("request_clock_invalid")
    else:
        submitted = request.submitted_at.astimezone(timezone.utc)
        expires = request.expires_at.astimezone(timezone.utc)
        if submitted > now or expires <= submitted or expires <= now:
            reasons.add("request_expired_or_future")
        if expires - submitted > _MAX_PACKET_LIFETIME:
            reasons.add("request_window_too_long")

    # An independently verified *legal* decision, not a model-derived number
    # of years, is required for each affected jurisdiction.
    for label, evidence in (("retention", packet.retention),
                            ("hold", packet.legal_hold)):
        if evidence.company_id != request.company_id:
            reasons.add(label + "_tenant_mismatch")
        if evidence.outcome != "clear":
            reasons.add(label + "_not_cleared")
        if (not evidence.source_verified or
            not evidence.policy_reference.strip() or
            not _valid_uuid(evidence.verifier_id) or
            not evidence.jurisdictions):
            reasons.add(label + "_source_unverified")
        if not _aware(evidence.verified_at):
            reasons.add(label + "_timestamp_invalid")
        elif (evidence.verified_at.astimezone(timezone.utc) > now or
              now - evidence.verified_at.astimezone(timezone.utc) >
              _MAX_PACKET_LIFETIME):
            reasons.add(label + "_stale")

    scope = packet.scope
    if scope.company_id != request.company_id:
        reasons.add("scope_tenant_mismatch")
    if (not scope.source_verified or
        not _valid_uuid(scope.reviewer_id) or
        not scope.approved_systems or
        not scope.vendor_and_backup_disposition.strip()):
        reasons.add("scope_unverified")
    if not _aware(scope.reviewed_at):
        reasons.add("scope_timestamp_invalid")
    elif (scope.reviewed_at.astimezone(timezone.utc) > now or
          now - scope.reviewed_at.astimezone(timezone.utc) >
          _MAX_PACKET_LIFETIME):
        reasons.add("scope_stale")

    approved_by_role: dict[str, set[str]] = {"legal": set(), "security": set()}
    all_approvers: set[str] = set()
    if not packet.approvals:
        reasons.add("two_independent_approvals_required")
    for approval in packet.approvals:
        if approval.company_id != request.company_id:
            reasons.add("approver_tenant_mismatch")
        if (approval.revoked or not approval.identity_verified or
            approval.identity_source != _EXPECTED_IDENTITY_SOURCE or
            not approval.evidence_reference.strip() or
            not _valid_uuid(approval.approver_id)):
            reasons.add("approval_unverified_or_revoked")
        if approval.approver_id == request.requester_id:
            reasons.add("self_approval_forbidden")
        if approval.approver_id in all_approvers:
            reasons.add("duplicate_approver")
        all_approvers.add(approval.approver_id)
        if approval.capacity not in approved_by_role:
            reasons.add("approval_capacity_invalid")
        else:
            approved_by_role[approval.capacity].add(approval.approver_id)
        if not _aware(approval.recorded_at):
            reasons.add("approval_timestamp_invalid")
        elif (_aware(request.submitted_at)
              and _aware(request.expires_at)
              and not (request.submitted_at <= approval.recorded_at <= now
                       < request.expires_at)):
            reasons.add("approval_stale_or_outside_request")

    if (not approved_by_role["legal"] or
        not approved_by_role["security"] or
        len(all_approvers) < 2):
        reasons.add("two_independent_approvals_required")

    receipt = packet.external_receipt
    if (receipt.request_id != request.request_id or
        receipt.company_id != request.company_id):
        reasons.add("external_receipt_scope_mismatch")
    if (not receipt.externally_verified or not receipt.immutable or
        receipt.external_store not in _ALLOWED_EXTERNAL_STORES or
        not receipt.evidence_id.strip() or
        not re.fullmatch(r"[a-f0-9]{64}", receipt.digest_sha256 or "")):
        reasons.add("independent_immutable_receipt_missing")
    if not _aware(receipt.recorded_at):
        reasons.add("receipt_timestamp_invalid")
    elif (receipt.recorded_at.astimezone(timezone.utc) > now or
          (_aware(request.submitted_at) and
           receipt.recorded_at < request.submitted_at)):
        reasons.add("receipt_outside_request_window")

    if reasons:
        return ReviewAssessment("BLOCKED", tuple(sorted(reasons)))
    return ReviewAssessment("REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED", ())
