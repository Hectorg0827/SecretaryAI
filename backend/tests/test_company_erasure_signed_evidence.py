"""Cryptographic erasure-evidence verification: synthetic Ed25519 issuers only.

Generated test keys are never persisted or used with an external provider.
Valid signatures prove attestation by configured test keys, never legal clearance
or authority to delete business records.
"""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.company_erasure.preflight import (
    ErasureRequest, LegalEvidence, ErasureApproval, ScopeReview,
    ExternalAuditReceipt,
)
from app.company_erasure.signed_evidence import (
    SignedEvidence, SignedReviewPacket, _signing_message,
    evaluate_signed_review_packet,
)

NOW = datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc)
COMPANY = "11111111-1111-4111-8111-111111111121"
REQUEST = "22222222-2222-4222-8222-222222222222"
REQUESTER = "33333333-3333-4333-8333-333333333333"
LEGAL = "44444444-4444-4444-8444-444444444444"
SECURITY = "55555555-5555-4555-8555-555555555555"
SCOPE = "66666666-6666-4666-8666-666666666666"

ISSUERS = {
    "request": ("case", "case-1"),
    "retention": ("legal", "legal-1"),
    "legal_hold": ("legal", "legal-1"),
    "approval": ("workforce", "idp-1"),
    "scope": ("inventory", "scope-1"),
    "external_receipt": ("archive", "worm-1"),
}


def claim_packet():
    request = ErasureRequest(
        request_id=REQUEST, company_id=COMPANY,
        requester_id=REQUESTER,
        submitted_at=NOW-timedelta(minutes=40),
        expires_at=NOW+timedelta(minutes=20),
        status="submitted",
    )
    legal = LegalEvidence(
        company_id=COMPANY, outcome="clear",
        jurisdictions=("US-NY",),
        policy_reference="attested-specific-legal-rule",
        verifier_id=LEGAL,
        verified_at=NOW-timedelta(minutes=10),
        source_verified=True,
    )
    hold = replace(legal, policy_reference="verified-current-hold-register")
    approvals = (
        ErasureApproval(
            company_id=COMPANY, approver_id=LEGAL, capacity="legal",
            identity_source="trusted_workforce_idp",
            evidence_reference="idp-workforce-legal-review",
            recorded_at=NOW-timedelta(minutes=7), identity_verified=True,
        ),
        ErasureApproval(
            company_id=COMPANY, approver_id=SECURITY, capacity="security",
            identity_source="trusted_workforce_idp",
            evidence_reference="idp-workforce-security-review",
            recorded_at=NOW-timedelta(minutes=6), identity_verified=True,
        ),
    )
    scope = ScopeReview(
        company_id=COMPANY, approved_systems=("database", "vendors", "backups"),
        vendor_and_backup_disposition="signed-review-only-backup-plan",
        reviewer_id=SCOPE, reviewed_at=NOW-timedelta(minutes=5),
        source_verified=True,
    )
    receipt = ExternalAuditReceipt(
        request_id=REQUEST, company_id=COMPANY,
        evidence_id="external-independent-write-once-test-ack",
        digest_sha256="a"*64,
        external_store="independent_worm_archive",
        externally_verified=True, immutable=True,
        recorded_at=NOW-timedelta(minutes=3),
    )
    return request, legal, hold, approvals, scope, receipt


def fake_issuers():
    keys = {name: Ed25519PrivateKey.generate()
            for name in ("case", "legal", "workforce", "inventory", "archive")}
    roots = {
        kind: {kid: keys[domain].public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)}
        for kind, (domain, kid) in ISSUERS.items()
    }
    return keys, roots


def sign(kind, record, keys, *,
         request_id=REQUEST, company_id=COMPANY,
         issued_at=NOW-timedelta(minutes=2)):
    domain, kid = ISSUERS[kind]
    unsigned = SignedEvidence(
        kind=kind, key_id=kid,
        request_id=request_id, company_id=company_id,
        issued_at=issued_at, record=record, signature_b64="",
    )
    signature = keys[domain].sign(_signing_message(unsigned))
    return replace(unsigned, signature_b64=base64.b64encode(signature).decode("ascii"))


def signed_fixture():
    keys, roots = fake_issuers()
    request, retention, hold, approvals, scope, receipt = claim_packet()
    packet = SignedReviewPacket(
        request=sign("request", request, keys),
        retention=sign("retention", retention, keys),
        legal_hold=sign("legal_hold", hold, keys),
        approvals=tuple(sign("approval", approval, keys) for approval in approvals),
        scope=sign("scope", scope, keys),
        external_receipt=sign("external_receipt", receipt, keys),
    )
    return keys, roots, packet


def check_blocked(packet, roots, now=NOW):
    result = evaluate_signed_review_packet(
        packet, trusted_public_keys=roots, now=now,
    )
    assert result.status == "BLOCKED"
    assert result.permits_deletion is False
    return result


