"""Explicit, employee-authorized Gmail 404 recovery coordinator.

Dormant backend-only method: no route, scheduler, webhook, production setup
or credential loader. The trusted caller MUST load a current employee-owned
OAuth connector and independently approved recovery record from private
backend storage. Do not accept an LLM/browser permission flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.intelligence.lola.authorized_provider_reads import (
    GmailBootstrapTransport, binding_for_resolved_case,
)
from app.intelligence.lola.email_sync_service import _identity_equal
from app.intelligence.lola.gmail_bootstrap import collect_gmail_bootstrap
from app.intelligence.lola.identity_registry import (
    ResolvedCrossChannelCase, resolve_verified_case,
)
from app.intelligence.lola.incremental_replay import (
    GmailHistoryExpired, replay_gmail_history,
)
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, StoredCheckpoint,
    _filters, read_checkpoint, scope_for_verified_case,
)
from app.intelligence.lola.replay_recovery import commit_confirmed_gmail_recovery


@dataclass(frozen=True)
class VerifiedRecoveryApproval:
    """Trusted backend approval registry record; NEVER construct from a request."""
    company_id: str
    employee_id: str
    provider_identity_sha256: str
    resource_sha256: str
    mapping_revision: str
    checkpoint_generation: int
    approved_at: datetime
    expires_at: datetime
    source: str = "employee_approved_backend_recovery_registry"


@dataclass(frozen=True)
class GmailRecoveryResult:
    state: str
    committed_generation: int | None = None
    recovery_epoch: int | None = None
    reply_status: str = "not_assessed"
    follow_up_eligible: bool = False
    cross_channel_case_ready: bool = False
    outbound_provider_writes_executed: bool = False
    external_messages_sent: bool = False


RecoveryApprovalLoader = Callable[
    [ResolvedCrossChannelCase, StoredCheckpoint], VerifiedRecoveryApproval
]


def _approval(
    *, resolved: ResolvedCrossChannelCase, prior: StoredCheckpoint,
    scope, vault: CursorVault, approval_loader: RecoveryApprovalLoader,
) -> None:
    if not callable(approval_loader):
        raise CheckpointUnavailable("Backend Gmail recovery approval loader required")
    try:
        record = approval_loader(resolved, prior)
    except Exception:
        raise CheckpointUnavailable("Employee Gmail recovery approval unavailable") from None
    now = datetime.now(timezone.utc)
    filters = _filters(scope, vault)
    if (not isinstance(record, VerifiedRecoveryApproval)
            or record.source != "employee_approved_backend_recovery_registry"
            or record.company_id != scope.company_id
            or record.employee_id != scope.employee_id
            or record.provider_identity_sha256 != filters["provider_identity_sha256"]
            or record.resource_sha256 != filters["resource_sha256"]
            or record.mapping_revision != scope.mapping_revision
            or type(record.checkpoint_generation) is not int
            or record.checkpoint_generation != prior.generation
            or not isinstance(record.approved_at, datetime)
            or not isinstance(record.expires_at, datetime)
            or record.approved_at.tzinfo is None
            or record.expires_at.tzinfo is None
            or record.approved_at > now
            or record.expires_at <= now
            or (record.expires_at-record.approved_at).total_seconds() > 1800):
        raise CheckpointUnavailable("No current scoped employee approval for Gmail recovery")


async def recover_verified_expired_gmail(
    *,
    user: dict,
    db,
    contact_id: str,
    work_item_id: str,
    verified_resource_id: str,
    verified_principal: str,
    connector,
    vault: CursorVault,
    approval_loader: RecoveryApprovalLoader,
) -> GmailRecoveryResult:
    """Check old cursor's verified 404, then full resync and approved CAS reset.

    Does NOT alter the normal Stage 11 sync path. Recovery requires explicit
    employee authorization in backend storage. A successful metadata sync
    still gives no proof of a customer reply's absence and sends nothing.
    """
    if not isinstance(vault, CursorVault):
        raise CheckpointUnavailable("Backend Gmail recovery encryption unavailable")
    now = datetime.now(timezone.utc)
    resolved = resolve_verified_case(
        user=user, db=db, contact_id=contact_id,
        work_item_id=work_item_id, as_of=now,
    )
    scope = scope_for_verified_case(
        resolved, provider="gmail", resource_id=verified_resource_id,
    )
    binding = binding_for_resolved_case(
        resolved, provider="gmail", resource_id=verified_resource_id,
        expected_principal=verified_principal,
    )
    prior = read_checkpoint(db=db, scope=scope, vault=vault)
    if prior is None:
        return GmailRecoveryResult("requires_initial_gmail_baseline")
    _approval(
        resolved=resolved, prior=prior, scope=scope,
        vault=vault, approval_loader=approval_loader,
    )

    def reauthorize() -> ResolvedCrossChannelCase:
        fresh = resolve_verified_case(
            user=user, db=db, contact_id=contact_id,
            work_item_id=work_item_id, as_of=datetime.now(timezone.utc),
        )
        if not _identity_equal(resolved, fresh):
            raise CheckpointUnavailable("Gmail recovery consent or identity changed")
        return fresh

    transport = GmailBootstrapTransport(
        binding=binding, connector=connector, reauthorize=reauthorize,
    )
    try:
        await replay_gmail_history(
            start_history_id=prior.plaintext_cursor,
            fetch_page=transport.fetch_page,
        )
    except GmailHistoryExpired as observed:
        expired = observed
    else:
        return GmailRecoveryResult("existing_gmail_history_still_valid")

    try:
        baseline = await collect_gmail_bootstrap(
            fetch_profile=transport.fetch_profile,
            fetch_messages_page=transport.fetch_messages_page,
            fetch_message_metadata=transport.fetch_message_metadata,
            fetch_history_page=transport.fetch_page,
        )
    except GmailHistoryExpired:
        return GmailRecoveryResult("fresh_gmail_history_also_expired")

    reauthorize()
    # Approval is checked a second time after all potentially long provider
    # requests. Stage 10 SQL checks BOTH consent grants again on the write.
    _approval(
        resolved=resolved, prior=prior, scope=scope,
        vault=vault, approval_loader=approval_loader,
    )
    committed = commit_confirmed_gmail_recovery(
        db=db, scope=scope, vault=vault, prior=prior,
        baseline=baseline, observed_expiry=expired,
        observed_at=datetime.now(timezone.utc),
    )
    return GmailRecoveryResult(
        "audited_gmail_baseline_recovered",
        committed_generation=committed.generation,
        recovery_epoch=committed.recovery_epoch,
    )
