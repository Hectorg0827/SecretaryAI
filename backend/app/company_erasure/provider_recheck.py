"""Read-only, fail-closed CURRENT-SOURCE checks for erasure *review*.

This is a contract for future independent provider adapters, NOT a provider
integration and NEVER a DELETE authorization. No providers or credentials are
configured by this module. Caller-supplied claims/booleans alone are not proof
of an IdP, legal hold register, vendor scope, or WORM archive.

An authorized deployment must construct adapters from server-owned secrets,
enforce TLS/authentication and pin endpoints independently of request data.
The function only returns a review assessment; it never mints a capability.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, Protocol

from .preflight import ReviewAssessment
from .signed_evidence import (
    SignedReviewPacket, evaluate_signed_review_packet,
    evidence_commitment_digest,
)

_MAX_SOURCE_AGE = timedelta(minutes=5)
_SOURCE_TIMEOUT_SECONDS = 2.0
_MAX_APPROVALS = 10


@dataclass(frozen=True)
class CaseStatus:
    request_id: str
    company_id: str
    requester_id: str
    state: str
    observed_at: datetime


@dataclass(frozen=True)
class LegalStatus:
    company_id: str
    outcome: Literal["clear", "hold", "unknown"]
    jurisdictions: tuple[str, ...]
    policy_reference: str
    observed_at: datetime


@dataclass(frozen=True)
class WorkforceStatus:
    company_id: str
    person_id: str
    capacity: str
    active: bool
    permission_revoked: bool
    observed_at: datetime


@dataclass(frozen=True)
class CurrentScope:
    company_id: str
    approved_systems: tuple[str, ...]
    vendor_and_backup_disposition: str
    observed_at: datetime


@dataclass(frozen=True)
class ArchiveStatus:
    request_id: str
    company_id: str
    evidence_id: str
    digest_sha256: str
    independent_store: str
    durable: bool
    immutable: bool
    observed_at: datetime


class CaseSource(Protocol):
    async def current_case(self, request_id: str, company_id: str) -> CaseStatus: ...


class LegalSource(Protocol):
    async def current_retention(self, company_id: str) -> LegalStatus: ...
    async def current_hold(self, company_id: str) -> LegalStatus: ...


class WorkforceSource(Protocol):
    async def current_identity(
        self, person_id: str, company_id: str, capacity: str,
    ) -> WorkforceStatus: ...


class InventorySource(Protocol):
    async def current_scope(self, company_id: str) -> CurrentScope: ...


class IndependentArchiveSource(Protocol):
    async def readback(
        self, evidence_id: str, request_id: str, company_id: str,
    ) -> ArchiveStatus: ...


@dataclass(frozen=True)
class SourceBundle:
    # The five sources are **independent externally authenticated adapters**.
    # None is created in the ordinary API process by this module.
    cases: CaseSource
    legal: LegalSource
    workforce: WorkforceSource
    inventory: InventorySource
    archive: IndependentArchiveSource


def _blocked(reason: str) -> ReviewAssessment:
    return ReviewAssessment("BLOCKED", (reason,))


def _fresh(timestamp: object, *, now: datetime) -> bool:
    if (not isinstance(timestamp, datetime) or timestamp.tzinfo is None or
            timestamp.utcoffset() is None):
        return False
    age = now - timestamp.astimezone(timezone.utc)
    return timedelta(0) <= age <= _MAX_SOURCE_AGE


async def evaluate_current_source_review(
    packet: SignedReviewPacket, *,
    trusted_public_keys: Mapping[str, Mapping[str, bytes]],
    providers: SourceBundle | None,
    now: datetime,
) -> ReviewAssessment:
    """Require signed evidence AND fresh independent external rechecks.

    All adapters must be supplied by trusted infrastructure; missing/errored,
    stale, cross-tenant or disagreeing providers block. Providers are read-only
    by contract; no erasure executor, capability, migration or DB write exists.
    Returns only BLOCKED or REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED.
    """
    signed = evaluate_signed_review_packet(
        packet, trusted_public_keys=trusted_public_keys, now=now,
    )
    if signed.status != "REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED":
        return signed
    if providers is None or not isinstance(providers, SourceBundle):
        return _blocked("independent_providers_not_configured")
    try:
        if now.tzinfo is None or now.utcoffset() is None:
            return _blocked("clock_not_trusted")
        now = now.astimezone(timezone.utc)
        request = packet.request.record
        company_id, request_id = request.company_id, request.request_id

        async def get(coroutine):
            return await asyncio.wait_for(coroutine, timeout=_SOURCE_TIMEOUT_SECONDS)

        case = await get(providers.cases.current_case(request_id, company_id))
        if (not isinstance(case, CaseStatus) or
            case.request_id != request_id or case.company_id != company_id or
            case.requester_id != request.requester_id or case.state != "submitted" or
            not _fresh(case.observed_at, now=now)):
            return _blocked("case_current_state_unverified")

        retention = packet.retention.record
        hold = packet.legal_hold.record
        live_retention = await get(providers.legal.current_retention(company_id))
        live_hold = await get(providers.legal.current_hold(company_id))
        for expected, live in ((retention, live_retention), (hold, live_hold)):
            if (not isinstance(live, LegalStatus) or live.company_id != company_id or
                live.outcome != "clear" or expected.outcome != "clear" or
                set(live.jurisdictions) != set(expected.jurisdictions) or
                not live.jurisdictions or
                live.policy_reference != expected.policy_reference or
                not _fresh(live.observed_at, now=now)):
                return _blocked("legal_or_hold_current_state_unverified")
        if set(live_retention.jurisdictions) != set(live_hold.jurisdictions):
            return _blocked("retention_hold_jurisdiction_disagreement")

        # Recheck requester and both approvers from the *current* IdP, rather
        # than treating historical signed approvals as currently valid.
        requester = await get(providers.workforce.current_identity(
            request.requester_id, company_id, "requester"))
        if (not isinstance(requester, WorkforceStatus) or
            requester.company_id != company_id or
            requester.person_id != request.requester_id or
            requester.capacity != "requester" or not requester.active or
            requester.permission_revoked or
            not _fresh(requester.observed_at, now=now)):
            return _blocked("requester_current_identity_unverified")

        approvals = packet.approvals
        if not 2 <= len(approvals) <= _MAX_APPROVALS:
            return _blocked("approval_count_invalid")
        for signed_approval in approvals:
            approval = signed_approval.record
            live = await get(providers.workforce.current_identity(
                approval.approver_id, company_id, approval.capacity))
            if (not isinstance(live, WorkforceStatus) or
                live.company_id != company_id or
                live.person_id != approval.approver_id or
                live.capacity != approval.capacity or
                not live.active or live.permission_revoked or
                not _fresh(live.observed_at, now=now)):
                return _blocked("approver_current_identity_unverified")

        scope = packet.scope.record
        live_scope = await get(providers.inventory.current_scope(company_id))
        if (not isinstance(live_scope, CurrentScope) or
            live_scope.company_id != company_id or
            set(live_scope.approved_systems) != set(scope.approved_systems) or
            not live_scope.approved_systems or
            live_scope.vendor_and_backup_disposition != scope.vendor_and_backup_disposition or
            not _fresh(live_scope.observed_at, now=now)):
            return _blocked("vendor_backup_scope_unverified")

        receipt = packet.external_receipt.record
        archive = await get(providers.archive.readback(
            receipt.evidence_id, request_id, company_id))
        if (not isinstance(archive, ArchiveStatus) or
            archive.request_id != request_id or archive.company_id != company_id or
            archive.evidence_id != receipt.evidence_id or
            archive.digest_sha256 != evidence_commitment_digest(packet) or
            archive.independent_store != receipt.external_store or
            not archive.durable or not archive.immutable or
            not _fresh(archive.observed_at, now=now)):
            return _blocked("independent_archive_readback_unverified")

        # Never a grant to delete, even if all sources agree.
        return ReviewAssessment("REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED", ())
    except (Exception):
        # Provider outage, timeout, bad shape, malformed data: ALWAYS BLOCK.
        # Do not log source responses, identities, or secrets.
        return _blocked("independent_provider_unavailable")
