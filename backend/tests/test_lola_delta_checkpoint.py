"""Atomic provider checkpoint acceptance with a synthetic, single-transaction store.

No real Supabase, OAuth credentials, provider calls, or external messages.
The storage contract is intentionally NOT treated as deployed functionality.
"""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.intelligence.lola.delta_checkpoint import (
    CheckpointConflict, CheckpointUnavailable, PersistedRound,
    persist_verified_round,
)
from test_lola_delta_replay import NOW, do_gmail, do_graph


class SyntheticAtomicStore:
    def __init__(self, prior=None):
        self.persisted = prior
        self.calls = []
        self.error = None
        self.accept = True
        self.tamper = None

    async def read_primary(self, scope):
        self.calls.append("read")
        if self.error == "read":
            raise RuntimeError("SECRET DATABASE ERROR BODY")
        return self.persisted

    async def commit_atomic(self, *, scope, expected_version, expected_cursor, round_candidate):
        self.calls.append("atomic_commit")
        if self.error == "commit":
            raise RuntimeError("SECRET PROVIDER CURSOR")
        if not self.accept:
            return False
        if self.persisted is not None and (
            self.persisted.scope != scope or
            self.persisted.version != expected_version or
            self.persisted.cursor != expected_cursor
        ):
            return False
        if self.persisted is None and (expected_version != 0 or expected_cursor != ""):
            return False
        self.persisted = PersistedRound(
            scope=scope, version=expected_version + 1,
            cursor=round_candidate.terminal_cursor,
            last_round_digest=round_candidate.source_digest,
            changes_applied=round_candidate.changes_processed,
            committed_at=round_candidate.observed_at,
            events_and_cursor_single_transaction=True,
            current_employee_authorization_checked=True,
        )
        if self.tamper:
            self.persisted = replace(self.persisted, **self.tamper)
        return True


def commit(candidate, store, *, now=NOW):
    return asyncio.run(persist_verified_round(
        candidate=candidate, store=store, now=now,
    ))


def prior_gmail(candidate):
    return PersistedRound(
        scope=candidate.scope, version=7, cursor=candidate.previous_cursor,
        last_round_digest="0"*64, changes_applied=0,
        committed_at=NOW-timedelta(minutes=5),
        events_and_cursor_single_transaction=True,
        current_employee_authorization_checked=True,
    )


def test_graph_initial_round_is_committed_as_one_atomic_batch_and_read_back():
    candidate, _ = do_graph()
    store = SyntheticAtomicStore()
    result = commit(candidate, store)
    assert store.calls == ["read", "atomic_commit", "read"]
    assert result.provider == "outlook"
    assert result.version == 1
    assert result.checkpoint_confirmed_in_store is True
    assert result.source_history_replay_complete is True
    assert result.cross_channel_case_verified is False
    assert result.no_reply_verified is False
    assert result.follow_up_allowed is False
    assert result.external_messages_sent is False
    assert result.changes_applied == candidate.changes_processed
    assert "graph-msg-" not in str(result)
    assert "opaque%3D" not in str(result)


def test_gmail_requires_previous_durable_history_cursor():
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore()
    with pytest.raises(CheckpointUnavailable):
        commit(candidate, store)
    assert store.calls == ["read"]


def test_gmail_commits_when_existing_cursor_matches():
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore(prior_gmail(candidate))
    result = commit(candidate, store)
    assert result.version == 8
    assert store.persisted.cursor == candidate.terminal_cursor
    assert result.changes_applied == 3
    assert store.calls == ["read", "atomic_commit", "read"]


def test_replay_of_a_stale_gmail_round_conflicts_not_deduplicated_into_success():
    candidate, _ = do_gmail()
    old = replace(prior_gmail(candidate), cursor="newer-cursor")
    store = SyntheticAtomicStore(old)
    with pytest.raises(CheckpointConflict):
        commit(candidate, store)
    assert store.calls == ["read"]


