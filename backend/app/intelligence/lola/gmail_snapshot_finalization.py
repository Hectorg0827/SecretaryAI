"""Stage 14B: dormant, employee-scoped Gmail baseline finalization coordinator.

No route, job, scheduler, token loader, outbound message or live provider read.
The trusted backend caller MUST resolve an authenticated employee-owned Gmail
case and supply a provider fetcher that verifies that OAuth principal on every
request. A checkpoint is only an incremental baseline, never no-reply proof.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.intelligence.lola.gmail_snapshot_staging import (
    SnapshotProgress, _anchor_digest,
)
from app.intelligence.lola.gmail_snapshot_reconciliation import (
    SnapshotReconciliationPreview, VerifiedSnapshotLedger,
    reconcile_gmail_snapshot_preview, verify_snapshot_ledger,
)
from app.intelligence.lola.incremental_replay import GmailFetch, _history_id
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, _filters, read_checkpoint,
)

_READ_RPC = "lola_read_gmail_snapshot_bundle"
_COMMIT_RPC = "lola_finalize_gmail_snapshot_baseline"
_RECEIPTS = "lola_gmail_snapshot_finalizations"


@dataclass(frozen=True)
class ScopedGmailBundle:
    progress: SnapshotProgress
    ledger: VerifiedSnapshotLedger
    pages: tuple[Mapping[str, object], ...] = field(repr=False)
    refs: tuple[Mapping[str, object], ...] = field(repr=False)


@dataclass(frozen=True)
class GmailBaselineFinalization:
    generation: int
    state: str = "encrypted_initial_baseline_committed"
    # Baseline cursor is durable, but no full history or cross-channel follow-up proof.
    durable_provider_checkpoint: bool = True
    baseline_only: bool = True
    complete_mailbox_history: bool = False
    reply_absence_proven: bool = False
    follow_up_eligible: bool = False
    outbound_action_approved: bool = False
    external_messages_sent: bool = False


def _one_result(data: Any) -> dict:
    item = data[0] if isinstance(data, list) and len(data) == 1 else data
    if not isinstance(item, dict):
        raise CheckpointUnavailable("Gmail snapshot bundle RPC returned invalid evidence")
    return item


def load_consistent_gmail_snapshot(
    *, db: Any, scope: MailboxScope, vault: CursorVault,
) -> ScopedGmailBundle:
    """One SQL RPC returns one consistent scoped job/pages/refs bundle.

    Unlike three independent REST table reads, the SQL RPC builds page and
    reference arrays in one MVCC statement and checks employee access under
    row locks. A completed source still needs independent proof validation.
    """
    filters = _filters(scope, vault)
    if not isinstance(vault, CursorVault) or scope.provider != "gmail":
        raise CheckpointUnavailable("Authenticated Gmail private scope required")
    args = {
        "p_company_id": scope.company_id,
        "p_employee_id": scope.employee_id,
        "p_provider_identity_sha256": filters["provider_identity_sha256"],
        "p_resource_sha256": filters["resource_sha256"],
        "p_mapping_revision": scope.mapping_revision,
    }
    try:
        raw = db.rpc(_READ_RPC, args).execute().data
    except Exception:
        raise CheckpointUnavailable("Employee-private Gmail snapshot reader unavailable") from None
    bundle = _one_result(raw)
    job, pages, refs = bundle.get("job"), bundle.get("pages"), bundle.get("refs")
    if (not isinstance(job, Mapping) or not isinstance(pages, list)
            or not isinstance(refs, list)
            or any(job.get(k) != v for k, v in filters.items())
            or job.get("mapping_revision") != scope.mapping_revision
            or job.get("state") != "snapshot_exhausted"
            or job.get("next_page_ciphertext") is not None
            or job.get("next_page_sha256") is not None):
        raise CheckpointUnavailable("Gmail snapshot bundle identity or state invalid")
    anchor = vault.decrypt(scope, job.get("initial_history_ciphertext"))
    _history_id(anchor)
    if job.get("initial_history_sha256") != _anchor_digest(scope, vault, anchor):
        raise CheckpointUnavailable("Gmail snapshot initial history anchor mismatch")
    progress = SnapshotProgress(
        pages_staged=job.get("pages_staged"),
        messages_staged=job.get("messages_staged"),
        initial_history_id=anchor,
        next_page_token=None,
        initial_history_sha256=job["initial_history_sha256"],
        next_page_sha256=None,
        page_chain_sha256=job.get("page_chain_sha256"),
        state="snapshot_exhausted",
    )
    proof = verify_snapshot_ledger(
        scope=scope, vault=vault, progress=progress,
        pages=pages, refs=refs,
    )
    return ScopedGmailBundle(progress=progress, ledger=proof,
                             pages=tuple(pages), refs=tuple(refs))


def _receipt_matches(
    *, db: Any, scope: MailboxScope, vault: CursorVault,
    snapshot: ScopedGmailBundle, preview: SnapshotReconciliationPreview,
    cursor_sha: str, replay_digest: str, generation: int,
) -> None:
    filters = _filters(scope, vault)
    fields = (
        "company_id,employee_id,provider,provider_identity_sha256,resource_sha256,"
        "mapping_revision,initial_history_sha256,snapshot_chain_sha256,"
        "snapshot_membership_sha256,history_digest_sha256,"
        "reconciled_membership_sha256,cursor_sha256,replay_digest,checkpoint_generation"
    )
    try:
        query = db.table(_RECEIPTS).select(fields)
        for key, value in filters.items():
            query = query.eq(key, value)
        rows = query.limit(2).execute().data
    except Exception:
        raise CheckpointUnavailable("Gmail baseline finalization receipt unreadable") from None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise CheckpointUnavailable("Gmail baseline finalization receipt missing")
    row = rows[0]
    expected = {
        **filters,
        "mapping_revision": scope.mapping_revision,
        "initial_history_sha256": snapshot.progress.initial_history_sha256,
        "snapshot_chain_sha256": snapshot.ledger.chain_digest,
        "snapshot_membership_sha256": snapshot.ledger.membership_digest,
        "history_digest_sha256": preview.history_digest,
        "reconciled_membership_sha256": preview.membership_digest,
        "cursor_sha256": cursor_sha,
        "replay_digest": replay_digest,
        "checkpoint_generation": generation,
    }
    if any(row.get(k) != v for k, v in expected.items()):
        raise CheckpointUnavailable("Gmail finalization receipt does not match source evidence")


async def finalize_verified_gmail_snapshot(
    *,
    db: Any, scope: MailboxScope, vault: CursorVault,
    fetch_history_page: GmailFetch,
    reauthorize: Callable[[], None],
    observed_at: datetime | None = None,
) -> GmailBaselineFinalization:
    """Read, replay, re-read, atomically commit, then verify. No send authority.

    The SQL writer requires an *empty* Gmail checkpoint for the scoped mailbox.
    Replayed identical calls return the prior generation. On an ambiguous RPC
    timeout, the caller must retry the same verified operation; it must not
    claim success without the authenticated receipt and checkpoint re-read.
    """
    if not callable(reauthorize) or not callable(fetch_history_page):
        raise CheckpointUnavailable("Authenticated Gmail replay transport required")
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise CheckpointUnavailable("Timezone-aware Gmail observation required")
    now = now.astimezone(timezone.utc)
    try:
        reauthorize()
    except Exception:
        raise CheckpointUnavailable("Gmail employee consent unavailable") from None
    snapshot = load_consistent_gmail_snapshot(db=db, scope=scope, vault=vault)
    preview = await reconcile_gmail_snapshot_preview(
        scope=scope, vault=vault, progress=snapshot.progress,
        pages=snapshot.pages, refs=snapshot.refs,
        fetch_history_page=fetch_history_page, reauthorize=reauthorize,
    )
    try:
        reauthorize()
    except Exception:
        raise CheckpointUnavailable("Gmail employee consent changed") from None
    # The ledger must remain exactly the same after a long provider read.
    current = load_consistent_gmail_snapshot(db=db, scope=scope, vault=vault)
    if (current.progress.initial_history_id != snapshot.progress.initial_history_id
            or current.progress.initial_history_sha256 != snapshot.progress.initial_history_sha256
            or current.progress.pages_staged != snapshot.progress.pages_staged
            or current.progress.messages_staged != snapshot.progress.messages_staged
            or current.ledger.chain_digest != snapshot.ledger.chain_digest
            or current.ledger.membership_digest != snapshot.ledger.membership_digest):
        raise CheckpointUnavailable("Gmail snapshot changed during provider replay")
    cursor_sha = vault.digest("cursor", preview.next_history_id)
    replay_digest = vault.digest(
        "gmail-stage14-baseline-v1", scope.company_id, scope.employee_id,
        scope.provider_identity_ref, scope.resource_id, scope.mapping_revision,
        snapshot.progress.initial_history_sha256,
        snapshot.ledger.chain_digest, snapshot.ledger.membership_digest,
        preview.history_digest, preview.membership_digest, preview.next_history_id,
    )
    args = {
        "p_company_id": scope.company_id,
        "p_employee_id": scope.employee_id,
        "p_provider_identity_sha256": _filters(scope, vault)["provider_identity_sha256"],
        "p_resource_sha256": _filters(scope, vault)["resource_sha256"],
        "p_mapping_revision": scope.mapping_revision,
        "p_initial_history_sha256": snapshot.progress.initial_history_sha256,
        "p_expected_pages": snapshot.progress.pages_staged,
        "p_expected_messages": snapshot.progress.messages_staged,
        "p_snapshot_chain_sha256": snapshot.ledger.chain_digest,
        "p_snapshot_membership_sha256": snapshot.ledger.membership_digest,
        "p_history_digest_sha256": preview.history_digest,
        "p_reconciled_membership_sha256": preview.membership_digest,
        "p_cursor_ciphertext": vault.encrypt(scope, preview.next_history_id),
        "p_cursor_sha256": cursor_sha,
        "p_replay_digest": replay_digest,
        "p_replayed_through": now.isoformat(),
        "p_events": list(preview.event_rows),
    }
    try:
        data = db.rpc(_COMMIT_RPC, args).execute().data
    except Exception:
        raise CheckpointUnavailable("Atomic Gmail baseline outcome unavailable; safe retry required") from None
    generation = data[0] if isinstance(data, list) and len(data) == 1 else data
    if type(generation) is not int or generation != 1:
        raise CheckpointUnavailable("Invalid Gmail baseline commit generation")
    stored = read_checkpoint(db=db, scope=scope, vault=vault)
    if (stored is None or stored.generation < generation
            or stored.recovery_epoch < 0
            or stored.baseline_only is not True):
        raise CheckpointUnavailable("Gmail baseline checkpoint unverified")
    if stored.generation == generation and (
        stored.plaintext_cursor != preview.next_history_id
        or stored.replay_digest != replay_digest
    ):
        raise CheckpointUnavailable("Gmail baseline checkpoint mismatch")
    _receipt_matches(
        db=db, scope=scope, vault=vault,
        snapshot=snapshot, preview=preview,
        cursor_sha=cursor_sha, replay_digest=replay_digest,
        generation=generation,
    )
    return GmailBaselineFinalization(generation=generation)
