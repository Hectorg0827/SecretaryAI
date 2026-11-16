"""Stage 13: zero-network resumed Gmail snapshot staging and provider privacy."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.intelligence.lola.gmail_snapshot_staging import (
    SnapshotProgress, advance_gmail_snapshot_page, read_snapshot_progress,
)
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, _filters,
)
from app.intelligence.lola.incremental_replay import ReplayUnavailable

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"


def scope():
    return MailboxScope(
        company_id=COMPANY, employee_id=EMPLOYEE, provider="gmail",
        provider_identity_ref="verified-private-oauth",
        resource_id="employee@private.invalid",
        mapping_revision="verified-case-mapping",
    )


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.columns = []
        self.filters = {}
        self.bound = 100
    def select(self, columns):
        self.columns = columns.split(",")
        return self
    def eq(self, name, value):
        self.filters[name] = value
        return self
    def limit(self, n):
        self.bound = n
        return self
    def execute(self):
        data = getattr(self.db, self.table)
        rows = [
            {field: row.get(field) for field in self.columns}
            for row in data if all(str(row.get(k)) == str(v) for k,v in self.filters.items())
        ]
        return SimpleNamespace(data=rows[:self.bound])


class RPC:
    def __init__(self, db, args):
        self.db, self.a = db, args
    def execute(self):
        db, a = self.db, self.a
        if db.revoked:
            raise RuntimeError("revoked employee consent")
        if db.conflict:
            raise RuntimeError("concurrent worker CAS conflict")
        filters = {
            "company_id": a["p_company_id"],
            "employee_id": a["p_employee_id"],
            "provider": "gmail",
            "provider_identity_sha256": a["p_provider_identity_sha256"],
            "resource_sha256": a["p_resource_sha256"],
        }
        jobs = db.lola_gmail_snapshot_jobs
        prior = jobs[0] if jobs else None
        if prior is None:
            if a["p_expected_pages"] != 0:
                raise RuntimeError("missing initial page")
            idx = 0
            chain = "d" * 64
        else:
            if (prior["pages_staged"] != a["p_expected_pages"]
                    or prior["next_page_sha256"] != a["p_expected_page_cursor_sha256"]
                    or prior["state"] != "collecting"):
                raise RuntimeError("stale snapshot")
            idx = prior["pages_staged"]
            chain = "e" * 64
        if len(a["p_entries"]) > 100 or (
            prior and prior["messages_staged"]+len(a["p_entries"])>20000
        ):
            raise RuntimeError("large page")
        existing = {r["message_key_sha256"] for r in db.lola_gmail_snapshot_refs}
        hashes = [r["message_key_sha256"] for r in a["p_entries"]]
        if len(hashes) != len(set(hashes)) or any(h in existing for h in hashes):
            raise RuntimeError("duplicate across pages")
        updated = {
            **filters,
            "mapping_revision": a["p_mapping_revision"],
            "initial_history_ciphertext": (
                prior["initial_history_ciphertext"] if prior
                else a["p_initial_history_ciphertext"]
            ),
            "initial_history_sha256": a["p_initial_history_sha256"],
            "next_page_ciphertext": a["p_next_page_ciphertext"],
            "next_page_sha256": a["p_next_page_sha256"],
            "page_chain_sha256": chain,
            "pages_staged": idx+1,
            "messages_staged": (prior["messages_staged"] if prior else 0)+len(hashes),
            "state": "snapshot_exhausted" if a["p_exhausted"] else "collecting",
        }
        db.lola_gmail_snapshot_jobs = [updated]
        db.lola_gmail_snapshot_pages.append({
            **filters, "page_index": idx,
            "page_digest_sha256": a["p_page_digest_sha256"],
            "entry_count": len(hashes), "chain_digest_sha256": chain,
        })
        db.lola_gmail_snapshot_refs.extend(
            {**filters, **ref, "page_index": idx} for ref in a["p_entries"]
        )
        return SimpleNamespace(data=idx+1)


class FakeDB:
    def __init__(self):
        self.lola_gmail_snapshot_jobs = []
        self.lola_gmail_snapshot_pages = []
        self.lola_gmail_snapshot_refs = []
        self.revoked = False
        self.conflict = False
        self.commit_count = 0
    def table(self, name):
        assert name in ("lola_gmail_snapshot_jobs", "lola_gmail_snapshot_pages")
        return Query(self, name)
    def rpc(self, name, args):
        assert name == "lola_stage_gmail_snapshot_page"
        self.commit_count += 1
        return RPC(self, args)


class GmailSource:
    def __init__(self, total=500):
        self.total = total
        self.pages = 0
        self.metadata = 0
        self.profiles = 0
        self.return_duplicate = False
        self.inconsistent_thread = False
    async def profile(self):
        self.profiles += 1
        return {"historyId": str(100 + self.profiles)}
    async def list_page(self, token):
        self.pages += 1
        offset = int(token[5:]) if token else 0
        n = min(100, self.total-offset)
        return {
            "messages": [
                {"id": "PRIVATE-" + str(0 if self.return_duplicate else offset+i),
                 "threadId": "T" + str(offset+i)}
                for i in range(n)
            ],
            **({"nextPageToken": f"page-{offset+n}"} if offset+n < self.total else {}),
            **({"resultSizeEstimate": 0} if n==0 else {}),
        }
    async def metadata_get(self, message_id):
        self.metadata += 1
        idx = int(message_id.split("-")[-1])
        return {"id": message_id,
                "threadId": ("OTHER" if self.inconsistent_thread else "T"+str(idx)),
                "historyId": "110"}


@pytest.mark.asyncio
async def test_resumes_five_pages_and_stages_500_without_400_message_truncation():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=500)
    for i in range(5):
        progress = await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page,
            fetch_message_metadata=gmail.metadata_get,
        )
        assert progress.pages_staged == i+1
        assert progress.messages_staged == (i+1)*100
        assert progress.follow_up_eligible is False
        assert progress.reply_absence_proven is False
        assert progress.durable_provider_checkpoint is False
        assert progress.history_reconciled is False
    assert progress.state == "snapshot_exhausted"
    assert progress.next_page_token is None
    assert len(db.lola_gmail_snapshot_pages)==5
    assert len(db.lola_gmail_snapshot_refs)==500
    assert gmail.profiles==5
    assert gmail.metadata==500
    assert len(db.lola_gmail_snapshot_jobs)==1
    assert "PRIVATE-0" not in str(db.lola_gmail_snapshot_refs)
    assert "page-100" not in str(db.lola_gmail_snapshot_jobs)
    with pytest.raises(CheckpointUnavailable):
        await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
        )


@pytest.mark.asyncio
async def test_empty_verified_mailbox_creates_unreconciled_snapshot_only():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=0)
    progress = await advance_gmail_snapshot_page(
        db=db, scope=s, vault=v, fetch_profile=gmail.profile,
        fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
    )
    assert progress.messages_staged == 0
    assert progress.state == "snapshot_exhausted"
    assert progress.follow_up_eligible is False


@pytest.mark.asyncio
async def test_revoked_consent_prevents_page_commit_and_does_not_advance():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=200)
    first = await advance_gmail_snapshot_page(
        db=db, scope=s, vault=v, fetch_profile=gmail.profile,
        fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
    )
    db.revoked = True
    with pytest.raises(CheckpointUnavailable):
        await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
        )
    assert read_snapshot_progress(db=db, scope=s, vault=v).pages_staged==1
    assert len(db.lola_gmail_snapshot_pages)==1


@pytest.mark.asyncio
async def test_conflicting_worker_cas_rejected_without_advancing():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=200)
    await advance_gmail_snapshot_page(
        db=db, scope=s, vault=v, fetch_profile=gmail.profile,
        fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
    )
    db.conflict = True
    with pytest.raises(CheckpointUnavailable):
        await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
        )
    assert db.lola_gmail_snapshot_jobs[0]["messages_staged"]==100


@pytest.mark.asyncio
async def test_duplicate_source_message_on_second_page_rolls_back():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=200)
    await advance_gmail_snapshot_page(
        db=db, scope=s, vault=v, fetch_profile=gmail.profile,
        fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
    )
    gmail.return_duplicate = True
    with pytest.raises(ReplayUnavailable):
        await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
        )
    assert db.lola_gmail_snapshot_jobs[0]["pages_staged"]==1


@pytest.mark.asyncio
async def test_mismatched_metadata_thread_does_not_stage_partial_page():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=200)
    gmail.inconsistent_thread = True
    with pytest.raises(ReplayUnavailable):
        await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
        )
    assert db.commit_count==0


@pytest.mark.asyncio
async def test_unknown_tenant_cannot_read_other_employee_progress():
    db, v, s, gmail = FakeDB(), CursorVault(b"k"*32), scope(), GmailSource(total=200)
    await advance_gmail_snapshot_page(
        db=db, scope=s, vault=v, fetch_profile=gmail.profile,
        fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
    )
    other = MailboxScope(
        "33333333-3333-4333-8333-333333333333", EMPLOYEE,
        "gmail", s.provider_identity_ref, s.resource_id, s.mapping_revision,
    )
    assert read_snapshot_progress(db=db, scope=other, vault=v) is None


@pytest.mark.asyncio
async def test_missing_receipt_never_reports_successful_page():
    class BrokenReceipt(FakeDB):
        def rpc(self, name, args):
            obj = super().rpc(name,args)
            old_execute = obj.execute
            def execute():
                result = old_execute()
                self.lola_gmail_snapshot_pages.clear()
                return result
            obj.execute = execute
            return obj
    db, v, s, gmail = BrokenReceipt(), CursorVault(b"k"*32), scope(), GmailSource(total=1)
    with pytest.raises(CheckpointUnavailable,match="receipt"):
        await advance_gmail_snapshot_page(
            db=db, scope=s, vault=v, fetch_profile=gmail.profile,
            fetch_messages_page=gmail.list_page, fetch_message_metadata=gmail.metadata_get,
        )