def test_same_graph_initial_round_cannot_be_committed_twice():
    candidate, _ = do_graph()
    store = SyntheticAtomicStore()
    commit(candidate, store)
    with pytest.raises(CheckpointConflict):
        commit(candidate, store)


def test_store_detects_concurrent_revision_update():
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore(prior_gmail(candidate))
    store.accept = False
    with pytest.raises(CheckpointConflict):
        commit(candidate, store)
    assert store.calls == ["read", "atomic_commit"]


def test_unauthorized_store_refuses_commit_and_no_success_claim():
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore(prior_gmail(candidate))
    store.accept = False
    with pytest.raises(CheckpointConflict):
        commit(candidate, store)


@pytest.mark.parametrize("failure", ["read", "commit"])
def test_failure_does_not_expose_sensitive_cursor_or_claim_persistence(failure):
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore(prior_gmail(candidate))
    store.error = failure
    with pytest.raises(CheckpointUnavailable) as err:
        commit(candidate, store)
    assert "SECRET" not in str(err.value)


@pytest.mark.parametrize("tamper", [
    {"version": 77},
    {"cursor": "unrelated"},
    {"last_round_digest": "f"*64},
    {"changes_applied": 0},
    {"events_and_cursor_single_transaction": False},
    {"current_employee_authorization_checked": False},
    {"committed_at": NOW+timedelta(minutes=1)},
])
def test_primary_readback_rejects_partial_write_or_forged_success(tamper):
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore(prior_gmail(candidate))
    store.tamper = tamper
    with pytest.raises(CheckpointUnavailable):
        commit(candidate, store)


def test_wrong_employee_scope_in_primary_record_is_rejected():
    candidate, _ = do_gmail()
    scope = replace(candidate.scope, employee_id="other-employee")
    store = SyntheticAtomicStore(replace(prior_gmail(candidate), scope=scope))
    with pytest.raises(CheckpointUnavailable):
        commit(candidate, store)
    assert "atomic_commit" not in store.calls


@pytest.mark.parametrize("field,value", [
    ("round_complete", False),
    ("gap_free_from_prior_cursor", False),
    ("checkpoint_persisted", True),
    ("mailbox_history_certified_for_followup", True),
    ("external_writes_executed", True),
    ("source_digest", "not-a-digest"),
    ("changes_processed", 1),
    ("pages_processed", 0),
])
def test_forged_or_partial_candidate_is_rejected_before_store_read(field, value):
    candidate, _ = do_gmail()
    forged = replace(candidate, **{field: value})
    store = SyntheticAtomicStore(prior_gmail(candidate))
    with pytest.raises(CheckpointUnavailable):
        commit(forged, store)
    assert store.calls == []


def test_outdated_candidate_rejected_before_database_write():
    candidate, _ = do_graph()
    store = SyntheticAtomicStore()
    with pytest.raises(CheckpointUnavailable):
        commit(candidate, store, now=NOW+timedelta(hours=1))
    assert store.calls == []


def test_future_candidate_rejected_before_database_write():
    candidate, _ = do_graph()
    store = SyntheticAtomicStore()
    with pytest.raises(CheckpointUnavailable):
        commit(candidate, store, now=NOW-timedelta(seconds=1))
    assert store.calls == []


def test_readback_after_commit_failure_is_not_silently_treated_as_no_changes():
    candidate, _ = do_gmail()
    store = SyntheticAtomicStore(prior_gmail(candidate))
    class FailureAfterCommit(SyntheticAtomicStore):
        async def read_primary(self, scope):
            if "atomic_commit" in self.calls:
                raise RuntimeError("REPLICA DELAY: SECRET")
            return await super().read_primary(scope)
    broken = FailureAfterCommit(prior_gmail(candidate))
    with pytest.raises(CheckpointUnavailable) as err:
        commit(candidate, broken)
    assert "SECRET" not in str(err.value)
    assert broken.calls == ["read", "atomic_commit"]
