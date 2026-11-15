"""Stage 11: Gmail bootstrap transport never fetches bodies or another account."""
from dataclasses import dataclass

import pytest

from app.intelligence.lola.authorized_provider_reads import (
    AuthorizedReadUnavailable, GmailBootstrapTransport,
    binding_for_resolved_case,
)
from app.intelligence.lola.continuity import WorkScope
from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"


def case(revision="verified"):
    return ResolvedCrossChannelCase(
        scope=WorkScope(COMPANY, EMPLOYEE, "CONTACT", "WORK", revision, True),
        email_provider_identity_ref="private-oauth-identity",
        whatsapp_provider_identity_ref="private-whatsapp-identity",
        email_contact_ref="private-email-contact",
        whatsapp_contact_ref="private-whatsapp-contact",
        source_fingerprint=revision,
    )


@dataclass
class Response:
    value: object
    def execute(self):
        return self.value


class Messages:
    def __init__(self):
        self.calls = []
    def list(self, **params):
        self.calls.append(("list", params))
        return Response({"messages": [{"id": "private-message", "threadId": "private-thread"}]})
    def get(self, **params):
        self.calls.append(("get", params))
        return Response({"id": params["id"], "threadId": "private-thread", "historyId": "1000"})


class Users:
    def __init__(self, principal="employee@private.invalid"):
        self.principal = principal
        self.messages_api = Messages()
        self.profiles = 0
    def getProfile(self, **params):
        assert params == {"userId": "me"}
        self.profiles += 1
        return Response({"emailAddress": self.principal, "historyId": "1000"})
    def messages(self):
        return self.messages_api


class Connector:
    def __init__(self, principal="employee@private.invalid"):
        self.fake_users = Users(principal)
    def _get_service(self):
        return self
    def users(self):
        return self.fake_users


def adapter(connector, reauthorize):
    return GmailBootstrapTransport(
        binding=binding_for_resolved_case(
            case(), provider="gmail",
            resource_id="employee@private.invalid",
            expected_principal="employee@private.invalid",
        ),
        connector=connector, reauthorize=reauthorize,
    )


@pytest.mark.asyncio
async def test_metadata_only_bootstrap_calls_no_body_or_send_operations():
    connector = Connector()
    reads = []
    t = adapter(connector, lambda: reads.append(1) or case())
    profile = await t.fetch_profile()
    refs = await t.fetch_messages_page(None)
    msg = await t.fetch_message_metadata("private-message")
    assert profile["historyId"] == "1000"
    assert refs["messages"][0]["id"] == msg["id"]
    assert len(reads) == 6
    assert connector.fake_users.profiles == 3
    assert connector.fake_users.messages_api.calls == [
        ("list", {"userId": "me", "maxResults": 100}),
        ("get", {
            "userId": "me", "id": "private-message",
            "format": "metadata", "fields": "id,threadId,historyId",
        }),
    ]


@pytest.mark.asyncio
async def test_gmail_page_token_not_gmail_query_or_message_body():
    connector = Connector()
    t = adapter(connector, case)
    # The callable must return the same already-resolved identity.
    await t.fetch_messages_page("opaque-page")
    assert connector.fake_users.messages_api.calls[0][1] == {
        "userId": "me", "maxResults": 100, "pageToken": "opaque-page",
    }


@pytest.mark.asyncio
async def test_gmail_wrong_principal_does_not_list_messages():
    connector = Connector(principal="other-employee@private.invalid")
    t = adapter(connector, case)
    with pytest.raises(AuthorizedReadUnavailable):
        await t.fetch_messages_page(None)
    assert connector.fake_users.messages_api.calls == []


@pytest.mark.asyncio
async def test_consent_revoked_before_metadata_fetch_blocks_provider_call():
    connector = Connector()
    t = adapter(connector, lambda: case("new-revision"))
    with pytest.raises(AuthorizedReadUnavailable):
        await t.fetch_message_metadata("private-message")
    assert connector.fake_users.profiles == 0
    assert connector.fake_users.messages_api.calls == []


@pytest.mark.asyncio
async def test_unexpected_get_failure_never_leaks_private_api_exception():
    connector = Connector()
    def broken(**params):
        raise RuntimeError("private OAuth token and message body")
    connector.fake_users.messages_api.get = broken
    t = adapter(connector, case)
    with pytest.raises(AuthorizedReadUnavailable) as exc:
        await t.fetch_message_metadata("private-message")
    assert "private" not in str(exc.value).lower() or "private oauth" not in str(exc.value).lower()


@pytest.mark.asyncio
async def test_invalid_message_id_does_not_fetch_message():
    connector = Connector()
    t = adapter(connector, case)
    with pytest.raises(AuthorizedReadUnavailable):
        await t.fetch_message_metadata("")
    assert connector.fake_users.messages_api.calls == []
