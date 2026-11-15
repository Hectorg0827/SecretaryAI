"""Synthetic provider page-walk and durable change-checkpoint regression tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence.lola.source_proof import (
    DurableCheckpoint, EmailMessageRef, EmailPage, SourceProofUnavailable,
    parse_gmail_page, parse_graph_page, verify_email_source, walk_digest,
)

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
COMPANY = "company-A"
EMPLOYEE = "employee-A"
ACCOUNT = "verified-mailbox-A"
REVISION = "employee-mapping-rev-3"
GRAPH1 = "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=opaque-ABC-1"
GRAPH2 = "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=opaque-ABC-2"


def gmail_pages():
    return (
        parse_gmail_page(
            {"messages": [{"id": "gmail-1", "threadId": "gmail-thread-1"}],
             "nextPageToken": "page-two", "resultSizeEstimate": 20},
            requested_cursor=None, provider_authenticated=True, mailbox_identity_verified=True,
        ),
        parse_gmail_page(
            {"messages": [{"id": "gmail-2", "threadId": "gmail-thread-1"}],
             "resultSizeEstimate": 20},
            requested_cursor="page-two", provider_authenticated=True, mailbox_identity_verified=True,
        ),
    )


def graph_pages():
    return (
        parse_graph_page(
            {"value": [{"id": "graph-1", "conversationId": "conv-1"}],
             "@odata.nextLink": GRAPH1},
            requested_cursor=None, provider_authenticated=True, mailbox_identity_verified=True,
        ),
        parse_graph_page(
            {"value": [{"id": "graph-2", "conversationId": "conv-1"}],
             "@odata.nextLink": GRAPH2},
            requested_cursor=GRAPH1, provider_authenticated=True, mailbox_identity_verified=True,
        ),
        parse_graph_page(
            {"value": [{"id": "graph-3"}]},
            requested_cursor=GRAPH2, provider_authenticated=True, mailbox_identity_verified=True,
        ),
    )


def checkpoint(pages=None, *, provider="gmail"):
    pages = pages or (graph_pages() if provider == "outlook" else gmail_pages())
    return DurableCheckpoint(
        company_id=COMPANY, employee_id=EMPLOYEE,
        provider=provider, provider_identity_ref=ACCOUNT,
        mapping_revision=REVISION, source_revision="change-history-19",
        window_start=NOW - timedelta(days=10),
        checked_through=NOW - timedelta(seconds=30),
        replayed_through=NOW - timedelta(seconds=20),
        persisted_at=NOW - timedelta(seconds=10),
        page_walk_digest=walk_digest(pages, provider),
        replay_gap_free=True, replay_checkpoint_persisted=True,
        source_authorization_current=True,
    )


def verified(pages=None, *, provider="gmail", receipt=None):
    pages = pages or (graph_pages() if provider == "outlook" else gmail_pages())
    receipt = receipt or checkpoint(pages, provider=provider)
    return verify_email_source(
        company_id=COMPANY, employee_id=EMPLOYEE,
        provider=provider, provider_identity_ref=ACCOUNT,
        mapping_revision=REVISION, pages=pages, checkpoint=receipt,
        as_of=NOW,
    )


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_complete_pages_plus_gap_free_persisted_replay_is_bounded_source_proof(provider):
    result = verified(provider=provider)
    assert result.provider == provider
    assert result.page_count == (2 if provider == "gmail" else 3)
    assert result.message_count == (2 if provider == "gmail" else 3)
    assert result.checked_through == (NOW - timedelta(seconds=30)).isoformat()
    assert result.page_walk_exhausted is True
    assert result.change_replay_persisted is True
    assert len(result.source_digest) == 64
    assert result.provider_message_body_selected is False
    assert result.message_contact_verified is False
    assert result.cross_channel_case_verified is False
    assert result.follow_up_authorized is False
    assert result.external_writes_executed is False
    assert "gmail-1" not in str(result)
    assert "graph-1" not in str(result)


def test_gmail_result_size_estimate_is_not_proof_of_completeness():
    pages = gmail_pages()
    assert pages[0].next_cursor == "page-two"
    with pytest.raises(SourceProofUnavailable):
        walk_digest(pages[:1], "gmail")


def test_graph_nextlink_must_be_followed_exactly():
    pages = graph_pages()
    with pytest.raises(SourceProofUnavailable):
        walk_digest(pages[:2], "outlook")
    bad = replace(pages[1], requested_cursor=GRAPH2)
    with pytest.raises(SourceProofUnavailable):
        walk_digest((pages[0], bad, pages[2]), "outlook")


@pytest.mark.parametrize("malicious", [
    "http://graph.microsoft.com/v1.0/me/messages?$skip=1",
    "https://graph.microsoft.com.evil.org/v1.0/me/messages?$skip=1",
    "https://graph.microsoft.com@evil.org/v1.0/me/messages?$skip=1",
    "https://graph.microsoft.com/v1.0/users/other/messages?$skip=1",
    "https://graph.microsoft.com/v1.0/me/messages?$skip=1#frag",
    "https://graph.microsoft.com:443/v1.0/me/messages?$skip=1",
    "https://graph.microsoft.com/v1.0/me/messages",
    "https://graph.microsoft.com/v1.0/me/messages?x=1\nAuthorization:secret",
])
def test_graph_does_not_follow_untrusted_redirects_or_wrong_mailbox(malicious):
    with pytest.raises(SourceProofUnavailable):
        parse_graph_page(
            {"value": [{"id": "graph-1"}], "@odata.nextLink": malicious},
            requested_cursor=None, provider_authenticated=True, mailbox_identity_verified=True,
        )


def test_email_identity_or_oauth_scope_false_never_satisfies_proof():
    pages = gmail_pages()
    for bad in (
        replace(pages[0], authenticated_provider_request=False),
        replace(pages[0], mailbox_identity_verified=False),
    ):
        with pytest.raises(SourceProofUnavailable):
            walk_digest((bad, pages[1]), "gmail")


def test_duplicate_provider_event_identity_is_not_silently_deduplicated():
    pages = gmail_pages()
    duplicate = replace(pages[1], message_refs=pages[0].message_refs)
    with pytest.raises(SourceProofUnavailable):
        walk_digest((pages[0], duplicate), "gmail")


def test_cursor_cycle_is_rejected_even_if_message_ids_differ():
    pages = gmail_pages()
    cycle = replace(pages[1], next_cursor="page-two")
    with pytest.raises(SourceProofUnavailable):
        walk_digest((pages[0], cycle), "gmail")


def test_more_pages_after_exhausted_cursor_is_invalid():
    pages = gmail_pages()
    extra = replace(pages[1], requested_cursor=None, next_cursor=None)
    with pytest.raises(SourceProofUnavailable):
        walk_digest((pages[0], pages[1], extra), "gmail")


def test_gmail_missing_messages_with_nonzero_estimate_fails_closed():
    with pytest.raises(SourceProofUnavailable):
        parse_gmail_page(
            {"resultSizeEstimate": 23}, requested_cursor=None,
            provider_authenticated=True, mailbox_identity_verified=True,
        )


def test_empty_provider_result_only_valid_with_complete_replay():
    empty = parse_gmail_page(
        {"resultSizeEstimate": 0}, requested_cursor=None,
        provider_authenticated=True, mailbox_identity_verified=True,
    )
    result = verified((empty,), receipt=checkpoint((empty,)))
    assert result.message_count == 0
    assert result.follow_up_authorized is False


@pytest.mark.parametrize("updates", [
    {"replay_gap_free": False},
    {"replay_checkpoint_persisted": False},
    {"source_authorization_current": False},
    {"page_walk_digest": "unrelated-pages"},
    {"company_id": "other-tenant"},
    {"employee_id": "another-employee"},
    {"provider_identity_ref": "someone-else-mailbox"},
    {"mapping_revision": "stale-contact-link"},
    {"source_revision": ""},
])
def test_missing_or_wrong_durable_change_checkpoint_fails_closed(updates):
    receipt = replace(checkpoint(), **updates)
    with pytest.raises(SourceProofUnavailable):
        verified(receipt=receipt)


@pytest.mark.parametrize("updates", [
    {"checked_through": NOW - timedelta(hours=2)},
    {"replayed_through": NOW - timedelta(days=1)},
    {"persisted_at": NOW - timedelta(days=1)},
    {"persisted_at": NOW + timedelta(seconds=1)},
    {"replayed_through": NOW + timedelta(seconds=1)},
    {"checked_through": NOW + timedelta(seconds=1)},
    {"window_start": NOW - timedelta(days=70)},
    {"window_start": NOW + timedelta(seconds=1)},
    {"checked_through": datetime(2026, 11, 15, 11)},
])
def test_stale_future_gapped_or_naive_replay_cannot_certify_coverage(updates):
    receipt = replace(checkpoint(), **updates)
    with pytest.raises(SourceProofUnavailable):
        verified(receipt=receipt)


def test_change_to_message_ids_changes_evidence_digest():
    baseline = gmail_pages()
    changed = (
        baseline[0],
        replace(baseline[1], message_refs=(EmailMessageRef("gmail-different", "gmail-thread-1"),)),
    )
    assert walk_digest(changed, "gmail") != walk_digest(baseline, "gmail")
    with pytest.raises(SourceProofUnavailable):
        verified(changed, receipt=checkpoint(baseline))


def test_graph_delta_response_is_not_confused_with_a_paginated_list():
    with pytest.raises(SourceProofUnavailable):
        parse_graph_page(
            {"value": [], "@odata.deltaLink": GRAPH2},
            requested_cursor=None,
            provider_authenticated=True, mailbox_identity_verified=True,
        )


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_external_page_payload_cannot_claim_authorization_with_false_flags(provider):
    parser = parse_gmail_page if provider == "gmail" else parse_graph_page
    data = ({"messages": [{"id": "1"}]} if provider == "gmail"
            else {"value": [{"id": "1"}]})
    result = parser(
        data, requested_cursor=None,
        provider_authenticated=False, mailbox_identity_verified=False,
    )
    with pytest.raises(SourceProofUnavailable):
        walk_digest((result,), provider)


def test_unknown_message_metadata_cannot_create_false_received_message():
    with pytest.raises(SourceProofUnavailable):
        parse_graph_page(
            {"value": [{"id": ""}]},
            requested_cursor=None, provider_authenticated=True,
            mailbox_identity_verified=True,
        )


def test_provider_max_page_bound_fails_closed():
    empty_page = parse_gmail_page(
        {"resultSizeEstimate": 0}, requested_cursor=None,
        provider_authenticated=True, mailbox_identity_verified=True,
    )
    with pytest.raises(SourceProofUnavailable):
        walk_digest((empty_page,) * 101, "gmail")


def test_provider_max_message_bound_fails_closed():
    def make(i):
        return EmailMessageRef(str(i), None)
    first = EmailPage(
        "gmail", None, "next-cursor", tuple(make(i) for i in range(300)), True, True,
    )
    second = EmailPage(
        "gmail", "next-cursor", None, tuple(make(i) for i in range(300, 600)), True, True,
    )
    with pytest.raises(SourceProofUnavailable):
        walk_digest((first, second), "gmail")
