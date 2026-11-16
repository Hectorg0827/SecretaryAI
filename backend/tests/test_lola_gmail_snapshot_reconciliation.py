"""Stage 14 read-only Gmail snapshot/history proof; no provider or DB writes."""
import hashlib
from dataclasses import replace

import pytest

from app.intelligence.lola.gmail_snapshot_reconciliation import (
    reconcile_gmail_snapshot_preview, verify_snapshot_ledger,
)
from app.intelligence.lola.gmail_snapshot_staging import (
    SnapshotProgress, _anchor_digest,
)
from app.intelligence.lola.incremental_replay import GmailHistoryExpired, ReplayUnavailable
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, CursorVault, MailboxScope, _filters,
)

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"


def fixture():
    vault = CursorVault(b"k" * 32)
    scope = MailboxScope(
        company_id=COMPANY, employee_id=EMPLOYEE, provider="gmail",
        provider_identity_ref="authorized-employee-mailbox",
        resource_id="verified-resource.invalid",
        mapping_revision="consent-and-case-revision-1",
    )
    anchor = "100"
    digest = _anchor_digest(scope, vault, anchor)
    fields = _filters(scope, vault)
    first = "a" * 64
    second = "b" * 64
    c1 = hashlib.sha256(
        ("lola-gmail-snapshot-chain-v1:" + digest + ":" + first).encode()
    ).hexdigest()
    c2 = hashlib.sha256((c1 + ":" + second).encode()).hexdigest()
    pages = [
        {**fields, "page_index": 0, "entry_count": 1,
         "page_digest_sha256": first, "chain_digest_sha256": c1},
        {**fields, "page_index": 1, "entry_count": 1,
         "page_digest_sha256": second, "chain_digest_sha256": c2},
    ]
    refs = [
        {
            **fields, "page_index": index,
            "message_key_sha256": vault.digest(
                "snapshot-message-v1", COMPANY, EMPLOYEE,
                scope.provider_identity_ref, scope.resource_id, "message-" + str(index),
            ),
            "thread_key_sha256": vault.digest("thread", str(index)),
            "metadata_history_key_sha256": vault.digest("history", str(index)),
        }
        for index in range(2)
    ]
    progress = SnapshotProgress(
        pages_staged=2, messages_staged=2,
        initial_history_id=anchor, next_page_token=None,
        initial_history_sha256=digest, next_page_sha256=None,
        page_chain_sha256=c2, state="snapshot_exhausted",
    )
    return scope, vault, progress, pages, refs


def test_verified_complete_chain_and_private_hmac_membership():
    scope, vault, progress, pages, refs = fixture()
    proof = verify_snapshot_ledger(
        scope=scope, vault=vault, progress=progress,
        pages=pages, refs=refs,
    )
    assert proof.pages == 2 and proof.messages == 2
    assert proof.provider_checkpoint_committed is False
    assert proof.reply_absence_proven is False
    assert all("message-" not in key for key in proof.message_keys)


@pytest.mark.parametrize("failure", [
    "missing_page", "tampered_page", "wrong_page_count", "missing_ref",
    "duplicate_ref", "wrong_scope", "wrong_anchor", "partial_state",
    "duplicated_page", "bad_ref_page", "bad_digest",
])
def test_preflight_rejects_incomplete_or_corrupt_evidence(failure):
    scope, vault, progress, pages, refs = fixture()
    if failure == "missing_page":
        pages.pop()
    elif failure == "tampered_page":
        pages[1]["chain_digest_sha256"] = "c" * 64
    elif failure == "wrong_page_count":
        pages[0]["entry_count"] = 2
    elif failure == "missing_ref":
        refs.pop()
    elif failure == "duplicate_ref":
        refs[1]["message_key_sha256"] = refs[0]["message_key_sha256"]
    elif failure == "wrong_scope":
        refs[1]["employee_id"] = "33333333-3333-4333-8333-333333333333"
    elif failure == "wrong_anchor":
        progress = replace(progress, initial_history_id="101")
    elif failure == "partial_state":
        progress = replace(progress, state="collecting", next_page_token="page-3")
    elif failure == "duplicated_page":
        pages[1]["page_index"] = 0
    elif failure == "bad_ref_page":
        refs[1]["page_index"] = 12
    elif failure == "bad_digest":
        refs[1]["metadata_history_key_sha256"] = "not-a-valid-hmac"
    with pytest.raises(CheckpointUnavailable):
        verify_snapshot_ledger(
            scope=scope, vault=vault, progress=progress,
            pages=pages, refs=refs,
        )


