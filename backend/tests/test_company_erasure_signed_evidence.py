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
    evidence_commitment_digest, evaluate_signed_review_packet,
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
    bound_digest = evidence_commitment_digest(packet)
    bound_receipt = replace(packet.external_receipt.record, digest_sha256=bound_digest)
    packet = replace(packet, external_receipt=sign("external_receipt", bound_receipt, keys))
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


def test_independently_signed_receipt_for_wrong_evidence_fails():
    keys, roots, packet = signed_fixture()
    # A real archive issuer's signature alone is insufficient: the receipt
    # must attest to the *exact* case evidence in this reviewed packet.
    different_receipt = sign(
        "external_receipt",
        replace(packet.external_receipt.record, digest_sha256="f"*64),
        keys,
    )
    check_blocked(replace(packet, external_receipt=different_receipt), roots)


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


# Phase 3: injected read-only provider contracts. The fakes below DO NOT
# represent verified external legal/IdP/archive integrations in any deployment.
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.company_erasure.provider_recheck import (
    SourceBundle, CaseStatus, LegalStatus, WorkforceStatus,
    CurrentScope, ArchiveStatus, evaluate_current_source_review,
)


def synthetic_current_sources(packet):
    request = packet.request.record
    retention = packet.retention.record
    hold = packet.legal_hold.record
    scope = packet.scope.record
    receipt = packet.external_receipt.record

    async def current_identity(person_id, company_id, capacity):
        return WorkforceStatus(
            company_id=company_id, person_id=person_id,
            capacity=capacity, active=True,
            permission_revoked=False, observed_at=NOW,
        )

    return SourceBundle(
        cases=SimpleNamespace(current_case=AsyncMock(return_value=CaseStatus(
            request_id=request.request_id, company_id=request.company_id,
            requester_id=request.requester_id, state="submitted",
            observed_at=NOW,
        ))),
        legal=SimpleNamespace(
            current_retention=AsyncMock(return_value=LegalStatus(
                company_id=COMPANY, outcome="clear",
                jurisdictions=retention.jurisdictions,
                policy_reference=retention.policy_reference,
                observed_at=NOW,
            )),
            current_hold=AsyncMock(return_value=LegalStatus(
                company_id=COMPANY, outcome="clear",
                jurisdictions=hold.jurisdictions,
                policy_reference=hold.policy_reference,
                observed_at=NOW,
            )),
        ),
        workforce=SimpleNamespace(current_identity=AsyncMock(
            side_effect=current_identity
        )),
        inventory=SimpleNamespace(current_scope=AsyncMock(return_value=CurrentScope(
            company_id=COMPANY,
            approved_systems=scope.approved_systems,
            vendor_and_backup_disposition=scope.vendor_and_backup_disposition,
            observed_at=NOW,
        ))),
        archive=SimpleNamespace(readback=AsyncMock(return_value=ArchiveStatus(
            request_id=REQUEST, company_id=COMPANY,
            evidence_id=receipt.evidence_id,
            digest_sha256=receipt.digest_sha256,
            independent_store=receipt.external_store,
            durable=True, immutable=True, observed_at=NOW,
        ))),
    )


async def review_with(providers, packet, roots, *, now=NOW):
    return await evaluate_current_source_review(
        packet, trusted_public_keys=roots,
        providers=providers, now=now,
    )


@pytest.mark.asyncio
async def test_current_sources_missing_blocks_even_valid_signature():
    _, roots, packet = signed_fixture()
    result = await review_with(None, packet, roots)
    assert result.status == "BLOCKED"
    assert "independent_providers_not_configured" in result.reasons
    assert result.permits_deletion is False


@pytest.mark.asyncio
async def test_valid_current_sources_only_support_human_review():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    result = await review_with(providers, packet, roots)
    assert result.status == "REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED"
    assert not result.permits_deletion
    assert providers.archive.readback.await_count == 1
    assert providers.workforce.current_identity.await_count == 3


