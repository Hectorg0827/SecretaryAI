"""Stage 9: strict Supabase RPC checkpoint adapter. All data synthetic.

No hosted database mutation, network, real account/employee, token, or message.
The expected database RPCs are NOT deployed by these tests.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.intelligence.lola.delta_checkpoint import (
    CheckpointUnavailable, CheckpointConflict, persist_verified_round,
)
from app.intelligence.lola.supabase_delta_store import (
    RPC_READ, RPC_COMMIT, SupabaseAtomicRoundStore,
)
from test_lola_delta_replay import NOW, GMAIL_SCOPE, GRAPH_SCOPE, do_gmail, do_graph

ENCRYPTION_KEY = "synthetic-staging-checkpoint-encryption-secret-at-least-24"
HMAC_KEY = b"synthetic-staging-opaque-event-hmac-key-at-least-32-bytes"
USER = {"sub": "employee-X", "company_id": "tenant-X", "role": "owner"}


class Query:
    def __init__(self, db):
        self.db = db
        self.filters = []
    def select(self, columns):
        self.db.calls.append(("users_select", columns))
        assert columns == "id,company_id,role,is_active"
        return self
    def eq(self, key, value):
        self.filters.append((key, value))
        return self
    def limit(self, n):
        assert n == 2
        return self
    def execute(self):
        self.db.calls.append(("users_execute", tuple(self.filters)))
        if self.db.membership_fail:
            raise RuntimeError("SECRET user membership credentials")
        row = self.db.user_row
        rows = [row] if all(str(row.get(field)) == str(value)
                             for field, value in self.filters) else []
        return SimpleNamespace(data=rows)


class Rpc:
    def __init__(self, db, name, data):
        self.db, self.name, self.payload = db, name, data
    def execute(self):
        self.db.calls.append((self.name, self.payload))
        if self.db.rpc_failure:
            raise RuntimeError("PROVIDER SECRET TOKEN / database service error")
        if self.name == RPC_READ:
            return SimpleNamespace(data=self.db.persisted)
        if self.name != RPC_COMMIT:
            raise RuntimeError("Unexpected RPC")
        if self.db.force_conflict:
            return SimpleNamespace(data={"committed": False})
        if self.db.force_bad_reply:
            return SimpleNamespace(data={"ok": True})
        p = self.payload
        old = self.db.persisted
        expected_version = 0 if old is None else old["version"]
        if p["p_expected_version"] != expected_version:
            return SimpleNamespace(data={"committed": False})
        if old is not None and p["p_expected_cursor_digest"] != old["cursor_digest"]:
            return SimpleNamespace(data={"committed": False})
        if old is None and p["p_expected_cursor_digest"] is not None:
            return SimpleNamespace(data={"committed": False})
        self.db.persisted = {
            "company_id": p["p_company_id"],
            "employee_id": p["p_employee_id"],
            "provider": p["p_provider"],
            "provider_connection_ref": p["p_provider_connection_ref"],
            "provider_identity_ref": p["p_provider_identity_ref"],
            "mailbox_ref": p["p_mailbox_ref"],
            "folder_id": p["p_folder_id"],
            "mapping_revision": p["p_mapping_revision"],
            "cursor_ciphertext": p["p_terminal_cursor_ciphertext"],
            "cursor_digest": p["p_terminal_cursor_digest"],
            "version": expected_version + 1,
            "last_round_digest": p["p_last_round_digest"],
            "changes_applied": len(p["p_changes"]),
            "committed_at": self.db.commit_at.isoformat(),
            "source_coverage_start": None,
            "events_and_cursor_single_transaction": True,
            "current_employee_authorization_checked": True,
        }
        if self.db.tamper:
            self.db.persisted.update(self.db.tamper)
        return SimpleNamespace(data={"committed": True})


class Db:
    def __init__(self):
        self.calls = []
        self.persisted = None
        self.user_row = {
            "id": "employee-X", "company_id": "tenant-X",
            "role": "owner", "is_active": True,
        }
        self.membership_fail = False
        self.rpc_failure = False
        self.force_conflict = False
        self.force_bad_reply = False
        self.tamper = None
        self.commit_at = NOW
    def table(self, name):
        assert name == "users", "No direct table access except current membership"
        return Query(self)
    def rpc(self, name, data):
        assert name in (RPC_READ, RPC_COMMIT)
        return Rpc(self, name, data)


def make(db=None, *, user=USER, authorized=None):
    db = db if db is not None else Db()
    attempts = []
    async def current(scope):
        attempts.append(scope)
        if callable(authorized):
            return authorized(scope)
        return authorized is not False
    return SupabaseAtomicRoundStore(
        db=db, user=user, current_authorization=current,
        encryption_key=ENCRYPTION_KEY, hmac_key=HMAC_KEY,
    ), db, attempts


def read(store, scope=GRAPH_SCOPE):
    return asyncio.run(store.read_primary(scope))


def commit(store, candidate):
    return asyncio.run(persist_verified_round(
        candidate=candidate, store=store, now=NOW,
    ))


def test_graph_empty_store_and_atomic_first_round_receipt():
    store, db, verified = make()
    candidate, _ = do_graph()
    assert read(store) is None
    result = commit(store, candidate)
    assert result.provider == "outlook"
    assert result.version == 1
    assert result.bounded_provider_round_persisted is True
    assert result.checkpoint_confirmed_in_store is True
    assert result.cross_channel_case_verified is False
    assert result.no_reply_verified is False
    assert result.follow_up_allowed is False
    assert result.external_messages_sent is False
    assert len(verified) == 4  # pre-read, pre-commit, primary readback + earlier read
    assert [call[0] for call in db.calls if call[0] in (RPC_READ, RPC_COMMIT)] == [
        RPC_READ, RPC_READ, RPC_COMMIT, RPC_READ,
    ]
    persisted = read(store)
    assert persisted.cursor == candidate.terminal_cursor
    assert persisted.version == 1


def test_gmail_initial_history_requires_an_existing_persisted_baseline():
    store, db, verified = make()
    candidate, _ = do_gmail()
    with pytest.raises(CheckpointUnavailable):
        commit(store, candidate)
    assert not any(call[0] == RPC_COMMIT for call in db.calls)


def test_rpc_writes_only_keyed_opaque_refs_and_a_ciphertext_cursor():
    store, db, _ = make()
    candidate, _ = do_graph()
    commit(store, candidate)
    writes = [call[1] for call in db.calls if call[0] == RPC_COMMIT]
    assert len(writes) == 1
    params = writes[0]
    assert params["p_terminal_cursor_ciphertext"] != candidate.terminal_cursor
    assert params["p_expected_cursor_digest"] is None
    assert params["p_last_round_digest"] == candidate.source_digest
    assert len(params["p_changes"]) == 3
    assert all(set(item) == {"event_ref", "kind", "thread_ref"} for item in params["p_changes"])
    assert all(len(item["event_ref"]) == 64 for item in params["p_changes"])
    serialized = str(params)
    for private in (
        "graph-msg-1", "graph-msg-2", "graph-msg-3",
        "graph-conv-A", "graph-conv-B",
        "Bearer", "private@example.com", candidate.terminal_cursor,
    ):
        assert private not in serialized


def test_existing_encrypted_cursor_integrity_checked_before_return():
    store, db, _ = make()
    candidate, _ = do_graph()
    commit(store, candidate)
    db.persisted["cursor_digest"] = "0"*64
    with pytest.raises(CheckpointUnavailable):
        read(store)


def test_encrypted_cursor_tamper_or_wrong_decryption_key_fails_closed():
    store, db, _ = make()
    candidate, _ = do_graph()
    commit(store, candidate)
    db.persisted["cursor_ciphertext"] = "fake-ciphertext"
    with pytest.raises(CheckpointUnavailable):
        read(store)


def test_changed_tenant_cannot_read_primary_or_write_provider_events():
    store, db, _ = make()
    other = replace(GRAPH_SCOPE, company_id="other-tenant")
    with pytest.raises(CheckpointUnavailable):
        read(store, other)
    assert not any(call[0] in (RPC_READ, RPC_COMMIT) for call in db.calls)


def test_changed_employee_cannot_read_primary_provider_cursor():
    store, db, _ = make()
    other = replace(GRAPH_SCOPE, employee_id="other-employee")
    with pytest.raises(CheckpointUnavailable):
        read(store, other)
    assert not any(call[0] in (RPC_READ, RPC_COMMIT) for call in db.calls)


def test_inactive_or_changed_role_fails_before_rpc():
    db = Db()
    db.user_row["is_active"] = False
    store, _, _ = make(db)
    with pytest.raises(CheckpointUnavailable):
        read(store)
    assert not any(call[0] in (RPC_READ, RPC_COMMIT) for call in db.calls)


def test_current_provider_consent_revocation_blocks_database_use():
    store, db, _ = make(authorized=False)
    with pytest.raises(CheckpointUnavailable):
        read(store)
    assert not any(call[0] in (RPC_READ, RPC_COMMIT) for call in db.calls)


def test_provider_revoked_after_first_read_prevents_atomic_commit():
    counter = [0]
    def grant(_scope):
        counter[0] += 1
        return counter[0] == 1
    store, db, _ = make(authorized=grant)
    candidate, _ = do_graph()
    with pytest.raises(CheckpointUnavailable):
        commit(store, candidate)
    assert not any(call[0] == RPC_COMMIT for call in db.calls)


def test_revoked_before_primary_readback_does_not_claim_success():
    counter = [0]
    def grant(_scope):
        counter[0] += 1
        return counter[0] <= 2
    store, db, _ = make(authorized=grant)
    candidate, _ = do_graph()
    with pytest.raises(CheckpointUnavailable):
        commit(store, candidate)
    assert any(call[0] == RPC_COMMIT for call in db.calls)
    assert not any(call[0] == "followup_send" for call in db.calls)


def test_second_graph_initial_round_is_conflict_not_success():
    store, db, _ = make()
    candidate, _ = do_graph()
    commit(store, candidate)
    with pytest.raises(CheckpointConflict):
        commit(store, candidate)


def test_uninstalled_rpc_or_database_failure_never_becomes_empty_coverage():
    store, db, _ = make()
    db.rpc_failure = True
    with pytest.raises(CheckpointUnavailable) as exc:
        read(store)
    assert "SECRET" not in str(exc.value)
    assert "TOKEN" not in str(exc.value)


def test_ambiguous_commit_response_never_claims_persistence():
    store, db, _ = make()
    db.force_bad_reply = True
    with pytest.raises(CheckpointUnavailable):
        commit(store, do_graph()[0])


def test_concurrent_cas_conflict_leaves_cursor_unverified():
    store, db, _ = make()
    db.force_conflict = True
    with pytest.raises(CheckpointConflict):
        commit(store, do_graph()[0])


@pytest.mark.parametrize("tamper", [
    {"version": 7},
    {"mapping_revision": "another-mapping"},
    {"provider_identity_ref": "other-account"},
    {"last_round_digest": "0"*64},
    {"changes_applied": 0},
    {"events_and_cursor_single_transaction": False},
    {"current_employee_authorization_checked": False},
])
def test_partial_or_inconsistent_primary_readback_fail_closed(tamper):
    store, db, _ = make()
    db.tamper = tamper
    with pytest.raises(CheckpointUnavailable):
        commit(store, do_graph()[0])


def test_bounded_one_second_database_commit_lag_is_accepted():
    store, db, _ = make()
    db.commit_at = NOW + timedelta(seconds=1)
    result = commit(store, do_graph()[0])
    assert result.checkpoint_confirmed_in_store is True


def test_31_second_database_commit_future_timestamp_is_rejected():
    store, db, _ = make()
    db.commit_at = NOW + timedelta(seconds=31)
    with pytest.raises(CheckpointUnavailable):
        commit(store, do_graph()[0])


def test_scope_mismatch_forged_candidate_blocked_before_rpc_write():
    store, db, _ = make()
    candidate, _ = do_graph()
    swapped = replace(candidate.scope, employee_id="other-employee")
    with pytest.raises(CheckpointUnavailable):
        asyncio.run(store.commit_atomic(
            scope=swapped, expected_version=0, expected_cursor="",
            round_candidate=candidate,
        ))
    assert not any(call[0] == RPC_COMMIT for call in db.calls)


def test_bad_key_configuration_rejected():
    db = Db()
    async def auth(_scope):
        return True
    with pytest.raises(ValueError):
        SupabaseAtomicRoundStore(
            db=db, user=USER, current_authorization=auth,
            encryption_key="short", hmac_key=b"short",
        )



def test_gmail_event_identity_stable_across_page_walk_fingerprint_changes():
    store, _db, _ = make()
    baseline, _ = do_gmail()
    changed_page_digest = replace(baseline, source_digest="f"*64)
    first = store._event_ref(GMAIL_SCOPE, baseline, 0, baseline.changes[0])
    second = store._event_ref(GMAIL_SCOPE, changed_page_digest, 1, baseline.changes[0])
    assert first["event_ref"] == second["event_ref"]


def test_graph_event_identity_scoped_to_previous_delta_cursor_not_page_order():
    store, _db, _ = make()
    baseline, _ = do_graph()
    changed_page_digest = replace(baseline, source_digest="f"*64)
    first = store._event_ref(GRAPH_SCOPE, baseline, 0, baseline.changes[0])
    repeated = store._event_ref(GRAPH_SCOPE, changed_page_digest, 999, baseline.changes[0])
    assert first["event_ref"] == repeated["event_ref"]
    later_round = replace(baseline, previous_cursor="new-opaque-round-cursor")
    later = store._event_ref(GRAPH_SCOPE, later_round, 0, baseline.changes[0])
    assert later["event_ref"] != first["event_ref"]


def test_repeated_provider_change_does_not_advance_cursor():
    store, db, _ = make()
    candidate, _ = do_graph()
    duplicate = replace(
        candidate,
        changes=candidate.changes + (candidate.changes[0],),
        changes_processed=candidate.changes_processed + 1,
    )
    with pytest.raises(CheckpointUnavailable, match="Duplicate"):
        asyncio.run(store.commit_atomic(
            scope=duplicate.scope, expected_version=0,
            expected_cursor="", round_candidate=duplicate,
        ))
    assert not any(call[0] == RPC_COMMIT for call in db.calls)


def test_gmail_change_must_have_a_verified_history_record_identity():
    store, db, _ = make()
    candidate, _ = do_gmail()
    broken_change = replace(candidate.changes[0], history_id=None)
    with pytest.raises(CheckpointUnavailable):
        store._event_ref(GMAIL_SCOPE, candidate, 0, broken_change)


def test_graph_delta_rejects_gmail_history_identity_in_source_event():
    store, db, _ = make()
    candidate, _ = do_graph()
    broken_change = replace(candidate.changes[0], history_id="1234")
    with pytest.raises(CheckpointUnavailable):
        store._event_ref(GRAPH_SCOPE, candidate, 0, broken_change)
