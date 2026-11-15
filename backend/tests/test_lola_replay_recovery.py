"""No-network Stage 12 Gmail recovery cursor epoch and audit persistence tests."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.intelligence.lola.gmail_bootstrap import GmailBootstrapEvidence
from app.intelligence.lola.incremental_replay import ChangeRef, GmailHistoryExpired
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, StoredCheckpoint,
    _filters, read_checkpoint,
)
from app.intelligence.lola.replay_recovery import (
    commit_confirmed_gmail_recovery,
)

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def scope():
    return MailboxScope(
        company_id=COMPANY, employee_id=EMPLOYEE,
        provider="gmail", provider_identity_ref="verified-oauth-ref",
        resource_id="employee@private.invalid", mapping_revision="signed-rev",
    )


def vault():
    return CursorVault(b"k" * 32)


def baseline(**overrides):
    params = dict(
        next_history_id="250",
        changed_records=(ChangeRef("PRIVATE-MESSAGE-ID", "added"),),
        page_count=2, message_count=1, history_record_count=0,
        digest="b" * 64,
    )
    params.update(overrides)
    return GmailBootstrapEvidence(**params)


def expiry():
    return GmailHistoryExpired("synthetic observed Gmail history 404")


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.cols = []
        self.limit_value = 100

    def select(self, cols):
        self.cols = cols.split(",")
        return self

    def eq(self, name, value):
        self.filters[name] = value
        return self

    def limit(self, n):
        self.limit_value = n
        return self

    def execute(self):
        records = getattr(self.db, self.table)
        rows = [
            {key: item[key] for key in self.cols if key in item}
            for item in records
            if all(item.get(k) == v for k, v in self.filters.items())
        ]
        return SimpleNamespace(data=rows[:self.limit_value])


class RPC:
    def __init__(self, db, args):
        self.db, self.args = db, args
    def execute(self):
        if self.db.reject:
            raise RuntimeError("private OAuth token must not leak")
        a = self.args
        existing = self.db.lola_provider_replay_checkpoints[0]
        if (existing["generation"] != a["p_expected_generation"]
                or existing["cursor_sha256"] != a["p_expected_cursor_sha256"]):
            raise RuntimeError("CAS conflict")
        before = deepcopy(existing)
        existing.update({
            "cursor_ciphertext": a["p_cursor_ciphertext"],
            "cursor_sha256": a["p_cursor_sha256"],
            "replay_digest": a["p_replay_digest"],
            "baseline_only": True,
            "recovery_epoch": existing["recovery_epoch"]+1,
            "generation": existing["generation"]+1,
            "replayed_through": a["p_replayed_through"],
        })
        self.db.lola_provider_replay_recoveries.append({
            **{k: existing[k] for k in (
                "company_id", "employee_id", "provider",
                "provider_identity_sha256", "resource_sha256",
                "mapping_revision",
            )},
            "old_generation": before["generation"],
            "new_generation": existing["generation"],
            "old_epoch": before["recovery_epoch"],
            "new_epoch": existing["recovery_epoch"],
            "old_cursor_sha256": before["cursor_sha256"],
            "new_cursor_sha256": existing["cursor_sha256"],
            "old_replay_digest": before["replay_digest"],
            "new_replay_digest": existing["replay_digest"],
            "reason": a["p_reason"],
            "initial_message_count": a["p_initial_message_count"],
            "new_event_count": len(a["p_events"]),
        })
        for item in a["p_events"]:
            self.db.lola_provider_replay_events.append({
                **{k: existing[k] for k in (
                    "company_id","employee_id","provider",
                    "provider_identity_sha256","resource_sha256",
                )},
                "event_key_sha256": item["key_sha256"],
                "recovery_epoch": existing["recovery_epoch"],
                "replay_digest": a["p_replay_digest"],
            })
        return SimpleNamespace(data=existing["generation"])


class DB:
    def __init__(self):
        v = vault()
        s = scope()
        self.lola_provider_replay_checkpoints = [{
            **_filters(s, v),
            "mapping_revision": s.mapping_revision,
            "cursor_ciphertext": v.encrypt(s, "100"),
            "cursor_sha256": v.digest("cursor", "100"),
            "replay_digest": "a"*64,
            "baseline_only": False,
            "recovery_epoch": 0,
            "replayed_through": NOW.isoformat(),
            "generation": 7,
        }]
        self.lola_provider_replay_events = [{
            **_filters(s, v),
            "event_key_sha256": "c"*64,
            "recovery_epoch": 0,
            "replay_digest": "a"*64,
        }]
        self.lola_provider_replay_recoveries = []
        self.reject = False
        self.rpc_called = False
        self.hide_audit = False
    def table(self, name):
        return Query(self, name)
    def rpc(self, name, args):
        assert name == "lola_recover_expired_gmail_replay"
        self.rpc_called = True
        return RPC(self, args)


def prior(db):
    return read_checkpoint(db=db, scope=scope(), vault=vault())


def commit(db, old=None, new=None, observed=None):
    return commit_confirmed_gmail_recovery(
        db=db, scope=scope(), vault=vault(),
        prior=old or prior(db), baseline=new or baseline(),
        observed_expiry=observed or expiry(), observed_at=NOW,
    )


def test_expired_gmail_cursor_recovery_preserves_old_events_and_audits_epoch():
    db = DB()
    result = commit(db)
    assert result.generation == 8
    assert result.recovery_epoch == 1
    assert result.complete_mailbox_history is False
    assert result.reply_absence_proven is False
    assert result.cross_channel_mapping_verified is False
    assert result.follow_up_eligible is False
    assert result.outbound_action_approved is False
    assert db.rpc_called is True
    assert prior(db).plaintext_cursor == "250"
    assert prior(db).recovery_epoch == 1
    assert prior(db).baseline_only is True
    assert len(db.lola_provider_replay_recoveries) == 1
    assert db.lola_provider_replay_recoveries[0]["old_epoch"] == 0
    assert db.lola_provider_replay_recoveries[0]["new_epoch"] == 1
    assert len(db.lola_provider_replay_events) == 2
    assert db.lola_provider_replay_events[0]["recovery_epoch"] == 0
    assert db.lola_provider_replay_events[1]["recovery_epoch"] == 1
    assert "PRIVATE-MESSAGE-ID" not in str(db.lola_provider_replay_events)
    assert "250" not in db.lola_provider_replay_checkpoints[0]["cursor_ciphertext"]


@pytest.mark.parametrize("case", ["no_expiry", "old_cursor", "snapshot_forged", "wrong_provider", "stale_time"])
def test_recovery_missing_authoritative_evidence_never_calls_rpc(case):
    db = DB()
    prev, new, observed, s, t = prior(db), baseline(), expiry(), scope(), NOW
    from dataclasses import replace
    if case == "no_expiry":
        observed = RuntimeError("not a confirmed provider 404")
    elif case == "old_cursor":
        new = baseline(next_history_id="99")
    elif case == "snapshot_forged":
        new = replace(new, reply_absence_proven=True)
    elif case == "wrong_provider":
        s = MailboxScope(COMPANY, EMPLOYEE, "outlook",
                         s.provider_identity_ref, s.resource_id, s.mapping_revision)
    elif case == "stale_time":
        t = datetime(2026, 11, 14, tzinfo=timezone.utc)
    with pytest.raises(CheckpointUnavailable):
        commit_confirmed_gmail_recovery(
            db=db, scope=s, vault=vault(), prior=prev,
            baseline=new, observed_expiry=observed, observed_at=t,
        )
    assert db.rpc_called is False
    assert len(db.lola_provider_replay_recoveries) == 0


def test_rpc_failure_does_not_claim_committed_epoch_or_log_sensitive_error():
    db = DB()
    db.reject = True
    with pytest.raises(CheckpointUnavailable) as exc:
        commit(db)
    assert "private OAuth" not in str(exc.value)
    assert db.lola_provider_replay_recoveries == []
    assert db.lola_provider_replay_checkpoints[0]["generation"] == 7


def test_cas_conflict_when_old_epoch_or_generation_is_stale():
    db = DB()
    stale = prior(db)
    commit(db)
    with pytest.raises(CheckpointUnavailable):
        commit(db, old=stale)
    assert len(db.lola_provider_replay_recoveries) == 1


def test_error_when_recovery_audit_is_missing_after_rpc():
    class NoAudit(DB):
        def rpc(self, name, args):
            runner = super().rpc(name, args)
            old_execute = runner.execute
            def execute():
                res = old_execute()
                self.lola_provider_replay_recoveries.clear()
                return res
            runner.execute = execute
            return runner
    db = NoAudit()
    with pytest.raises(CheckpointUnavailable, match="audit"):
        commit(db)


def test_error_when_recovered_event_epoch_is_wrong():
    class BrokenEpoch(DB):
        def rpc(self, name, args):
            runner = super().rpc(name, args)
            old_execute = runner.execute
            def execute():
                res = old_execute()
                self.lola_provider_replay_events[-1]["recovery_epoch"] = 0
                return res
            runner.execute = execute
            return runner
    db = BrokenEpoch()
    with pytest.raises(CheckpointUnavailable, match="audit"):
        commit(db)


def test_no_event_baseline_can_be_recovered_and_remains_nonactionable():
    db = DB()
    result = commit(db, new=baseline(changed_records=(), message_count=0))
    assert result.recovery_epoch == 1
    assert result.follow_up_eligible is False
    assert db.lola_provider_replay_recoveries[0]["new_event_count"] == 0