@pytest.mark.asyncio
async def test_invalid_signature_prevents_all_provider_io():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    packet = replace(packet, scope=replace(packet.scope, signature_b64="invalid"))
    result = await review_with(providers, packet, roots)
    assert result.status == "BLOCKED"
    providers.cases.current_case.assert_not_awaited()
    providers.archive.readback.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_case_cancelled_denies_even_when_old_evidence_signed():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    providers.cases.current_case.return_value = replace(
        providers.cases.current_case.return_value, state="cancelled",
    )
    result = await review_with(providers, packet, roots)
    assert result.status == "BLOCKED"
    assert "case_current_state_unverified" in result.reasons
    providers.archive.readback.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_legal_hold_and_stale_legal_source_denied():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    providers.legal.current_hold.return_value = replace(
        providers.legal.current_hold.return_value, outcome="hold",
    )
    result = await review_with(providers, packet, roots)
    assert "legal_or_hold_current_state_unverified" in result.reasons
    providers.legal.current_hold.return_value = replace(
        providers.legal.current_hold.return_value,
        outcome="clear", observed_at=NOW-timedelta(minutes=6),
    )
    result = await review_with(providers, packet, roots)
    assert "legal_or_hold_current_state_unverified" in result.reasons


@pytest.mark.asyncio
async def test_revoked_or_wrong_tenant_current_identity_denied():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)

    async def revoked_identity(person_id, company_id, capacity):
        return WorkforceStatus(
            company_id=company_id, person_id=person_id,
            capacity=capacity, active=True,
            permission_revoked=person_id == SECURITY,
            observed_at=NOW,
        )

    providers.workforce.current_identity.side_effect = revoked_identity
    result = await review_with(providers, packet, roots)
    assert "approver_current_identity_unverified" in result.reasons

    async def wrong_tenant(person_id, company_id, capacity):
        return WorkforceStatus(
            company_id=str(uuid4()), person_id=person_id,
            capacity=capacity, active=True,
            permission_revoked=False, observed_at=NOW,
        )

    providers.workforce.current_identity.side_effect = wrong_tenant
    result = await review_with(providers, packet, roots)
    assert "requester_current_identity_unverified" in result.reasons


@pytest.mark.asyncio
async def test_vendor_backup_scope_drift_denied():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    providers.inventory.current_scope.return_value = replace(
        providers.inventory.current_scope.return_value,
        approved_systems=("database",),
    )
    result = await review_with(providers, packet, roots)
    assert "vendor_backup_scope_unverified" in result.reasons


@pytest.mark.asyncio
async def test_archive_readback_requires_durable_current_exact_receipt():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    providers.archive.readback.return_value = replace(
        providers.archive.readback.return_value,
        durable=False,
    )
    result = await review_with(providers, packet, roots)
    assert "independent_archive_readback_unverified" in result.reasons
    providers.archive.readback.return_value = replace(
        providers.archive.readback.return_value,
        durable=True, digest_sha256="f"*64,
    )
    result = await review_with(providers, packet, roots)
    assert "independent_archive_readback_unverified" in result.reasons


@pytest.mark.asyncio
async def test_provider_outage_or_malformed_reply_is_never_review_complete():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    providers.legal.current_hold.side_effect = RuntimeError("synthetic outage")
    result = await review_with(providers, packet, roots)
    assert result.status == "BLOCKED"
    assert "independent_provider_unavailable" in result.reasons

    providers = synthetic_current_sources(packet)
    providers.archive.readback.return_value = {"claimed": True}
    result = await review_with(providers, packet, roots)
    assert result.status == "BLOCKED"
    assert "independent_archive_readback_unverified" in result.reasons


@pytest.mark.asyncio
async def test_untrusted_clock_blocks_without_io():
    _, roots, packet = signed_fixture()
    providers = synthetic_current_sources(packet)
    result = await review_with(
        providers, packet, roots,
        now=datetime(2026, 11, 16, 23, 0),
    )
    assert result.status == "BLOCKED"
    providers.cases.current_case.assert_not_awaited()


def test_current_source_coordinator_does_not_execute_deletion():
    import inspect
    import app.company_erasure.provider_recheck as coordinator

    code = inspect.getsource(coordinator)
    assert "import asyncpg" not in code
    assert "import supabase" not in code
    assert "DELETE FROM " not in code
    assert not hasattr(coordinator, "execute_erasure")
