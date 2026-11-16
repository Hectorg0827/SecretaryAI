"""Stage 14B synthetic coordinator coverage; SQL receives independent PG tests."""
import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.intelligence.lola import gmail_snapshot_finalization as finalizer
from app.intelligence.lola.incremental_replay import ReplayUnavailable
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, _filters,
)
from app.intelligence.lola.gmail_snapshot_staging import _anchor_digest

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"


def fixture():
    vault = CursorVault(b"k" * 32)
    scope = MailboxScope(
        COMPANY, EMPLOYEE, "gmail", "verified-principal",
        "verified-resource", "verified-mapping",
    )
    filters = _filters(scope, vault)
    anchor_hash = _anchor_digest(scope, vault, "100")
    page_hash = vault.digest("test-page", COMPANY, EMPLOYEE)
    chain_hash = hashlib.sha256((
        "lola-gmail-snapshot-chain-v1:" + anchor_hash + ":" + page_hash
    ).encode()).hexdigest()
    message = vault.digest(
        "snapshot-message-v1", COMPANY, EMPLOYEE, scope.provider_identity_ref,
        scope.resource_id, "source-message-1",
    )
    job = {
        **filters, "mapping_revision": scope.mapping_revision,
        "initial_history_ciphertext": vault.encrypt(scope, "100"),
        "initial_history_sha256": anchor_hash,
        "next_page_ciphertext": None, "next_page_sha256": None,
        "page_chain_sha256": chain_hash,
        "pages_staged": 1, "messages_staged": 1,
        "state": "snapshot_exhausted",
    }
    pages = [{
        **filters, "page_index": 0, "entry_count": 1,
        "page_digest_sha256": page_hash, "chain_digest_sha256": chain_hash,
    }]
    refs = [{
        **filters, "page_index": 0, "message_key_sha256": message,
        "thread_key_sha256": None,
        "metadata_history_key_sha256": vault.digest("test-history", "100"),
    }]
    return scope, vault, job, pages, refs


class Query:
    def __init__(self, db):
        self.db = db
        self.filters = {}
        self.columns = []
        self.limit_value = 100
    def select(self, columns):
        self.columns = columns.split(",")
        return self
    def eq(self, field, value):
        self.filters[field] = value
        return self
    def limit(self, value):
        self.limit_value = value
        return self
    def execute(self):
        if self.db.receipt is None:
            return SimpleNamespace(data=[])
        row = self.db.receipt
        if any(row.get(k) != v for k, v in self.filters.items()):
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[
            {column: row.get(column) for column in self.columns}
        ])


class RPC:
    def __init__(self, db, name, args):
        self.db = db
        self.name = name
        self.args = args
    def execute(self):
        db = self.db
        a = self.args
        if db.revoked:
            raise RuntimeError("employee consent revoked")
        if self.name == "lola_read_gmail_snapshot_bundle":
            if (a["p_employee_id"] != db.scope.employee_id
                or a["p_company_id"] != db.scope.company_id
                or a["p_resource_sha256"] != db.job["resource_sha256"]):
                raise RuntimeError("unknown tenant or employee")
            db.reads += 1
            return SimpleNamespace(data={
                "job": deepcopy(db.job), "pages": deepcopy(db.pages),
                "refs": deepcopy(db.refs),
            })
        if self.name != "lola_finalize_gmail_snapshot_baseline":
            raise RuntimeError("unrecognized RPC")
        if db.commit_failure:
            raise RuntimeError("failed atomic transaction")
        db.commit_attempts += 1
        if db.receipt:
            if db.receipt["replay_digest"] != a["p_replay_digest"]:
                raise RuntimeError("conflicting duplicate")
            return SimpleNamespace(data=1)
        db.commits += 1
        db.checkpoint = SimpleNamespace(
            generation=1, recovery_epoch=0, baseline_only=True,
            plaintext_cursor=db.expected_cursor,
            replay_digest=a["p_replay_digest"],
        )
        db.receipt = {
            **_filters(db.scope, db.vault),
            "mapping_revision": db.scope.mapping_revision,
            "initial_history_sha256": a["p_initial_history_sha256"],
            "snapshot_chain_sha256": a["p_snapshot_chain_sha256"],
            "snapshot_membership_sha256": a["p_snapshot_membership_sha256"],
            "history_digest_sha256": a["p_history_digest_sha256"],
            "reconciled_membership_sha256": a["p_reconciled_membership_sha256"],
            "cursor_sha256": a["p_cursor_sha256"],
            "replay_digest": a["p_replay_digest"],
            "checkpoint_generation": 1,
        }
        return SimpleNamespace(data=1)


class FakeDB:
    def __init__(self, scope, vault, job, pages, refs):
        self.scope, self.vault = scope, vault
        self.job, self.pages, self.refs = job, pages, refs
        self.revoked = False
        self.commit_failure = False
        self.commits = 0
        self.commit_attempts = 0
        self.reads = 0
        self.checkpoint = None
        self.receipt = None
        self.expected_cursor = "115"
    def rpc(self, name, args):
        return RPC(self, name, args)
    def table(self, name):
        assert name == "lola_gmail_snapshot_finalizations"
        return Query(self)


