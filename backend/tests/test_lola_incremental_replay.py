"""Synthetic, no-credential Gmail history and Microsoft Graph delta replay tests."""
from datetime import datetime
from urllib.parse import quote

import pytest

from app.intelligence.lola.incremental_replay import (
    GmailHistoryExpired, ReplayUnavailable,
    replay_gmail_history, replay_graph_folder_delta,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-ABC/messages/delta"
DELTA = GRAPH_BASE + "?$deltatoken=opaque-final"
NEXT = GRAPH_BASE + "?$skiptoken=opaque-next"


@pytest.mark.asyncio
async def test_gmail_history_exhausted_noncontiguous_ids_and_safe_result():
    calls = []
    async def reader(start, page):
        calls.append((start, page))
        if page is None:
            return {
                "historyId": "900",
                "history": [{"id": "810", "messagesAdded": [
                    {"message": {"id": "private-a", "threadId": "private-t"}}
                ]}],
                "nextPageToken": "page2",
            }
        if page == "page2":
            return {
                "historyId": "950",
                "history": [{"id": "940", "messagesDeleted": [
                    {"message": {"id": "private-b", "threadId": "private-t"}}
                ]}],
            }
        raise AssertionError("unexpected cursor")
    result = await replay_gmail_history(start_history_id="800", fetch_page=reader)
    assert calls == [("800", None), ("800", "page2")]
    assert result.next_history_id == "950"
    assert result.page_count == 2 and result.record_count == 2
    assert len(result.changed_records) == 2
    assert result.changed_records[0].operation == "added"
    assert result.changed_records[1].operation == "deleted"
    assert result.checkpoint_persisted is False
    assert result.reply_absence_proven is False
    assert len(result.digest) == 64
    assert "private-a" not in repr(result)
    assert "private-b" not in repr(result)


@pytest.mark.asyncio
async def test_gmail_no_changes_is_not_evidence_of_reply_absence():
    async def reader(start, page):
        assert page is None
        return {"historyId": "1234"}
    result = await replay_gmail_history(start_history_id="1200", fetch_page=reader)
    assert result.changed_records == ()
    assert result.next_history_id == "1234"
    assert result.checkpoint_persisted is False
    assert result.reply_absence_proven is False


@pytest.mark.asyncio
async def test_gmail_history_cursor_expired_requires_resync_not_silent_advancement():
    async def reader(start, page):
        raise GmailHistoryExpired("known 404 (test fixture)")
    with pytest.raises(GmailHistoryExpired):
        await replay_gmail_history(start_history_id="100", fetch_page=reader)


@pytest.mark.parametrize("bad", ["", "0", "not-integer", "12.2", "-5", "1\ninjected"])
@pytest.mark.asyncio
async def test_gmail_rejects_invalid_start_cursors(bad):
    async def no_read(start, page):
        pytest.fail("must not contact Gmail")
    with pytest.raises(ReplayUnavailable):
        await replay_gmail_history(start_history_id=bad, fetch_page=no_read)


@pytest.mark.parametrize("response", [
    {},
    {"historyId": "99", "history": {}},
    {"historyId": "99", "history": [{"id": "99", "messagesAdded": "bad"}]},
    {"historyId": "99", "history": [{"id": "101"}]},
    {"historyId": "99", "history": [{"id": "90", "messagesAdded": [{"message": {}}]}]},
    {"historyId": "99", "history": [{"id": "90"}, {"id": "90"}]},
    {"historyId": "99", "nextPageToken": "cursor"},
    {"historyId": "99", "history": [{"id": "90"}], "nextPageToken": "cursor"},
])
@pytest.mark.asyncio
async def test_gmail_replay_malformed_or_nonterminal_cursors_fail_closed(response):
    async def read(start, page):
        return response
    with pytest.raises(ReplayUnavailable):
        await replay_gmail_history(start_history_id="80", fetch_page=read)


@pytest.mark.asyncio
async def test_gmail_network_error_is_not_successful_sync():
    async def error(start, page):
        raise RuntimeError("secret OAuth token in a provider exception")
    with pytest.raises(ReplayUnavailable) as exc:
        await replay_gmail_history(start_history_id="80", fetch_page=error)
    assert "OAuth" not in str(exc.value)


@pytest.mark.asyncio
async def test_gmail_history_watermark_regression_fails():
    async def reader(start, page):
        if page is None:
            return {"historyId": "400", "history": [{"id": "350"}], "nextPageToken": "2"}
        return {"historyId": "399", "history": [{"id": "375"}]}
    with pytest.raises(ReplayUnavailable):
        await replay_gmail_history(start_history_id="300", fetch_page=reader)


@pytest.mark.asyncio
async def test_graph_delta_cursor_round_trip_per_folder():
    calls = []
    async def reader(url):
        calls.append(url)
        if url == NEXT:
            return {"value": [
                {"id": "private-graph-b", "@removed": {"reason": "deleted"}}
            ], "@odata.deltaLink": DELTA}
        if "$select=id,conversationId" in url:
            return {"value": [
                {"id": "private-graph-a", "conversationId": "PRIVATE-CONVERSATION"}
            ], "@odata.nextLink": NEXT}
        raise AssertionError("unexpected URL")
    result = await replay_graph_folder_delta(
        verified_folder_id="FOLDER-ABC", fetch_url=reader,
    )
    assert len(calls) == 2
    assert result.next_delta_url == DELTA
    assert result.record_count == 2 and result.page_count == 2
    assert result.initial_baseline is True
    assert result.changed_records[1].operation == "deleted"
    assert result.checkpoint_persisted is False
    assert result.reply_absence_proven is False
    assert "private-graph-a" not in repr(result)
    assert "PRIVATE-CONVERSATION" not in repr(result)


@pytest.mark.asyncio
async def test_graph_incremental_from_persisted_delta_token_not_baseline():
    previous = GRAPH_BASE + "?$deltatoken=prior"
    async def reader(url):
        assert url == previous
        return {"value": [], "@odata.deltaLink": DELTA}
    result = await replay_graph_folder_delta(
        verified_folder_id="FOLDER-ABC", previous_delta_url=previous,
        fetch_url=reader,
    )
    assert result.initial_baseline is False
    assert result.record_count == 0
    assert result.checkpoint_persisted is False
    assert result.reply_absence_proven is False


@pytest.mark.asyncio
async def test_graph_accepts_microsoft_parenthesized_folder_path():
    final = ("https://graph.microsoft.com/v1.0/me/"
             "mailfolders('FOLDER-ABC')/messages/delta?$deltatoken=new")
    async def reader(url):
        return {"value": [], "@odata.deltaLink": final}
    result = await replay_graph_folder_delta(
        verified_folder_id="FOLDER-ABC", fetch_url=reader,
    )
    assert result.next_delta_url == final


@pytest.mark.parametrize("unsafe", [
    "http://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com.evil/v1.0/me/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com@evil/v1.0/me/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com:443/v1.0/me/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com/v1.0/me/mailFolders/OTHER/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com/v1.0/users/another/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-ABC/messages/delta",
    "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1#fragment",
    "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-ABC/messages/delta?$skiptoken=1\nleak:token",
])
@pytest.mark.asyncio
async def test_graph_rejects_unsafe_continuation_origins_and_wrong_folders(unsafe):
    async def no_read(url):
        pytest.fail("must reject continuation before authorized fetch")
    with pytest.raises(ReplayUnavailable):
        await replay_graph_folder_delta(
            verified_folder_id="FOLDER-ABC",
            fetch_url=no_read, previous_delta_url=unsafe,
        )


@pytest.mark.parametrize("payload", [
    {"value": []},
    {"value": [], "@odata.nextLink": NEXT, "@odata.deltaLink": DELTA},
    {"value": {}, "@odata.deltaLink": DELTA},
    {"value": [{"id": ""}], "@odata.deltaLink": DELTA},
    {"value": [{"id": "foo", "@removed": "not-an-object"}], "@odata.deltaLink": DELTA},
    {"value": [{"id": "foo", "conversationId": "private\nunsafe"}], "@odata.deltaLink": DELTA},
])
@pytest.mark.asyncio
async def test_graph_partial_or_malformed_responses_never_advance_cursor(payload):
    async def reader(url):
        return payload
    with pytest.raises(ReplayUnavailable):
        await replay_graph_folder_delta(
            verified_folder_id="FOLDER-ABC", fetch_url=reader,
        )


@pytest.mark.asyncio
async def test_graph_page_cycle_is_incomplete():
    async def reader(url):
        return {"value": [{"id": "msg-1"}], "@odata.nextLink": NEXT}
    with pytest.raises(ReplayUnavailable):
        await replay_graph_folder_delta(
            verified_folder_id="FOLDER-ABC", fetch_url=reader,
        )


@pytest.mark.asyncio
async def test_graph_provider_error_does_not_leak_tokens_or_claim_completeness():
    async def reader(url):
        raise RuntimeError("private-token" + url)
    with pytest.raises(ReplayUnavailable) as exc:
        await replay_graph_folder_delta(
            verified_folder_id="FOLDER-ABC", fetch_url=reader,
        )
    assert "private-token" not in str(exc.value)


@pytest.mark.asyncio
async def test_graph_repeated_same_message_change_is_not_mistaken_for_unique_events():
    async def reader(url):
        return {
            "value": [{"id": "private-a"}, {"id": "private-a"}],
            "@odata.deltaLink": DELTA,
        }
    with pytest.raises(ReplayUnavailable):
        await replay_graph_folder_delta(
            verified_folder_id="FOLDER-ABC", fetch_url=reader,
        )


@pytest.mark.asyncio
async def test_graph_unrelated_folder_ids_are_rejected_before_fetch():
    async def no_read(url):
        pytest.fail("must not request")
    with pytest.raises(ReplayUnavailable):
        await replay_graph_folder_delta(
            verified_folder_id="../other", fetch_url=no_read,
        )


@pytest.mark.asyncio
async def test_graph_terminal_token_is_required():
    async def reader(url):
        return {"value": [{"id": "msg-1"}], "@odata.nextLink": NEXT}
    with pytest.raises(ReplayUnavailable):
        await replay_graph_folder_delta(
            verified_folder_id="FOLDER-ABC", fetch_url=reader,
        )


@pytest.mark.asyncio
async def test_gmail_history_rows_must_increase_chronologically_across_pages():
    async def reader(start, page):
        if page is None:
            return {"historyId": "300", "history": [{"id": "290"}],
                    "nextPageToken": "page2"}
        # Later page cannot report a distinct but older record.
        return {"historyId": "330", "history": [{"id": "285"}]}
    with pytest.raises(ReplayUnavailable):
        await replay_gmail_history(start_history_id="200", fetch_page=reader)
