"""No-network replay acceptance for Gmail history and Graph per-folder delta.

Synthetic provider responses only. No credentials, hosted database writes,
live provider APIs, message bodies, follow-up recommendations or schedulers.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

from app.intelligence.lola.delta_replay import (
    DeltaCursorExpired, DeltaUnavailable, Scope, collect_gmail_history,
    collect_graph_delta,
)

NOW = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
GMAIL_SCOPE = Scope(
    company_id="tenant-X", employee_id="employee-X",
    provider_connection_ref="gmail-connection-17",
    provider_identity_ref="verified-email-account",
    mailbox_ref="mailbox-001",
    mapping_revision="rev-2", provider="gmail",
)
FOLDER = "AAKALAL=Inbox"
GRAPH_SCOPE = Scope(
    company_id="tenant-X", employee_id="employee-X",
    provider_connection_ref="outlook-connection-12",
    provider_identity_ref="verified-graph-account",
    mailbox_ref="mailbox-002",
    mapping_revision="rev-2", provider="outlook", folder_id=FOLDER,
)
GRAPH_ROOT = (
    "https://graph.microsoft.com/v1.0/me/mailFolders/"
    + quote(FOLDER, safe="")
    + "/messages/delta"
)
GRAPH_NEXT = GRAPH_ROOT + "?%24skiptoken=opaque%3Dabc"
GRAPH_DELTA = GRAPH_ROOT + "?%24deltatoken=opaque%3Ddone"


def gmail_pages():
    return (
        {
            "historyId": "1100",
            "history": [{
                "id": "1010", "messagesAdded": [
                    {"message": {"id": "gmail-msg-1", "threadId": "thread-A"}}
                ],
            }],
            "nextPageToken": "next-opaque",
        },
        {
            "historyId": "1120",
            "history": [
                {"id": "1110", "messagesDeleted": [
                    {"message": {"id": "gmail-msg-2", "threadId": "thread-B"}}
                ]},
                {"id": "1113", "labelsAdded": [
                    {"message": {"id": "gmail-msg-1", "threadId": "thread-A"}, "labelIds": ["IMPORTANT"]}
                ]},
            ],
        },
    )


def graph_pages():
    return (
        {"value": [{"id": "graph-msg-1", "conversationId": "graph-conv-A"}],
         "@odata.nextLink": GRAPH_NEXT},
        {"value": [
            {"id": "graph-msg-2", "@removed": {"reason": "deleted"}},
            {"id": "graph-msg-3", "conversationId": "graph-conv-B"},
        ], "@odata.deltaLink": GRAPH_DELTA},
    )


def do_gmail(pages=None, *, start="1000", scope=GMAIL_SCOPE):
    pages = pages or gmail_pages()
    calls = []
    async def read(history, cursor):
        calls.append((history, cursor))
        return pages[len(calls) - 1]
    candidate = asyncio.run(collect_gmail_history(
        scope=scope, start_history_id=start, read_page=read, as_of=NOW,
    ))
    return candidate, calls


def do_graph(pages=None, *, scope=GRAPH_SCOPE, previous=None):
    pages = pages or graph_pages()
    calls = []
    async def read(url):
        calls.append(url)
        return pages[len(calls) - 1]
    candidate = asyncio.run(collect_graph_delta(
        scope=scope, previous_delta_link=previous, read_page=read, as_of=NOW,
    ))
    return candidate, calls


def test_gmail_full_history_round_has_no_durable_or_followup_authority():
    record, calls = do_gmail()
    assert calls == [("1000", None), ("1000", "next-opaque")]
    assert record.previous_cursor == "1000"
    assert record.terminal_cursor == "1120"
    assert record.pages_processed == 2
    assert record.changes_processed == 3
    assert record.round_complete is True
    assert record.gap_free_from_prior_cursor is True
    assert record.checkpoint_persisted is False
    assert record.mailbox_history_certified_for_followup is False
    assert record.external_writes_executed is False
    assert len(record.source_digest) == 64
    assert [(event.kind, event.message_id) for event in record.changes] == [
        ("added", "gmail-msg-1"), ("deleted", "gmail-msg-2"),
        ("label_added", "gmail-msg-1"),
    ]
    assert "gmail-msg" not in repr(record)
    assert "mailbox-001" not in repr(record)


def test_gmail_initial_history_id_must_be_provider_checkpoint_not_zero():
    with pytest.raises(DeltaUnavailable):
        do_gmail(start="0")
    with pytest.raises(DeltaUnavailable):
        do_gmail(start="garbage")


def test_gmail_missing_history_cursor_404_does_not_advance_a_checkpoint():
    async def expired(_start, _cursor):
        raise DeltaCursorExpired("Provider 404")
    with pytest.raises(DeltaCursorExpired):
        asyncio.run(collect_gmail_history(
            scope=GMAIL_SCOPE, start_history_id="1000",
            read_page=expired, as_of=NOW,
        ))


def test_gmail_provider_exception_not_exposed():
    async def bad(_start, _cursor):
        raise RuntimeError("SECRET PRIVATE MESSAGE IN PROVIDER BODY")
    with pytest.raises(DeltaUnavailable) as exc:
        asyncio.run(collect_gmail_history(
            scope=GMAIL_SCOPE, start_history_id="1000",
            read_page=bad, as_of=NOW,
        ))
    assert "SECRET" not in str(exc.value)


def test_gmail_history_ids_increase_but_are_not_consecutive():
    record, _ = do_gmail()
    assert record.changes[0].history_id == "1010"
    assert record.changes[-1].history_id == "1113"


@pytest.mark.parametrize("mutator", [
    lambda pages: pages[1].update({"historyId": "900"}),
    lambda pages: pages[1].update({"historyId": "not-an-id"}),
    lambda pages: pages[1]["history"][0].update({"id": "1010"}),
    lambda pages: pages[0]["history"][0].update({"id": "2000"}),
    lambda pages: pages[1]["history"][0].update({"messagesDeleted": [None]}),
    lambda pages: pages[1].update({"history": "not-a-list"}),
])
def test_gmail_malformed_or_regressed_history_fails_closed(mutator):
    from copy import deepcopy
    pages = deepcopy(gmail_pages())
    mutator(pages)
    with pytest.raises(DeltaUnavailable):
        do_gmail(pages)


def test_gmail_empty_middle_page_is_permitted_only_with_complete_cursor_walk():
    pages = (
        {"historyId": "1000", "history": [], "nextPageToken": "first"},
        {"historyId": "1000", "history": [], "nextPageToken": "second"},
        {"historyId": "1000", "history": []},
    )
    record, calls = do_gmail(pages)
    assert len(calls) == 3
    assert record.changes_processed == 0
    assert record.checkpoint_persisted is False


def test_gmail_next_token_cycle_is_not_trusted():
    pages = (
        {"historyId": "1000", "history": [], "nextPageToken": "first"},
        {"historyId": "1000", "history": [], "nextPageToken": "first"},
    )
    with pytest.raises(DeltaUnavailable):
        do_gmail(pages)


def test_gmail_wrong_mailbox_provider_never_executes_reader():
    calls = []
    async def forbidden(_history, _cursor):
        calls.append(1)
        raise AssertionError("Should not make request")
    with pytest.raises(DeltaUnavailable):
        asyncio.run(collect_gmail_history(
            scope=GRAPH_SCOPE, start_history_id="1000",
            read_page=forbidden, as_of=NOW,
        ))
    assert calls == []


def test_gmail_source_changes_affect_fingerprint():
    from copy import deepcopy
    first, _ = do_gmail()
    changed = deepcopy(gmail_pages())
    changed[1]["history"][0]["messagesDeleted"][0]["message"]["id"] = "different"
    new, _ = do_gmail(changed)
    assert new.source_digest != first.source_digest


def test_graph_delta_round_requires_terminal_delta_link():
    record, calls = do_graph()
    assert calls == [GRAPH_ROOT + "?$select=id,conversationId", GRAPH_NEXT]
    assert record.previous_cursor == ""
    assert record.terminal_cursor == GRAPH_DELTA
    assert record.pages_processed == 2
    assert record.changes_processed == 3
    assert [change.kind for change in record.changes] == [
        "updated_or_added", "deleted", "updated_or_added",
    ]
    assert record.checkpoint_persisted is False
    assert record.mailbox_history_certified_for_followup is False
    assert "graph-msg-1" not in repr(record)


def test_graph_incremental_round_reuses_exact_saved_delta_token():
    candidate, calls = do_graph(
        pages=({"value": [], "@odata.deltaLink": GRAPH_DELTA},),
        previous=GRAPH_DELTA,
    )
    assert calls == [GRAPH_DELTA]
    assert candidate.previous_cursor == GRAPH_DELTA
    assert candidate.changes_processed == 0
    assert candidate.round_complete is True


def test_graph_other_folder_is_rejected_before_provider_call():
    wrong_folder = "https://graph.microsoft.com/v1.0/me/mailFolders/other/messages/delta?$deltatoken=X"
    called = []
    async def reader(url):
        called.append(url)
        return {"value": [], "@odata.deltaLink": GRAPH_DELTA}
    with pytest.raises(DeltaUnavailable):
        asyncio.run(collect_graph_delta(
            scope=GRAPH_SCOPE, previous_delta_link=wrong_folder,
            read_page=reader, as_of=NOW,
        ))
    assert called == []


@pytest.mark.parametrize("url", [
    "http://graph.microsoft.com/v1.0/me/mailFolders/x/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com.evil.test/v1.0/me/mailFolders/x/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com:443/v1.0/me/mailFolders/x/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com@evil.test/v1.0/me/mailFolders/x/messages/delta?$skiptoken=1",
    "https://graph.microsoft.com/v1.0/me/mailFolders/x/messages/delta?$skiptoken=1#x",
    "https://graph.microsoft.com/v1.0/me/messages/delta?$skiptoken=x",
])
def test_graph_provider_continuation_is_not_safe_without_exact_scope(url):
    with pytest.raises(DeltaUnavailable):
        do_graph(({"value": [], "@odata.deltaLink": url},))


@pytest.mark.parametrize("bad", [
    {"value": [{"id": "x"}]},
    {"value": [{"id": "x"}], "@odata.nextLink": GRAPH_NEXT, "@odata.deltaLink": GRAPH_DELTA},
    {"value": "not-a-list", "@odata.deltaLink": GRAPH_DELTA},
    {"value": [{"id": ""}], "@odata.deltaLink": GRAPH_DELTA},
    {"value": [{"id": "x", "@removed": "deleted"}], "@odata.deltaLink": GRAPH_DELTA},
])
def test_graph_incomplete_or_malformed_round_is_not_committable(bad):
    with pytest.raises(DeltaUnavailable):
        do_graph((bad,))


def test_graph_empty_page_with_new_next_link_is_supported():
    record, calls = do_graph((
        {"value": [], "@odata.nextLink": GRAPH_NEXT},
        {"value": [], "@odata.deltaLink": GRAPH_DELTA},
    ))
    assert record.pages_processed == 2
    assert record.changes_processed == 0


def test_graph_cursor_cycle_prevents_checkpoint():
    with pytest.raises(DeltaUnavailable):
        do_graph((
            {"value": [], "@odata.nextLink": GRAPH_NEXT},
            {"value": [], "@odata.nextLink": GRAPH_NEXT},
        ))


def test_graph_provider_error_is_sanitized():
    async def fail(_url):
        raise RuntimeError("PROVIDER GRAPH EMAIL BODY EXCEPTION")
    with pytest.raises(DeltaUnavailable) as exc:
        asyncio.run(collect_graph_delta(
            scope=GRAPH_SCOPE, previous_delta_link=None,
            read_page=fail, as_of=NOW,
        ))
    assert "PROVIDER GRAPH" not in str(exc.value)


def test_graph_cannot_switch_to_gmail_reader():
    with pytest.raises(DeltaUnavailable):
        do_graph(scope=GMAIL_SCOPE)


def test_no_naive_evaluation_timestamp_allowed():
    with pytest.raises(DeltaUnavailable):
        asyncio.run(collect_gmail_history(
            scope=GMAIL_SCOPE, start_history_id="1000",
            read_page=lambda *_args: None, as_of=datetime(2026, 11, 15, 10),
        ))


@pytest.mark.parametrize("bad", ["", "newline\ncredential", "a"*257])
def test_provider_scope_invalid_identity_never_authorizes_read(bad):
    with pytest.raises(DeltaUnavailable):
        Scope(company_id=bad, employee_id="e", provider_connection_ref="c",
              provider_identity_ref="p", mailbox_ref="m",
              mapping_revision="r", provider="gmail")