def setup(monkeypatch):
    scope, vault, job, pages, refs = fixture()
    db = FakeDB(scope, vault, job, pages, refs)
    monkeypatch.setattr(
        finalizer, "read_checkpoint", lambda **kwargs: db.checkpoint,
    )
    return db, scope, vault


async def normal_history(start, page):
    assert start == "100" and page is None
    return {"historyId": "115", "history": [
        {"id": "110", "messagesAdded": [
            {"message": {"id": "source-message-2", "threadId": "thread-2"}}
        ]}
    ]}


@pytest.mark.asyncio
async def test_successful_initial_checkpoint_is_not_reply_proof(monkeypatch):
    db, scope, vault = setup(monkeypatch)
    approved = []
    result = await finalizer.finalize_verified_gmail_snapshot(
        db=db, scope=scope, vault=vault, fetch_history_page=normal_history,
        reauthorize=lambda: approved.append(True),
    )
    assert result.generation == 1 and result.baseline_only is True
    assert result.reply_absence_proven is False
    assert result.follow_up_eligible is False
    assert result.outbound_action_approved is False
    assert result.external_messages_sent is False
    assert db.commits == 1 and db.reads == 2
    assert len(approved) >= 4


@pytest.mark.asyncio
async def test_same_evidence_retry_never_creates_second_commit(monkeypatch):
    db, scope, vault = setup(monkeypatch)
    for _ in range(2):
        result = await finalizer.finalize_verified_gmail_snapshot(
            db=db, scope=scope, vault=vault, fetch_history_page=normal_history,
            reauthorize=lambda: None,
        )
        assert result.generation == 1
    assert db.commit_attempts == 2 and db.commits == 1


@pytest.mark.asyncio
async def test_interrupted_history_pagination_never_commits(monkeypatch):
    db, scope, vault = setup(monkeypatch)
    pages = []

    async def interrupted(start, token):
        pages.append((start, token))
        if token == "page-2":
            raise ConnectionError("synthetic Gmail outage")
        return {
            "historyId": "112",
            "history": [{"id": "110", "messagesAdded": [
                {"message": {"id": "a", "threadId": "t"}}
            ]}],
            "nextPageToken": "page-2",
        }

    with pytest.raises(ReplayUnavailable):
        await finalizer.finalize_verified_gmail_snapshot(
            db=db, scope=scope, vault=vault, fetch_history_page=interrupted,
            reauthorize=lambda: None,
        )
    assert pages == [("100", None), ("100", "page-2")]
    assert db.commits == 0 and db.receipt is None


@pytest.mark.asyncio
async def test_revoked_consent_during_history_read_prevents_write(monkeypatch):
    db, scope, vault = setup(monkeypatch)
    checks = [0]

    def current_authorization():
        checks[0] += 1
        if checks[0] >= 3:
            db.revoked = True
            raise RuntimeError("consent withdrawn")

    with pytest.raises((ReplayUnavailable, CheckpointUnavailable)):
        await finalizer.finalize_verified_gmail_snapshot(
            db=db, scope=scope, vault=vault, fetch_history_page=normal_history,
            reauthorize=current_authorization,
        )
    assert db.commits == 0


@pytest.mark.asyncio
async def test_changed_snapshot_reference_before_commit_rejected(monkeypatch):
    db, scope, vault = setup(monkeypatch)

    async def concurrent_change(start, token):
        db.refs[0]["message_key_sha256"] = vault.digest("changed-member", "x")
        return {"historyId": "115", "history": []}

    with pytest.raises(CheckpointUnavailable, match="changed during provider replay"):
        await finalizer.finalize_verified_gmail_snapshot(
            db=db, scope=scope, vault=vault, fetch_history_page=concurrent_change,
            reauthorize=lambda: None,
        )
    assert db.commits == 0


@pytest.mark.asyncio
async def test_cross_employee_bundle_denied(monkeypatch):
    db, scope, vault = setup(monkeypatch)
    other = MailboxScope(
        COMPANY,"33333333-3333-4333-8333-333333333333",
        "gmail",scope.provider_identity_ref,scope.resource_id,scope.mapping_revision,
    )
    with pytest.raises(CheckpointUnavailable):
        finalizer.load_consistent_gmail_snapshot(db=db, scope=other, vault=vault)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_unknown_atomic_commit_outcome_never_claims_success(monkeypatch):
    db, scope, vault = setup(monkeypatch)
    db.commit_failure = True
    with pytest.raises(CheckpointUnavailable, match="safe retry"):
        await finalizer.finalize_verified_gmail_snapshot(
            db=db, scope=scope, vault=vault, fetch_history_page=normal_history,
            reauthorize=lambda: None,
        )
    assert db.commits == 0
