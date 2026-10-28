"""Deterministic mailbox identity and account-candidate contract tests.

These fixtures intentionally do not assert sender authentication or provider
acceptance. A From header can be forged and an email match is only a candidate.
"""

from dataclasses import replace

import pytest

from app.connectors.email_identity import (
    AccountEmailContact,
    identify_mailbox_message,
    match_sender_account_candidate,
)


SCOPE = {
    "tenant_id": "tenant-a",
    "source_connection_id": "connection-1",
    "provider": "gmail",
    "mailbox_id": "mailbox-1",
}


def _message(**overrides):
    return {
        "id": "provider-msg-1",
        "thread_id": "provider-thread-1",
        "message_id": "<internet-1@example.invalid>",
        "from": "Customer <CUSTOMER@example.invalid>",
        **overrides,
    }


def _identity(**overrides):
    return identify_mailbox_message(**SCOPE, message=_message(**overrides))


def test_replay_is_deterministic_and_message_id_is_not_provider_identity():
    original = _identity()
    replay = _identity(subject="Updated display text", snippet="Changed snippet")
    assert original.event_key == replay.event_key
    assert original.thread_key == replay.thread_key
    assert original.sender_email == "customer@example.invalid"
    assert original.internet_message_id == "<internet-1@example.invalid>"
    assert original.provider_message_id == "provider-msg-1"
    same_rfc_id = _identity(id="provider-msg-2")
    assert same_rfc_id.event_key != original.event_key
    assert same_rfc_id.thread_key == original.thread_key


@pytest.mark.parametrize("field,new_value", [
    ("tenant_id", "tenant-b"),
    ("source_connection_id", "connection-2"),
    ("provider", "outlook"),
    ("mailbox_id", "mailbox-2"),
])
def test_event_and_thread_keys_are_scoped_to_verified_source(field, new_value):
    baseline = _identity()
    different_scope = {**SCOPE, field: new_value}
    changed = identify_mailbox_message(**different_scope, message=_message())
    assert baseline.event_key != changed.event_key
    assert baseline.thread_key != changed.thread_key


def test_composite_key_cannot_collide_through_delimiter_injection():
    first = identify_mailbox_message(
        tenant_id="alpha|beta", source_connection_id="gamma", provider="gmail",
        mailbox_id="box", message=_message(),
    )
    second = identify_mailbox_message(
        tenant_id="alpha", source_connection_id="beta|gamma", provider="gmail",
        mailbox_id="box", message=_message(),
    )
    assert first.event_key != second.event_key


@pytest.mark.parametrize("field", ["tenant_id", "source_connection_id", "provider", "mailbox_id"])
@pytest.mark.parametrize("value", [None, "", "  ", 1])
def test_invalid_provenance_rejected(field, value):
    with pytest.raises(ValueError):
        identify_mailbox_message(**{**SCOPE, field: value}, message=_message())


@pytest.mark.parametrize("value", [None, "", "   ", 42])
def test_missing_source_message_id_rejected(value):
    with pytest.raises(ValueError, match="id"):
        _identity(id=value)


def test_no_thread_id_is_unknown_not_guessed_from_message_id():
    identity = _identity(thread_id=None)
    assert identity.thread_key is None
    assert identity.event_key


def test_provider_ids_are_case_sensitive_and_whitespace_trimmed():
    assert _identity(id=" id-a ").event_key == _identity(id="id-a").event_key
    assert _identity(id="Id-a").event_key != _identity(id="id-a").event_key


def test_invalid_sender_or_multiple_addresses_cannot_match():
    for raw in (
        "Alice <alice@example.invalid>, Bob <bob@example.invalid>",
        "not a mailbox", "bad\r\nBcc: other@example.invalid", "Alice <missing-address>",
    ):
        assert _identity(**{"from": raw}).sender_email is None


def test_outlook_explicit_from_email_preferred_over_display_name():
    identity = identify_mailbox_message(
        **{**SCOPE, "provider": "outlook"},
        message=_message(**{"from": "Untrusted Display", "from_email": "Customer@Example.Invalid"}),
    )
    assert identity.sender_email == "customer@example.invalid"


def _contacts(*addresses):
    return [AccountEmailContact("tenant-a", f"account-{i}", email)
            for i, email in enumerate(addresses, 1)]


def test_exact_unique_sender_only_produces_unverified_candidate():
    result = match_sender_account_candidate(
        _identity(), _contacts("CUSTOMER@example.invalid", "other@example.invalid"),
        permitted_account_ids={"account-1", "account-2"},
    )
    assert result.status == "candidate"
    assert result.account_id == "account-1"
    assert result.attribution_verified is False
    assert result.evidence == "sender_address_exact_match_unverified"


def test_ambiguous_account_email_never_selects_arbitrary_customer():
    result = match_sender_account_candidate(
        _identity(), _contacts("customer@example.invalid", "customer@example.invalid"),
        permitted_account_ids={"account-1", "account-2"},
    )
    assert result.status == "ambiguous"
    assert result.account_id is None


def test_duplicate_contact_rows_for_same_account_do_not_create_ambiguity():
    contacts = [AccountEmailContact("tenant-a", "same", "customer@example.invalid"),
                AccountEmailContact("tenant-a", "same", "CUSTOMER@example.invalid")]
    result = match_sender_account_candidate(
        _identity(), contacts, permitted_account_ids={"same"},
    )
    assert result.status == "candidate"
    assert result.account_id == "same"


def test_other_tenants_or_unpermitted_accounts_are_not_disclosed():
    contacts = [AccountEmailContact("tenant-b", "foreign", "customer@example.invalid"),
                AccountEmailContact("tenant-a", "private", "customer@example.invalid")]
    result = match_sender_account_candidate(
        _identity(), contacts, permitted_account_ids={"allowed"},
    )
    assert result.status == "unmatched"
    assert result.account_id is None


def test_missing_authorization_context_and_invalid_sender_fail_closed():
    contacts = _contacts("customer@example.invalid")
    assert match_sender_account_candidate(
        _identity(), contacts, permitted_account_ids=None,
    ).status == "unavailable"
    assert match_sender_account_candidate(
        _identity(**{"from": "invalid"}), contacts,
        permitted_account_ids={"account-1"},
    ).status == "invalid_sender"


def test_domain_similarity_and_display_name_do_not_trigger_matching():
    contacts = _contacts("sales@example.invalid")
    result = match_sender_account_candidate(
        _identity(**{"from": "Sales <other@example.invalid>"}), contacts,
        permitted_account_ids={"account-1"},
    )
    assert result.status == "unmatched"


def test_malformed_contact_rows_are_not_silently_used():
    with pytest.raises(ValueError, match="contact"):
        match_sender_account_candidate(
            _identity(), [AccountEmailContact("tenant-a", "", "customer@example.invalid")],
            permitted_account_ids={"account-1"},
        )


def test_contact_scope_is_explicit_and_cannot_be_reused_under_another_identity():
    foreign = replace(_identity(), tenant_id="tenant-b")
    result = match_sender_account_candidate(
        foreign, _contacts("customer@example.invalid"),
        permitted_account_ids={"account-1"},
    )
    assert result.status == "unmatched"