@pytest.mark.asyncio
async def test_replays_from_original_anchor_and_never_authorizes_followup():
    scope, vault, progress, pages, refs = fixture()
    fetch_calls = []
    authorizations = []

    async def history(start, page):
        fetch_calls.append((start, page))
        return {
            "historyId": "115",
            "history": [
                {"id": "110", "messagesDeleted": [
                    {"message": {"id": "message-0", "threadId": "t0"}}
                ]},
                {"id": "114", "messagesAdded": [
                    {"message": {"id": "message-2", "threadId": "t2"}}
                ]},
            ],
        }

    preview = await reconcile_gmail_snapshot_preview(
        scope=scope, vault=vault, progress=progress,
        pages=pages, refs=refs, fetch_history_page=history,
        reauthorize=lambda: authorizations.append(True),
    )
    assert fetch_calls == [("100", None)]
    assert len(authorizations) == 4
    assert preview.message_count == 2
    assert preview.history_record_count == 2
    assert preview.next_history_id == "115"
    assert preview.durable_provider_checkpoint is False
    assert preview.follow_up_eligible is False
    assert preview.reply_absence_proven is False
    assert preview.outbound_action_approved is False
    assert "message-2" not in repr(preview)


@pytest.mark.asyncio
async def test_expired_gmail_anchor_blocks_reconciliation():
    scope, vault, progress, pages, refs = fixture()

    async def expired(start, page):
        raise GmailHistoryExpired("source history expired")

    with pytest.raises(GmailHistoryExpired):
        await reconcile_gmail_snapshot_preview(
            scope=scope, vault=vault, progress=progress,
            pages=pages, refs=refs, fetch_history_page=expired,
            reauthorize=lambda: None,
        )


@pytest.mark.asyncio
async def test_revoked_employee_consent_prevents_history_read():
    scope, vault, progress, pages, refs = fixture()
    calls = []

    async def history(start, page):
        calls.append(True)
        return {"historyId": "115"}

    def revoke():
        raise RuntimeError("consent revoked")

    with pytest.raises(CheckpointUnavailable):
        await reconcile_gmail_snapshot_preview(
            scope=scope, vault=vault, progress=progress,
            pages=pages, refs=refs, fetch_history_page=history,
            reauthorize=revoke,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_revocation_after_provider_read_rejects_preview():
    scope, vault, progress, pages, refs = fixture()
    checks = [0]

    async def history(start, page):
        return {"historyId": "115", "history": []}

    def check():
        checks[0] += 1
        if checks[0] == 3:
            raise RuntimeError("revoked after read")

    with pytest.raises(ReplayUnavailable):
        await reconcile_gmail_snapshot_preview(
            scope=scope, vault=vault, progress=progress,
            pages=pages, refs=refs, fetch_history_page=history,
            reauthorize=check,
        )
    assert checks == [3]


@pytest.mark.asyncio
async def test_incomplete_history_page_blocks_preview():
    scope, vault, progress, pages, refs = fixture()

    async def incomplete(start, page):
        return {
            "historyId": "115",
            "history": [{"id": "110"}],
            "nextPageToken": "more",
        }

    with pytest.raises(ReplayUnavailable):
        await reconcile_gmail_snapshot_preview(
            scope=scope, vault=vault, progress=progress,
            pages=pages, refs=refs, fetch_history_page=incomplete,
            reauthorize=lambda: None,
        )


def test_same_message_in_two_employees_cannot_match_hmac():
    scope, vault, progress, pages, refs = fixture()
    other = MailboxScope(
        company_id=scope.company_id,
        employee_id="33333333-3333-4333-8333-333333333333",
        provider="gmail",
        provider_identity_ref=scope.provider_identity_ref,
        resource_id=scope.resource_id,
        mapping_revision=scope.mapping_revision,
    )
    match = vault.digest(
        "snapshot-message-v1", scope.company_id, scope.employee_id,
        scope.provider_identity_ref, scope.resource_id, "message-0",
    )
    different = vault.digest(
        "snapshot-message-v1", other.company_id, other.employee_id,
        other.provider_identity_ref, other.resource_id, "message-0",
    )
    assert match != different
    with pytest.raises(CheckpointUnavailable):
        verify_snapshot_ledger(
            scope=other, vault=vault, progress=progress, pages=pages, refs=refs,
        )


@pytest.mark.asyncio
async def test_contradictory_added_deleted_transitions_are_not_guessed():
    scope, vault, progress, pages, refs = fixture()

    async def ambiguous(start, page):
        return {
            "historyId": "116",
            "history": [
                {"id": "110", "messagesAdded": [
                    {"message": {"id": "message-7", "threadId": "thread-7"}}
                ]},
                {"id": "115", "messagesDeleted": [
                    {"message": {"id": "message-7", "threadId": "thread-7"}}
                ]},
            ],
        }

    with pytest.raises(ReplayUnavailable, match="Ambiguous Gmail"):
        await reconcile_gmail_snapshot_preview(
            scope=scope, vault=vault, progress=progress,
            pages=pages, refs=refs, fetch_history_page=ambiguous,
            reauthorize=lambda: None,
        )
