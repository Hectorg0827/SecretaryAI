"""One-case backend-only incremental email ingestion orchestration.

No router, scheduler, notification or user-editable provider selector is
installed. A trusted server caller must load the employee's own OAuth connector
and independently verified principal/resource from private storage. A current
Stage 5 registry resolution occurs before reading and again before writing.
Insufficient evidence leaves the existing durable checkpoint unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

from app.intelligence.lola.authorized_provider_reads import (
    GmailHistoryTransport, GmailBootstrapTransport, GraphFolderDeltaTransport,
    GraphDeltaExpired, VerifiedEmailConnection, binding_for_resolved_case,
)
from app.intelligence.lola.identity_registry import (
    ResolvedCrossChannelCase, resolve_verified_case,
)
from app.intelligence.lola.incremental_replay import (
    GmailHistoryExpired, ReplayUnavailable,
    replay_gmail_history, replay_graph_folder_delta,
)
from app.intelligence.lola.gmail_bootstrap import collect_gmail_bootstrap
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault,
    commit_replay, read_checkpoint, scope_for_verified_case,
)


@dataclass(frozen=True)
class SyncResult:
    state: str
    provider: str
    committed_generation: int | None
    ingested_metadata_count: int
    # This ingest does NOT materialize contact mapping, reply direction or
    # complete cross-channel history. The follow-up engine is NOT enabled.
    cross_channel_case_ready: bool = False
    reply_status: str = "not_assessed"
    follow_up_eligible: bool = False
    external_messages_sent: bool = False
    outbound_provider_writes_executed: bool = False


def _identity_equal(a: ResolvedCrossChannelCase, b: ResolvedCrossChannelCase) -> bool:
    return (
        isinstance(b, ResolvedCrossChannelCase)
        and a.source == b.source == "backend_verified_registry"
        and a.scope == b.scope
        and a.email_provider_identity_ref == b.email_provider_identity_ref
        and a.whatsapp_provider_identity_ref == b.whatsapp_provider_identity_ref
        and a.email_contact_ref == b.email_contact_ref
        and a.whatsapp_contact_ref == b.whatsapp_contact_ref
        and a.source_fingerprint == b.source_fingerprint
    )


async def synchronize_verified_case_email(
    *,
    user: dict,
    db,
    contact_id: str,
    work_item_id: str,
    provider: str,
    verified_resource_id: str,
    verified_principal: str,
    connector,
    vault: CursorVault,
    http_transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> SyncResult:
    """Consumes one eligible employee-owned email provider via exact-case scope.

    The trusted caller must NOT source provider, connector, principal, folder
    or contact mappings from browser input. A service-role DB client by itself
    is NEVER authority. No new OAuth connection or consent is created here.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CheckpointUnavailable("Provider sync requires aware clock")
    now = now.astimezone(timezone.utc)
    if not isinstance(vault, CursorVault):
        raise CheckpointUnavailable("Backend cursor encryption unavailable")
    if provider not in ("gmail", "outlook"):
        raise CheckpointUnavailable("Unsupported mailbox provider")

    # Every invocation starts from an authenticated employee and CURRENT
    # private registry read, not a cached browser role or a provider ID.
    resolved = resolve_verified_case(
        user=user, db=db, contact_id=contact_id,
        work_item_id=work_item_id, as_of=now,
    )
    binding = binding_for_resolved_case(
        resolved, provider=provider, resource_id=verified_resource_id,
        expected_principal=verified_principal,
    )
    scope = scope_for_verified_case(
        resolved, provider=provider, resource_id=verified_resource_id,
    )
    checkpoint = read_checkpoint(db=db, scope=scope, vault=vault)

    def reauthorize() -> ResolvedCrossChannelCase:
        fresh = resolve_verified_case(
            user=user, db=db, contact_id=contact_id, work_item_id=work_item_id,
            as_of=datetime.now(timezone.utc),
        )
        if not _identity_equal(resolved, fresh):
            raise CheckpointUnavailable("Employee consent or provider mapping changed")
        return fresh

    if provider == "gmail":
        if checkpoint is None:
            fetcher = GmailBootstrapTransport(
                binding=binding, connector=connector, reauthorize=reauthorize,
            )
            try:
                result = await collect_gmail_bootstrap(
                    fetch_profile=fetcher.fetch_profile,
                    fetch_messages_page=fetcher.fetch_messages_page,
                    fetch_message_metadata=fetcher.fetch_message_metadata,
                    fetch_history_page=fetcher.fetch_page,
                )
            except GmailHistoryExpired:
                # The initial profile anchor expired while listing messages.
                # Do not persist any partial snapshot or fabricated cursor.
                return SyncResult("requires_full_gmail_bootstrap_retry", provider, None, 0)
        else:
            fetcher = GmailHistoryTransport(
                binding=binding, connector=connector, reauthorize=reauthorize,
            )
            try:
                result = await replay_gmail_history(
                    start_history_id=checkpoint.plaintext_cursor,
                    fetch_page=fetcher.fetch_page,
                )
            except GmailHistoryExpired:
                # Existing checkpoint must be reset by a separate verified
                # recovery operation. Never overwrite it with a guessed cursor.
                return SyncResult("requires_full_gmail_resync", provider, None, 0)
    else:
        fetcher = GraphFolderDeltaTransport(
            binding=binding, connector=connector, reauthorize=reauthorize,
            http_transport=http_transport,
        )
        try:
            result = await replay_graph_folder_delta(
                verified_folder_id=verified_resource_id,
                previous_delta_url=checkpoint.plaintext_cursor if checkpoint else None,
                fetch_url=fetcher.fetch_url,
            )
        except GraphDeltaExpired:
            # Explicitly signal folder baseline recovery; no cursor commit.
            return SyncResult("requires_graph_folder_resync", provider, None, 0)
    # Revocation/role/identity changes after last provider page invalidate the
    # entire uncommitted batch. The database is not written on such failures.
    reauthorize()
    committed = commit_replay(
        db=db, scope=scope, vault=vault, replay=result,
        previously_read=checkpoint, observed_at=datetime.now(timezone.utc),
    )
    return SyncResult(
        "baseline_recorded" if checkpoint is None
        else "incremental_metadata_recorded",
        provider, committed.generation, committed.stored_event_count,
    )
