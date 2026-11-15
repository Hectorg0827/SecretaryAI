"""Atomic cursor persistence contract for Lola provider change replay.

READ/WARNING: This is a backend-only interface, NOT an installed Supabase
migration or a production database writer. A trusted implementation must
store the complete event batch and the terminal change token in ONE atomic
transaction with an optimistic revision check and current employee-consent
revalidation. Never perform a plain multi-request upsert and claim success.

Until a server-side implementation is installed, this module cannot commit
a real provider checkpoint. Fake stores are used in the synthetic tests only.
All results remain non-actionable for customer follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from app.intelligence.lola.delta_replay import (
    MAX_CHANGES, MAX_PAGES, DeltaCandidate, DeltaUnavailable, Scope,
)


class CheckpointUnavailable(DeltaUnavailable):
    """Atomic persistence, authorization or replay evidence is unavailable."""


class CheckpointConflict(CheckpointUnavailable):
    """Concurrent cursor advancement, mapping change or a stale sync result."""


@dataclass(frozen=True)
class PersistedRound:
    """Result of a trusted re-read from the primary durable checkpoint store."""
    scope: Scope = field(repr=False)
    version: int
    cursor: str = field(repr=False)
    last_round_digest: str
    changes_applied: int
    committed_at: datetime
    events_and_cursor_single_transaction: bool
    current_employee_authorization_checked: bool
    source_coverage_start: datetime | None = None


@dataclass(frozen=True)
class CommitOutcome:
    provider: str
    version: int
    source_digest: str
    changes_applied: int
    observed_through: str
    checkpoint_confirmed_in_store: bool
    bounded_provider_round_persisted: bool
    cross_channel_case_verified: bool = False
    no_reply_verified: bool = False
    follow_up_allowed: bool = False
    external_messages_sent: bool = False


@runtime_checkable
class AtomicRoundStore(Protocol):
    """Privileged, tenant-scoped backend adapter—not an HTTP/browser payload.

    commit_atomic MUST:
      * recheck active employee consent, ownership and source connection
      * use one transaction with optimistic expected version + cursor
      * upsert/dedupe all change records in the same transaction
      * advance the provider cursor ONLY if all changes are persisted
      * return False when the previous version or cursor no longer matches
    """

    async def read_primary(self, scope: Scope) -> PersistedRound | None: ...

    async def commit_atomic(
        self, *,
        scope: Scope,
        expected_version: int,
        expected_cursor: str,
        round_candidate: DeltaCandidate,
    ) -> bool: ...


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CheckpointUnavailable("Checkpoint timestamp missing time zone")
    return value.astimezone(timezone.utc)


def _assert_record(record: PersistedRound, scope: Scope) -> None:
    if (not isinstance(record, PersistedRound) or record.scope != scope
            or type(record.version) is not int or record.version < 1
            or not isinstance(record.cursor, str) or not record.cursor
            or not isinstance(record.last_round_digest, str)
            or record.events_and_cursor_single_transaction is not True
            or record.current_employee_authorization_checked is not True):
        raise CheckpointUnavailable("Stored checkpoint scope or transaction proof invalid")
    if type(record.changes_applied) is not int or record.changes_applied < 0:
        raise CheckpointUnavailable("Stored change counter invalid")
    _utc(record.committed_at)
    if record.source_coverage_start is not None:
        _utc(record.source_coverage_start)


async def persist_verified_round(
    *,
    candidate: DeltaCandidate,
    store: AtomicRoundStore,
    now: datetime,
) -> CommitOutcome:
    """Commit one complete provider round, then read it back from primary.

    No authorization for customer follow-ups is ever issued here. A Graph
    round proves only ONE folder; a Gmail history round requires an earlier
    trusted baseline to assert full coverage.
    """
    check_at = _utc(now)
    if (not isinstance(candidate, DeltaCandidate)
            or candidate.round_complete is not True
            or candidate.gap_free_from_prior_cursor is not True
            or candidate.checkpoint_persisted is not False
            or candidate.mailbox_history_certified_for_followup is not False
            or candidate.external_writes_executed is not False
            or not isinstance(candidate.scope, Scope)
            or not isinstance(candidate.source_digest, str)
            or len(candidate.source_digest) != 64
            or any(c not in "0123456789abcdef" for c in candidate.source_digest)
            or type(candidate.pages_processed) is not int
            or not 1 <= candidate.pages_processed <= MAX_PAGES
            or type(candidate.changes_processed) is not int
            or candidate.changes_processed != len(candidate.changes)
            or candidate.changes_processed > MAX_CHANGES):
        raise CheckpointUnavailable("Provider candidate is incomplete or untrusted")
    observed_at = _utc(candidate.observed_at)
    if observed_at > check_at or check_at - observed_at > timedelta(minutes=10):
        raise CheckpointUnavailable("Provider evidence is stale or future dated")
    if candidate.scope.provider == "gmail" and not candidate.previous_cursor:
        raise CheckpointUnavailable("Gmail change replay requires a prior history baseline")
    if candidate.scope.provider == "outlook" and not candidate.terminal_cursor:
        raise CheckpointUnavailable("Graph round missing terminal delta link")

    try:
        before = await store.read_primary(candidate.scope)
    except Exception:
        raise CheckpointUnavailable("Trusted checkpoint store unavailable") from None

    if before is None:
        # Graph initial delta starts without a token. Gmail requires a
        # separately initialized, previously persisted source history ID.
        if candidate.scope.provider != "outlook" or candidate.previous_cursor:
            raise CheckpointUnavailable("Cannot replay without a trusted baseline")
        version = 0
        expected_cursor = ""
    else:
        _assert_record(before, candidate.scope)
        version = before.version
        expected_cursor = before.cursor
        if candidate.previous_cursor != expected_cursor:
            raise CheckpointConflict("Provider cursor changed during replay")
        if _utc(before.committed_at) > check_at:
            raise CheckpointUnavailable("Stored checkpoint is future dated")
    try:
        accepted = await store.commit_atomic(
            scope=candidate.scope, expected_version=version,
            expected_cursor=expected_cursor, round_candidate=candidate,
        )
    except Exception:
        # The commit may have applied before the exception: never claim
        # successful or safely retryable persistence without a read-back.
        raise CheckpointUnavailable("Checkpoint commit outcome ambiguous") from None
    if accepted is not True:
        raise CheckpointConflict("Provider checkpoint revision did not match")
    try:
        after = await store.read_primary(candidate.scope)
    except Exception:
        raise CheckpointUnavailable("Checkpoint read-back unavailable") from None
    _assert_record(after, candidate.scope)
    committed_at = _utc(after.committed_at)
    if (after.version != version + 1
            or after.cursor != candidate.terminal_cursor
            or after.last_round_digest != candidate.source_digest
            or after.changes_applied != candidate.changes_processed
            or committed_at < observed_at or committed_at > check_at):
        raise CheckpointUnavailable("Durable read-back did not match committed event batch")

    return CommitOutcome(
        provider=candidate.scope.provider,
        version=after.version,
        source_digest=after.last_round_digest,
        changes_applied=after.changes_applied,
        observed_through=observed_at.isoformat(),
        checkpoint_confirmed_in_store=True,
        bounded_provider_round_persisted=True,
    )
