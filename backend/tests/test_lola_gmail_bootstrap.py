"""Stage 11 Gmail full snapshot/history reconciliation: synthetic no-network tests."""
import pytest

from app.intelligence.lola.gmail_bootstrap import (
    GmailBootstrapEvidence, collect_gmail_bootstrap,
)
from app.intelligence.lola.incremental_replay import (
    GmailHistoryExpired, ReplayUnavailable,
)


def fixtures(*, history_404=False):
    calls = []
    async def profile():
        calls.append(("profile",))
        return {"emailAddress": "employee@private.invalid", "historyId": "800"}
    async def list_page(token):
        calls.append(("list", token))
        if token is None:
            return {"messages": [{"id": "PRIVATE-msg-1", "threadId": "t1"}],
                    "nextPageToken": "next-page"}
        assert token == "next-page"
        return {"messages": [{"id": "PRIVATE-msg-2", "threadId": "t2"}]}
    async def metadata(message_id):
        calls.append(("get", message_id))
        return {"id": message_id, "threadId": "t1" if message_id.endswith("1") else "t2",
                "historyId": "800"}
    async def history(anchor, token):
        calls.append(("history", anchor, token))
        assert anchor == "800" and token is None
        if history_404:
            raise GmailHistoryExpired("Synthetic expired anchor")
        return {"historyId": "850", "history": [
            {"id": "840", "messagesAdded": [
                {"message": {"id": "PRIVATE-msg-3", "threadId": "t3"}}
            ]}
        ]}
    return profile, list_page, metadata, history, calls


@pytest.mark.asyncio
async def test_snapshot_and_concurrent_change_reconcile_before_baseline():
    profile, list_page, metadata, history, calls = fixtures()
    evidence = await collect_gmail_bootstrap(
        fetch_profile=profile, fetch_messages_page=list_page,
        fetch_message_metadata=metadata, fetch_history_page=history,
    )
    assert isinstance(evidence, GmailBootstrapEvidence)
    assert evidence.next_history_id == "850"
    assert evidence.message_count == 2
    assert evidence.history_record_count == 1
    assert evidence.page_count == 3  # 2 snapshot pages, 1 history page
    assert [x.operation for x in evidence.changed_records] == ["added"] * 3
    assert calls[0] == ("profile",)
    assert calls[-1] == ("history", "800", None)
    assert evidence.initial_baseline is True
    assert evidence.exhausted is True
    assert evidence.checkpoint_persisted is False
    assert evidence.complete_mailbox_history is False
    assert evidence.reply_absence_proven is False
    assert evidence.outbound_action_approved is False
    assert "PRIVATE-msg" not in repr(evidence)


@pytest.mark.asyncio
async def test_bootstrap_with_empty_mailbox_and_no_changes_is_not_no_reply():
    async def profile():
        return {"historyId": "500"}
    async def listing(token):
        assert token is None
        return {"resultSizeEstimate": 0}
    async def forbidden(message_id):
        pytest.fail("Empty mailbox cannot trigger metadata.get")
    async def history(anchor, token):
        assert anchor == "500" and token is None
        return {"historyId": "510"}
    evidence = await collect_gmail_bootstrap(
        fetch_profile=profile, fetch_messages_page=listing,
        fetch_message_metadata=forbidden, fetch_history_page=history,
    )
    assert evidence.changed_records == ()
    assert evidence.next_history_id == "510"
    assert evidence.complete_mailbox_history is False
    assert evidence.reply_absence_proven is False


@pytest.mark.asyncio
async def test_expired_start_anchor_rolls_back_full_bootstrap_evidence():
    profile, listing, metadata, history, calls = fixtures(history_404=True)
    with pytest.raises(GmailHistoryExpired):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=listing,
            fetch_message_metadata=metadata, fetch_history_page=history,
        )


@pytest.mark.asyncio
async def test_duplicate_mailbox_message_ref_never_becomes_baseline():
    profile, listing, metadata, history, calls = fixtures()
    async def duplicates(page):
        return {"messages": [{"id": "PRIVATE-msg-1"}, {"id": "PRIVATE-msg-1"}]}
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=duplicates,
            fetch_message_metadata=metadata, fetch_history_page=history,
        )
    assert not any(x[0] == "history" for x in calls)


@pytest.mark.asyncio
async def test_snapshot_page_cursor_cycle_rejected_before_checkpoint():
    profile, listing, metadata, history, calls = fixtures()
    async def loop(page):
        return {"messages": [{"id": f"new-{page}"}],
                "nextPageToken": "loop"}
    async def get(message_id):
        return {"id": message_id, "historyId": "800"}
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=loop,
            fetch_message_metadata=get, fetch_history_page=history,
        )


@pytest.mark.asyncio
async def test_snapshot_missing_list_results_does_not_assume_empty_mailbox():
    profile, listing, metadata, history, calls = fixtures()
    async def missing(page):
        return {"nextPageToken": None}
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=missing,
            fetch_message_metadata=metadata, fetch_history_page=history,
        )


@pytest.mark.asyncio
async def test_metadata_gone_during_snapshot_is_incomplete():
    profile, listing, metadata, history, calls = fixtures()
    async def deleted(message_id):
        raise RuntimeError("Private provider API response, do not log")
    with pytest.raises(ReplayUnavailable) as exc:
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=listing,
            fetch_message_metadata=deleted, fetch_history_page=history,
        )
    assert "Private" not in str(exc.value)


@pytest.mark.asyncio
async def test_source_metadata_thread_mismatch_is_rejected():
    profile, listing, metadata, history, calls = fixtures()
    async def wrong(message_id):
        return {"id": message_id, "historyId": "800", "threadId": "changed"}
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=listing,
            fetch_message_metadata=wrong, fetch_history_page=history,
        )


@pytest.mark.asyncio
async def test_oversized_mailbox_fails_closed_not_partial_success():
    profile, listing, metadata, history, calls = fixtures()
    async def oversized(token):
        return {"messages": [
            {"id": f"message-{i}", "threadId": "t"} for i in range(101)
        ]}
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=oversized,
            fetch_message_metadata=metadata, fetch_history_page=history,
        )


@pytest.mark.asyncio
async def test_oversized_initial_history_change_batch_rejected():
    profile, listing, metadata, history, calls = fixtures()
    async def many_history(anchor, token):
        return {"historyId": "900", "history": [
            {"id": str(801+i), "messagesAdded": [
                {"message": {"id": f"change-{i}-{j}"}} for j in range(6)
            ]} for i in range(100)
        ]}
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=profile, fetch_messages_page=listing,
            fetch_message_metadata=metadata, fetch_history_page=many_history,
        )


@pytest.mark.asyncio
async def test_without_authenticated_fetchers_rejects_before_any_result():
    with pytest.raises(ReplayUnavailable):
        await collect_gmail_bootstrap(
            fetch_profile=None, fetch_messages_page=None,
            fetch_message_metadata=None, fetch_history_page=None,
        )