def test_valid_signatures_only_complete_review_never_authorize():
    _, roots, packet = signed_fixture()
    result = evaluate_signed_review_packet(packet, trusted_public_keys=roots, now=NOW)
    assert result.status == "REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED"
    assert result.reasons == ()
    assert not result.permits_deletion


def test_absent_or_wrong_trust_roots_deny():
    _, roots, packet = signed_fixture()
    check_blocked(packet, {})
    swapped = dict(roots)
    attacker = Ed25519PrivateKey.generate()
    swapped["legal_hold"] = {"legal-1": attacker.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)}
    check_blocked(packet, swapped)


def test_cross_domain_key_reuse_is_refused():
    _, roots, packet = signed_fixture()
    shared = dict(roots)
    shared["external_receipt"] = {
        "worm-1": next(iter(roots["retention"].values()))
    }
    check_blocked(packet, shared)


def test_forged_signature_and_tampered_claims_deny():
    _, roots, packet = signed_fixture()
    fake = base64.b64encode(b"\x00" * 64).decode("ascii")
    check_blocked(replace(packet, external_receipt=replace(
        packet.external_receipt, signature_b64=fake)), roots)
    check_blocked(replace(packet, legal_hold=replace(
        packet.legal_hold, record=replace(
            packet.legal_hold.record, outcome="hold"
        ))), roots)
    check_blocked(replace(packet, approvals=(
        packet.approvals[0], replace(
            packet.approvals[1],
            record=replace(packet.approvals[1].record, revoked=True),
        ),
    )), roots)


def test_tenant_and_request_swap_fail_even_for_valid_signature():
    keys, roots, packet = signed_fixture()
    other_company = str(uuid4())
    other_request = str(uuid4())
    forged_company = sign(
        "legal_hold", packet.legal_hold.record,
        keys, company_id=other_company,
    )
    forged_request = sign(
        "external_receipt", packet.external_receipt.record,
        keys, request_id=other_request,
    )
    check_blocked(replace(packet, legal_hold=forged_company), roots)
    check_blocked(replace(packet, external_receipt=forged_request), roots)


def test_wrong_issuer_kind_cannot_be_substituted():
    _, roots, packet = signed_fixture()
    check_blocked(replace(
        packet, legal_hold=replace(packet.retention, kind="legal_hold"),
    ), roots)
    check_blocked(replace(
        packet, approvals=(packet.retention, packet.approvals[1]),
    ), roots)


def test_stale_or_future_signed_attestations_deny():
    keys, roots, packet = signed_fixture()
    expired = sign(
        "legal_hold", packet.legal_hold.record, keys,
        issued_at=NOW-timedelta(days=2),
    )
    future = sign(
        "scope", packet.scope.record, keys,
        issued_at=NOW+timedelta(seconds=30),
    )
    check_blocked(replace(packet, legal_hold=expired), roots)
    check_blocked(replace(packet, scope=future), roots)
    check_blocked(packet, roots, now=datetime(2026, 11, 16, 23, 0))


def test_signed_active_hold_and_revoked_employee_are_still_blocked():
    keys, roots, packet = signed_fixture()
    active_hold = sign(
        "legal_hold", replace(packet.legal_hold.record, outcome="hold"), keys,
    )
    revoked = sign(
        "approval", replace(packet.approvals[1].record, revoked=True), keys,
    )
    check_blocked(replace(packet, legal_hold=active_hold), roots)
    check_blocked(replace(packet, approvals=(packet.approvals[0], revoked)), roots)


@pytest.mark.parametrize("field", [
    "bad_signature_encoding", "bad_timestamp", "unknown_key",
    "missing_evidence", "extra_approvals", "unsigned_dataclass",
])
def test_invalid_envelopes_deny(field):
    _, roots, p = signed_fixture()
    if field == "bad_signature_encoding":
        p = replace(p, retention=replace(p.retention, signature_b64="☃"))
    elif field == "bad_timestamp":
        p = replace(p, retention=replace(p.retention, issued_at=None))
    elif field == "unknown_key":
        p = replace(p, scope=replace(p.scope, key_id="unknown-issuer"))
    elif field == "missing_evidence":
        p = replace(p, legal_hold=None)
    elif field == "extra_approvals":
        p = replace(p, approvals=p.approvals * 6)
    else:
        p = replace(p, request=p.request.record)
    check_blocked(p, roots)


def test_no_execution_or_private_key_issuer_code_in_verifier():
    import inspect
    import app.company_erasure.signed_evidence as verifier
    src = inspect.getsource(verifier)
    assert "Ed25519PrivateKey" not in src
    assert "import supabase" not in src
    assert "import asyncpg" not in src
    assert "DELETE FROM " not in src
    assert not hasattr(verifier, "execute_erasure")
