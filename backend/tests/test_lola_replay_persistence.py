"""Zero-network tests for AES-GCM employee-private replay storage and CAS."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.intelligence.lola.continuity import WorkScope
from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase
from app.intelligence.lola.incremental_replay import (
    ChangeRef, GmailReplay, GraphReplay,
)
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope,
    commit_replay, read_checkpoint, scope_for_verified_case,
)

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
KEY = b"k" * 32
GMAIL = "employee@private.invalid"


class Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters = {}
        self.limit_value = None

    def select(self, names):
        self.names = names.split(",")
        return self

    def eq(self, name, value):
        self.filters[name] = value
        return self

    def limit(self, limit):
        self.limit_value = limit
        return self

    def execute(self):
        items = self.db.checkpoints if self.name.endswith("checkpoints") else self.db.events
        results = [
            {col: row[col] for col in self.names if col in row}
            for row in items
            if all(row.get(k) == v for k, v in self.filters.items())
        ]
        return type("Result", (), {"data": results[:self.limit_value] if self.limit_value else results})()


class FakeRPC:
    def __init__(self, db, args):
        self.db = db
        self.args = args

    def execute(self):
        if self.db.reject:
            raise RuntimeError("private provider ID / secret in simulated SQL error")
        a = self.args
        expected = a["p_expected_generation"]
        matching = [
            row for row in self.db.checkpoints
            if all(row.get(k) == a["p_" + k] for k in (
                "company_id", "employee_id", "provider",
                "provider_identity_sha256", "resource_sha256",
            ))
        ]
        if len(matching) > 1 or (matching[0]["generation"] if matching else 0) != expected:
            raise RuntimeError("stale CAS generation")
        if matching and matching[0]["mapping_revision"] != a["p_mapping_revision"]:
            raise RuntimeError("mapping rotated")
        if matching:
            row = matching[0]
            if datetime.fromisoformat(row["replayed_through"]) > datetime.fromisoformat(a["p_replayed_through"]):
                raise RuntimeError("regressing timestamp")
            baseline = row["baseline_only"] and a["p_baseline_only"]
        else:
            if not a["p_baseline_only"]:
                raise RuntimeError("initial Graph baseline required")
            row = {}
            self.db.checkpoints.append(row)
            baseline = True
        generation = expected + 1
        row.update({
            "company_id": a["p_company_id"],
            "employee_id": a["p_employee_id"],
            "provider": a["p_provider"],
            "provider_identity_sha256": a["p_provider_identity_sha256"],
            "resource_sha256": a["p_resource_sha256"],
            "mapping_revision": a["p_mapping_revision"],
            "cursor_ciphertext": a["p_cursor_ciphertext"],
            "cursor_sha256": a["p_cursor_sha256"],
            "replay_digest": a["p_replay_digest"],
            "baseline_only": baseline,
            "replayed_through": a["p_replayed_through"],
            "generation": generation,
        })
        for event in a["p_events"]:
            self.db.events.append({
                **{k: row[k] for k in (
                    "company_id", "employee_id", "provider",
                    "provider_identity_sha256", "resource_sha256",
                )},
                "event_key_sha256": event["key_sha256"],
                "operation": event["operation"],
                "replay_digest": a["p_replay_digest"],
            })
        return type("Result", (), {"data": generation})()


class FakeDB:
    def __init__(self):
        self.checkpoints = []
        self.events = []
        self.calls = 0
        self.reject = False
        self.rpc_called = False

    def table(self, name):
        self.calls += 1
        return Query(self, name)

    def rpc(self, name, args):
        assert name == "lola_commit_provider_replay"
        self.rpc_called = True
        return FakeRPC(self, args)


def vault():
    return CursorVault(KEY)


def scope(provider="outlook", resource="FOLDER-A", employee=EMPLOYEE):
    return MailboxScope(
        company_id=COMPANY, employee_id=employee,
        provider=provider, provider_identity_ref="verified-provider-connection",
        resource_id=resource, mapping_revision="verified-revision",
    )


def graph(first=True, cursor="https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-A/messages/delta?$deltatoken=new"):
    return GraphReplay(
        next_delta_url=cursor, changed_records=(ChangeRef("PRIVATE-MSG-ID", "changed"),),
        folder_id="FOLDER-A", page_count=1, record_count=1,
        digest="a" * 64, initial_baseline=first,
    )


def test_cursor_round_trip_same_employee_and_mailbox_only():
    secret = vault()
    binding = scope()
    clear = graph().next_delta_url
    sealed = secret.encrypt(binding, clear)
    assert sealed.startswith("v1:")
    assert clear not in sealed
    assert secret.decrypt(binding, sealed) == clear
    for wrong in (
        scope(employee="33333333-3333-4333-8333-333333333333"),
        scope(resource="FOLDER-B"),
        MailboxScope(COMPANY, EMPLOYEE, "outlook", "other-connection", "FOLDER-A", "verified-revision"),
        MailboxScope(COMPANY, EMPLOYEE, "outlook", "verified-provider-connection", "FOLDER-A", "new-revision"),
    ):
        with pytest.raises(CheckpointUnavailable):
            secret.decrypt(wrong, sealed)
    with pytest.raises(CheckpointUnavailable):
        CursorVault(b"bad")


def test_cursor_ciphertext_integrity_rejected():
    v = vault()
    data = v.encrypt(scope(), "https://graph.microsoft.com/private-delta-link")
    with pytest.raises(CheckpointUnavailable):
        v.decrypt(scope(), data[:-2] + "AA")
    with pytest.raises(CheckpointUnavailable):
        v.decrypt(scope(), "v7:AA")


def test_graph_baseline_is_recorded_atomically_but_not_declared_complete():
    db = FakeDB()
    v = vault()
    result = commit_replay(
        db=db, scope=scope(), vault=v, replay=graph(), previously_read=None,
        observed_at=NOW,
    )
    assert result.generation == 1
    assert result.cursor_encrypted_and_persisted is True
    assert result.complete_mailbox_history is False
    assert result.reply_absence_proven is False
    assert result.outbound_action_approved is False
    assert db.rpc_called
    assert db.checkpoints[0]["cursor_ciphertext"] != graph().next_delta_url
    assert "PRIVATE" not in str(db.checkpoints[0])
    assert "PRIVATE-MSG-ID" not in str(db.events[0])
    assert read_checkpoint(db=db, scope=scope(), vault=v).baseline_only is True


def test_graph_incremental_commits_monotonically_and_retires_baseline_only_state():
    db = FakeDB()
    v = vault()
    first = graph()
    commit_replay(db=db, scope=scope(), vault=v, replay=first, previously_read=None, observed_at=NOW)
    previous = read_checkpoint(db=db, scope=scope(), vault=v)
    updated = graph(False, "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-A/messages/delta?$deltatoken=later")
    updated = GraphReplay(
        next_delta_url=updated.next_delta_url,
        changed_records=updated.changed_records, folder_id=updated.folder_id,
        page_count=1, record_count=1, digest="b" * 64, initial_baseline=False,
    )
    final = commit_replay(
        db=db, scope=scope(), vault=v, replay=updated,
        previously_read=previous, observed_at=NOW,
    )
    assert final.generation == 2
    assert read_checkpoint(db=db, scope=scope(), vault=v).baseline_only is False
    assert final.reply_absence_proven is False


def test_cas_conflict_is_not_mistaken_for_committed_checkpoint():
    db = FakeDB()
    v = vault()
    commit_replay(db=db, scope=scope(), vault=v, replay=graph(), previously_read=None, observed_at=NOW)
    with pytest.raises(CheckpointUnavailable):
        commit_replay(db=db, scope=scope(), vault=v, replay=graph(), previously_read=None, observed_at=NOW)
    assert len(db.checkpoints) == 1


def test_missing_gmail_baseline_never_invokes_database_write():
    db = FakeDB()
    replay = GmailReplay(
        next_history_id="456", changed_records=(ChangeRef("PRIVATE-GMAIL", "added"),),
        page_count=1, record_count=1, digest="b" * 64,
    )
    with pytest.raises(CheckpointUnavailable):
        commit_replay(
            db=db, scope=scope(provider="gmail", resource=GMAIL),
            vault=vault(), replay=replay, previously_read=None, observed_at=NOW,
        )
    assert not db.rpc_called


def test_failed_rpc_keeps_uncommitted_cursor_unpromoted():
    db = FakeDB()
    db.reject = True
    with pytest.raises(CheckpointUnavailable) as exc:
        commit_replay(
            db=db, scope=scope(), vault=vault(), replay=graph(),
            previously_read=None, observed_at=NOW,
        )
    assert "PRIVATE" not in str(exc.value)
    assert db.checkpoints == [] and db.events == []


def test_mismatched_provider_or_folder_does_not_call_rpc():
    db = FakeDB()
    for wrong in (scope(provider="gmail", resource=GMAIL), scope(resource="FOLDER-B")):
        with pytest.raises(CheckpointUnavailable):
            commit_replay(
                db=db, scope=wrong, vault=vault(), replay=graph(),
                previously_read=None, observed_at=NOW,
            )
    assert not db.rpc_called


def test_checkpoint_tenant_employee_scope_filters_all_reads():
    db = FakeDB()
    v = vault()
    commit_replay(db=db, scope=scope(), vault=v, replay=graph(), previously_read=None, observed_at=NOW)
    assert read_checkpoint(
        db=db, scope=scope(employee="33333333-3333-4333-8333-333333333333"),
        vault=v,
    ) is None
    db.checkpoints[0]["employee_id"] = "33333333-3333-4333-8333-333333333333"
    assert read_checkpoint(db=db, scope=scope(), vault=v) is None


def test_mapping_revision_change_fails_closed_on_existing_checkpoint():
    db = FakeDB()
    v = vault()
    commit_replay(db=db, scope=scope(), vault=v, replay=graph(), previously_read=None, observed_at=NOW)
    changed = MailboxScope(COMPANY, EMPLOYEE, "outlook",
                           "verified-provider-connection", "FOLDER-A", "revoked-revision")
    with pytest.raises(CheckpointUnavailable):
        read_checkpoint(db=db, scope=changed, vault=v)


def test_absent_encryption_key_never_uses_insecure_default(monkeypatch):
    monkeypatch.delenv("LOLA_PROVIDER_CURSOR_KEY_B64", raising=False)
    with pytest.raises(CheckpointUnavailable):
        CursorVault.from_backend_environment()


def test_undetected_event_persistence_is_never_reported_as_success():
    class DiscardEventsDB(FakeDB):
        def rpc(self, name, args):
            base = super().rpc(name, args)
            original = base.execute
            def execute():
                response = original()
                self.events.clear()
                return response
            base.execute = execute
            return base
    db = DiscardEventsDB()
    with pytest.raises(CheckpointUnavailable):
        commit_replay(
            db=db, scope=scope(), vault=vault(), replay=graph(),
            previously_read=None, observed_at=NOW,
        )


def test_bad_employee_scope_uuid_is_rejected():
    db = FakeDB()
    with pytest.raises(CheckpointUnavailable):
        read_checkpoint(db=db, scope=scope(employee="not-a-uuid"), vault=vault())


def test_duplicate_or_cross_tenant_database_row_not_trusted():
    db = FakeDB()
    v = vault()
    commit_replay(db=db, scope=scope(), vault=v, replay=graph(), previously_read=None, observed_at=NOW)
    db.checkpoints.append(deepcopy(db.checkpoints[0]))
    with pytest.raises(CheckpointUnavailable):
        read_checkpoint(db=db, scope=scope(), vault=v)
