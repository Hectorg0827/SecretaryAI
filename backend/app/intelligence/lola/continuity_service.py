"""Read-only integration gate for Lola's cross-channel continuity engine.

Trusted registry resolution always precedes connector reads. Only backend
adapter-supplied, complete, connection-matched provider snapshots reach the
continuity policy. No user-requested source proof, text similarity or
unverified provider message is promoted into an actionable follow-up.

This module does not register or contact providers itself. Until official
connectors and an authorized mapping store are installed, it fails closed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from app.intelligence.lola.continuity import (
    ActivityRecord, Channel, ChannelCoverage, ContinuityDecision,
    ContinuityState, evaluate_follow_up,
)
from app.intelligence.lola.identity_registry import (
    IdentityAccessDenied, IdentityNotVerified, IdentitySourceUnavailable,
    ResolvedCrossChannelCase, resolve_verified_case,
)

MAX_TOTAL_EVENTS = 500


@dataclass(frozen=True)
class VerifiedChannelSnapshot:
    channel: Channel
    provider_identity_ref: str
    mapping_revision: str
    coverage: ChannelCoverage
    events: tuple[ActivityRecord, ...]
    # Only privileged provider adapters may mint this type. Never accept
    # these flags from a browser or from an LLM-generated summary.
    source_kind: str
    connector_identity_verified: bool
    pagination_complete: bool
    checkpoint_persisted: bool


# An injected authorized backend provider reader accepts only the resolved
# identity context; it cannot be invoked with arbitrary browser-supplied
# tenant, employee, mailbox, or phone-account IDs.
TrustedReader = Callable[[ResolvedCrossChannelCase], Awaitable[VerifiedChannelSnapshot]]


def _unavailable(
    contact_id: str, work_item_id: str, reason: str,
) -> ContinuityDecision:
    return ContinuityDecision(
        state=ContinuityState.EVIDENCE_UNAVAILABLE,
        reason=reason, work_item_id=work_item_id, contact_id=contact_id,
        supporting_record_ids=(), evidence_fingerprint=None,
        follow_up_after=None,
    )


async def evaluate_verified_case(
    *,
    user: dict,
    db,
    contact_id: str,
    work_item_id: str,
    now: datetime,
    email_reader: TrustedReader,
    whatsapp_reader: TrustedReader,
    timeout_seconds: float = 6.0,
) -> ContinuityDecision:
    """Enforce resolver/connector boundary before Stage 4 follow-up policy.

    Requires a *server-authenticated* employee session; passing a privileged
    service-role db connection alone does not grant an identity scope. This
    seam is not a public endpoint and does not enqueue any action.
    """
    if not 0 < timeout_seconds <= 30:
        raise ValueError("Reader timeout must be bounded")
    try:
        resolved = resolve_verified_case(
            user=user, db=db, contact_id=contact_id,
            work_item_id=work_item_id, as_of=now,
        )
    except IdentityAccessDenied:
        raise
    except (IdentityNotVerified, IdentitySourceUnavailable, ValueError, TypeError):
        return _unavailable(contact_id, work_item_id, "Employee consent or identity evidence unavailable")

    try:
        results = await asyncio.wait_for(
            asyncio.gather(email_reader(resolved), whatsapp_reader(resolved)),
            timeout=timeout_seconds,
        )
    except Exception:
        return _unavailable(contact_id, work_item_id, "Authorized provider reads incomplete")

    expected = {
        Channel.EMAIL: resolved.email_provider_identity_ref,
        Channel.WHATSAPP: resolved.whatsapp_provider_identity_ref,
    }
    if (not isinstance(results, list) or len(results) != 2
            or any(not isinstance(item, VerifiedChannelSnapshot) for item in results)):
        return _unavailable(contact_id, work_item_id, "Provider snapshot malformed")

    by_channel = {x.channel: x for x in results}
    if len(by_channel) != 2 or set(by_channel) != set(expected):
        return _unavailable(contact_id, work_item_id, "Cross-channel provider identities ambiguous")

    total = 0
    for channel in expected:
        batch = by_channel[channel]
        if (batch.provider_identity_ref != expected[channel]
                or batch.mapping_revision != resolved.scope.mapping_revision
                or batch.source_kind != "authorized_backend_provider"
                or batch.connector_identity_verified is not True
                or batch.pagination_complete is not True
                or batch.checkpoint_persisted is not True):
            return _unavailable(contact_id, work_item_id, "Provider identity, pagination or checkpoint unverified")
        if (not isinstance(batch.coverage, ChannelCoverage)
                or batch.coverage.channel != channel
                or batch.coverage.company_id != resolved.scope.company_id
                or batch.coverage.employee_id != resolved.scope.employee_id
                or not isinstance(batch.events, tuple)):
            return _unavailable(contact_id, work_item_id, "Provider coverage scope mismatched")
        total += len(batch.events)
        if total > MAX_TOTAL_EVENTS:
            return _unavailable(contact_id, work_item_id, "Provider scope exceeds bounded review window")
        if any(
            not isinstance(ev, ActivityRecord)
            or ev.channel != channel
            or ev.company_id != resolved.scope.company_id
            or ev.employee_id != resolved.scope.employee_id
            for ev in batch.events
        ):
            return _unavailable(contact_id, work_item_id, "Cross-channel event scope mismatched")

    return evaluate_follow_up(
        resolved.scope,
        activities=by_channel[Channel.EMAIL].events + by_channel[Channel.WHATSAPP].events,
        coverage=(
            by_channel[Channel.EMAIL].coverage,
            by_channel[Channel.WHATSAPP].coverage,
        ),
        now=now,
    )
