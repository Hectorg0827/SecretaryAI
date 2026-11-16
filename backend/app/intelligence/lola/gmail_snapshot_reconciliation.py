"""Stage 14: read-only proof of a paged Gmail snapshot and anchored history.

This module is intentionally NOT a checkpoint writer. It consumes only
server-authorized, employee-scoped Stage 13 ledger rows and a trusted
reauthorizing Gmail history fetcher. Passing a proof does not certify missing
replies, work-item linkage, or permission to contact anyone.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from app.intelligence.lola.gmail_snapshot_staging import (
    SnapshotProgress, _anchor_digest,
)
from app.intelligence.lola.incremental_replay import (
    GmailFetch, ReplayUnavailable, _history_id, replay_gmail_history,
)
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, _filters,
)

_HEX = frozenset("0123456789abcdef")


def _digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _HEX for c in value):
        raise CheckpointUnavailable("Malformed Gmail snapshot source digest")
    return value


def _chain(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def _scoped(row: Mapping[str, object], scope_fields: Mapping[str, object]) -> None:
    if not isinstance(row, Mapping) or any(row.get(k) != v for k, v in scope_fields.items()):
        raise CheckpointUnavailable("Gmail snapshot source row outside verified scope")


@dataclass(frozen=True)
class VerifiedSnapshotLedger:
    pages: int
    messages: int
    chain_digest: str
    membership_digest: str
    message_keys: frozenset[str] = field(repr=False)
    # This is a source integrity proof, not a provider checkpoint.
    complete_mailbox_history: bool = False
    provider_checkpoint_committed: bool = False
    reply_absence_proven: bool = False
    follow_up_eligible: bool = False


@dataclass(frozen=True)
class SnapshotReconciliationPreview:
    message_count: int
    history_record_count: int
    snapshot_digest: str
    history_digest: str
    membership_digest: str
    initial_history_id: str = field(repr=False)
    next_history_id: str = field(repr=False)
    state: str = "reconciled_preview_not_committed"
    durable_provider_checkpoint: bool = False
    complete_mailbox_history: bool = False
    reply_absence_proven: bool = False
    cross_channel_mapping_verified: bool = False
    follow_up_eligible: bool = False
    outbound_action_approved: bool = False
    event_rows: tuple[dict[str, str], ...] = field(default=(), repr=False)


def verify_snapshot_ledger(
    *,
    scope: MailboxScope,
    vault: CursorVault,
    progress: SnapshotProgress,
    pages: Sequence[Mapping[str, object]],
    refs: Sequence[Mapping[str, object]],
) -> VerifiedSnapshotLedger:
    """Require ALL scoped receipts and ALL message references, not a sampled page.

    The trusted caller must read a consistent, complete transactionally scoped
    set. This function rejects any mismatched counts, scope, order, HMAC,
    duplicate reference, missing receipt, or broken Stage 13 hash chain.
    It does not claim that the provider's historic messages were complete.
    """
    if not isinstance(vault, CursorVault) or scope.provider != "gmail":
        raise CheckpointUnavailable("Verified Gmail snapshot scope required")
    fields = _filters(scope, vault)
    if (not isinstance(progress, SnapshotProgress)
            or progress.state != "snapshot_exhausted"
            or progress.next_page_token is not None
            or progress.history_reconciled is not False
            or progress.durable_provider_checkpoint is not False
            or progress.reply_absence_proven is not False):
        raise CheckpointUnavailable("Only an uncommitted exhausted snapshot can be reconciled")
    if (type(progress.pages_staged) is not int or not 1 <= progress.pages_staged <= 500
            or type(progress.messages_staged) is not int
            or not 0 <= progress.messages_staged <= 20000
            or not isinstance(pages, (tuple, list))
            or not isinstance(refs, (tuple, list))
            or len(pages) != progress.pages_staged
            or len(refs) != progress.messages_staged):
        raise CheckpointUnavailable("Gmail snapshot ledger coverage incomplete")
    _history_id(progress.initial_history_id)
    if _digest(progress.initial_history_sha256) != _anchor_digest(
        scope, vault, progress.initial_history_id
    ):
        raise CheckpointUnavailable("Snapshot anchor integrity failed")
    _digest(progress.page_chain_sha256)

    receipts: dict[int, Mapping[str, object]] = {}
    for row in pages:
        _scoped(row, fields)
        index, count = row.get("page_index"), row.get("entry_count")
        if (type(index) is not int or not 0 <= index < progress.pages_staged
                or index in receipts or type(count) is not int or not 0 <= count <= 100):
            raise CheckpointUnavailable("Gmail snapshot page receipt invalid")
        _digest(row.get("page_digest_sha256"))
        _digest(row.get("chain_digest_sha256"))
        receipts[index] = row

    chain = ""
    total = 0
    for index in range(progress.pages_staged):
        row = receipts.get(index)
        if row is None:
            raise CheckpointUnavailable("Missing Gmail snapshot page receipt")
        if index == 0:
            chain = _chain(
                "lola-gmail-snapshot-chain-v1",
                progress.initial_history_sha256,
                row["page_digest_sha256"],
            )
        else:
            chain = _chain(chain, row["page_digest_sha256"])
        if chain != row["chain_digest_sha256"]:
            raise CheckpointUnavailable("Broken Gmail snapshot receipt chain")
        total += row["entry_count"]
    if chain != progress.page_chain_sha256 or total != progress.messages_staged:
        raise CheckpointUnavailable("Snapshot chain or message totals inconsistent")

    per_page = [0] * progress.pages_staged
    keys: set[str] = set()
    for row in refs:
        _scoped(row, fields)
        index = row.get("page_index")
        if type(index) is not int or not 0 <= index < progress.pages_staged:
            raise CheckpointUnavailable("Snapshot message refers to missing page")
        key = _digest(row.get("message_key_sha256"))
        if key in keys:
            raise CheckpointUnavailable("Duplicate Gmail snapshot message reference")
        keys.add(key)
        if row.get("thread_key_sha256") is not None:
            _digest(row.get("thread_key_sha256"))
        _digest(row.get("metadata_history_key_sha256"))
        per_page[index] += 1
    if any(per_page[i] != receipts[i]["entry_count"] for i in range(progress.pages_staged)):
        raise CheckpointUnavailable("Gmail snapshot message/page count disagreement")
    membership = hashlib.sha256("".join(sorted(keys)).encode("ascii")).hexdigest()
    return VerifiedSnapshotLedger(
        pages=progress.pages_staged, messages=progress.messages_staged,
        chain_digest=chain, membership_digest=membership,
        message_keys=frozenset(keys),
    )


async def reconcile_gmail_snapshot_preview(
    *,
    scope: MailboxScope,
    vault: CursorVault,
    progress: SnapshotProgress,
    pages: Sequence[Mapping[str, object]],
    refs: Sequence[Mapping[str, object]],
    fetch_history_page: GmailFetch,
    reauthorize: Callable[[], None],
) -> SnapshotReconciliationPreview:
    """Replay from the ORIGINAL snapshot anchor; never advance a database cursor.

    reauthorize and fetch_history_page MUST be backend-owned, and the trusted
    transport MUST independently reauthorize before/after each Gmail page.
    A history 404, incomplete page walk or revoked grant aborts this preview.
    """
    if not callable(reauthorize) or not callable(fetch_history_page):
        raise CheckpointUnavailable("Authenticated Gmail history readers required")
    try:
        reauthorize()
    except Exception:
        raise CheckpointUnavailable("Gmail snapshot authorization unavailable") from None
    proven = verify_snapshot_ledger(
        scope=scope, vault=vault, progress=progress, pages=pages, refs=refs
    )
    async def authorized_page(start: str, cursor: str | None):
        # Do not trust a fetcher to perform its own authorization checks.
        try:
            reauthorize()
        except Exception:
            raise CheckpointUnavailable("Gmail history authorization unavailable") from None
        response = await fetch_history_page(start, cursor)
        try:
            reauthorize()
        except Exception:
            raise CheckpointUnavailable("Gmail history authorization changed") from None
        return response

    replay = await replay_gmail_history(
        start_history_id=progress.initial_history_id,
        fetch_page=authorized_page,
    )
    try:
        reauthorize()
    except Exception:
        raise CheckpointUnavailable("Gmail snapshot authorization changed") from None
    if (replay.exhausted is not True or replay.checkpoint_persisted is not False
            or replay.reply_absence_proven is not False
            or int(_history_id(replay.next_history_id))
            < int(_history_id(progress.initial_history_id))):
        raise ReplayUnavailable("Gmail snapshot history replay not trustworthy")
    _digest(replay.digest)

    members = set(proven.message_keys)
    prefix = (
        scope.company_id, scope.employee_id,
        scope.provider_identity_ref, scope.resource_id,
    )
    transitions: dict[str, str] = {}
    event_rows: list[dict[str, str]] = []
    for index, event in enumerate(replay.changed_records):
        key = vault.digest("snapshot-message-v1", *prefix, event.source_id)
        event_rows.append({
            "key_sha256": vault.digest(
                "gmail-stage14-event-v1", *prefix, replay.digest,
                str(index), event.source_id, event.operation,
            ),
            "operation": event.operation,
        })
        if event.operation in ("added", "deleted"):
            previous = transitions.get(key)
            # The replay API flattens operations within a history record.
            # Conflicting transitions cannot be ordered safely without the
            # original event grouping, so do not infer final membership.
            if previous is not None and previous != event.operation:
                raise ReplayUnavailable("Ambiguous Gmail message transitions")
            transitions[key] = event.operation
        if event.operation == "added":
            members.add(key)
        elif event.operation == "deleted":
            members.discard(key)
        elif event.operation != "label_changed":
            raise ReplayUnavailable("Unrecognized Gmail history operation")
    membership = hashlib.sha256("".join(sorted(members)).encode("ascii")).hexdigest()
    return SnapshotReconciliationPreview(
        message_count=len(members),
        history_record_count=replay.record_count,
        snapshot_digest=proven.chain_digest,
        history_digest=replay.digest,
        membership_digest=membership,
        initial_history_id=progress.initial_history_id,
        next_history_id=replay.next_history_id,
        event_rows=tuple(event_rows),
    )
