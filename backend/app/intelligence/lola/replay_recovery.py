"""Atomic, audited Gmail history-404 reset; backend-only and fail-closed.

A verified provider 404 must be observed on the SAME employee-owned OAuth
mailbox and old durable cursor, and a fresh full metadata+history snapshot
must be exhausted. This module does not access the provider or approve itself.
It never deletes old events or treats a new baseline as proof of no reply.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.intelligence.lola.gmail_bootstrap import GmailBootstrapEvidence
from app.intelligence.lola.incremental_replay import GmailHistoryExpired
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, StoredCheckpoint,
    _filters, _utc, read_checkpoint,
)

_RPC = "lola_recover_expired_gmail_replay"
_AUDIT = "lola_provider_replay_recoveries"
_EVENTS = "lola_provider_replay_events"


@dataclass(frozen=True)
class RecoveryCommit:
    generation: int
    recovery_epoch: int
    source: str = "verified_gmail_full_resync_after_404"
    # A new baseline CANNOT prove historical correspondence or response absence.
    preserved_prior_audit_epoch: bool = True
    complete_mailbox_history: bool = False
    reply_absence_proven: bool = False
    cross_channel_mapping_verified: bool = False
    follow_up_eligible: bool = False
    outbound_action_approved: bool = False


def commit_confirmed_gmail_recovery(
    *,
    db: Any,
    scope: MailboxScope,
    vault: CursorVault,
    prior: StoredCheckpoint,
    baseline: GmailBootstrapEvidence,
    observed_expiry: GmailHistoryExpired,
    observed_at: datetime,
) -> RecoveryCommit:
    """Privileged DB writer called ONLY after a confirmed scoped Gmail 404.

    A server-side employee approval verifier and current registry resolution
    must run BEFORE this function; the Stage 10 SQL trigger reauthorizes again
    *inside* the atomic transaction while holding relevant consent row locks.
    """
    if (not isinstance(scope, MailboxScope) or scope.provider != "gmail"
            or not isinstance(vault, CursorVault)
            or not isinstance(prior, StoredCheckpoint)
            or not isinstance(baseline, GmailBootstrapEvidence)
            or not isinstance(observed_expiry, GmailHistoryExpired)):
        raise CheckpointUnavailable("Verified Gmail 404 recovery evidence required")
    now = _utc(observed_at)
    filters = _filters(scope, vault)
    if (prior.generation < 1 or prior.recovery_epoch < 0
            or not prior.plaintext_cursor.isascii()
            or not prior.plaintext_cursor.isdecimal()
            or int(prior.plaintext_cursor) <= 0
            or baseline.initial_baseline is not True
            or baseline.exhausted is not True
            or baseline.checkpoint_persisted is not False
            or baseline.complete_mailbox_history is not False
            or baseline.cross_channel_mapping_verified is not False
            or baseline.reply_absence_proven is not False
            or baseline.outbound_action_approved is not False):
        raise CheckpointUnavailable("Gmail recovery provenance invalid")
    cursor = baseline.next_history_id
    if (not isinstance(cursor, str) or not cursor.isascii()
            or not cursor.isdecimal() or int(cursor) <= int(prior.plaintext_cursor)):
        raise CheckpointUnavailable("Gmail recovery cursor must advance")
    if (not isinstance(baseline.digest, str) or len(baseline.digest) != 64
            or any(x not in "0123456789abcdef" for x in baseline.digest)
            or baseline.page_count < 1
            or baseline.message_count < 0 or baseline.message_count > 400
            or len(baseline.changed_records) > 500
            or now < prior.replayed_through):
        raise CheckpointUnavailable("Gmail recovery snapshot incomplete or stale")
    epoch = prior.recovery_epoch + 1
    if epoch > 9223372036854775807:
        raise CheckpointUnavailable("Gmail recovery epoch exhausted")
    events = []
    keys: set[str] = set()
    for index, event in enumerate(baseline.changed_records):
        if event.operation not in ("added", "deleted", "label_changed", "changed"):
            raise CheckpointUnavailable("Invalid Gmail recovery event")
        key = vault.digest(
            "recovery-event-v1", scope.company_id, scope.employee_id,
            scope.provider, scope.provider_identity_ref, scope.resource_id,
            str(epoch), baseline.digest, str(index), event.source_id, event.operation,
        )
        if key in keys:
            raise CheckpointUnavailable("Repeated Gmail recovery event key")
        keys.add(key)
        events.append({"key_sha256": key, "operation": event.operation})
    old_digest = vault.digest("cursor", prior.plaintext_cursor)
    new_digest = vault.digest("cursor", cursor)
    sealed = vault.encrypt(scope, cursor)
    args = {
        "p_company_id": scope.company_id,
        "p_employee_id": scope.employee_id,
        "p_provider_identity_sha256": filters["provider_identity_sha256"],
        "p_resource_sha256": filters["resource_sha256"],
        "p_mapping_revision": scope.mapping_revision,
        "p_expected_generation": prior.generation,
        "p_expected_cursor_sha256": old_digest,
        "p_cursor_ciphertext": sealed,
        "p_cursor_sha256": new_digest,
        "p_replay_digest": baseline.digest,
        "p_replayed_through": now.isoformat(),
        "p_initial_message_count": baseline.message_count,
        "p_events": events,
        "p_reason": "gmail_history_404",
    }
    try:
        response = db.rpc(_RPC, args).execute()
        raw_generation = response.data
        generation = (raw_generation[0] if isinstance(raw_generation, list)
                      and len(raw_generation) == 1 else raw_generation)
    except Exception:
        raise CheckpointUnavailable("Atomic Gmail recovery commit failed") from None
    if type(generation) is not int or generation != prior.generation + 1:
        raise CheckpointUnavailable("Gmail recovery RPC returned an invalid generation")
    stored = read_checkpoint(db=db, scope=scope, vault=vault)
    if (stored is None or stored.generation != generation
            or stored.recovery_epoch != epoch
            or stored.plaintext_cursor != cursor
            or stored.replay_digest != baseline.digest
            or stored.baseline_only is not True):
        raise CheckpointUnavailable("Committed Gmail reset checkpoint did not verify")

    try:
        query = db.table(_AUDIT).select(
            "company_id,employee_id,provider,provider_identity_sha256,"
            "resource_sha256,mapping_revision,old_generation,new_generation,"
            "old_epoch,new_epoch,old_cursor_sha256,new_cursor_sha256,"
            "old_replay_digest,new_replay_digest,reason,"
            "initial_message_count,new_event_count"
        )
        for column, value in filters.items():
            query = query.eq(column, value)
        data = query.eq("new_epoch", epoch).limit(2).execute().data
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError("Expected one recovery audit record")
        audit = data[0]
        expected = {
            **filters,
            "mapping_revision": scope.mapping_revision,
            "old_generation": prior.generation,
            "new_generation": generation,
            "old_epoch": prior.recovery_epoch,
            "new_epoch": epoch,
            "old_cursor_sha256": old_digest,
            "new_cursor_sha256": new_digest,
            "old_replay_digest": prior.replay_digest,
            "new_replay_digest": baseline.digest,
            "reason": "gmail_history_404",
            "initial_message_count": baseline.message_count,
            "new_event_count": len(events),
        }
        if (not isinstance(audit, dict)
                or any(audit.get(k) != v for k, v in expected.items())):
            raise ValueError("Recovery audit evidence mismatched")
        query = db.table(_EVENTS).select("event_key_sha256,recovery_epoch,replay_digest")
        for column, value in filters.items():
            query = query.eq(column, value)
        saved = (query.eq("recovery_epoch", epoch)
                 .eq("replay_digest", baseline.digest)
                 .limit(501).execute().data)
        if (not isinstance(saved, list) or len(saved) != len(keys)
                or {row.get("event_key_sha256") for row in saved
                    if isinstance(row, dict)} != keys):
            raise ValueError("Recovery event epoch verification failed")
    except Exception:
        raise CheckpointUnavailable("Committed Gmail recovery audit verification failed") from None

    return RecoveryCommit(generation=generation, recovery_epoch=epoch)
