"""No-network authenticated Gmail and Graph transport privacy/scope tests."""
from dataclasses import replace

import httpx
import pytest

from app.intelligence.lola.authorized_provider_reads import (
    AuthorizedReadUnavailable, GmailHistoryTransport, GraphFolderDeltaTransport,
    binding_for_resolved_case,
)
from app.intelligence.lola.continuity import WorkScope
from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase
from app.intelligence.lola.incremental_replay import GmailHistoryExpired

COMPANY = "11111111-1111-4111-8111-111111111111"
EMPLOYEE = "22222222-2222-4222-8222-222222222222"
GRAPH = "https://graph.microsoft.com/v1.0/me/mailFolders/FOLDER-A/messages/delta?$skiptoken=opaque"


def resolved(revision="revision-v1", employee=EMPLOYEE):
    return ResolvedCrossChannelCase(
        scope=WorkScope(
            company_id=COMPANY, employee_id=employee,
            contact_id="PRIVATE-contact", work_item_id="PRIVATE-work",
            mapping_revision=revision, employee_consent_verified=True,
        ),
        email_provider_identity_ref="private-oauth-connection-ref",
        whatsapp_provider_identity_ref="private-whatsapp-connection-ref",
        email_contact_ref="PRIVATE-email-contact",
        whatsapp_contact_ref="PRIVATE-whatsapp-contact",
        source_fingerprint=revision,
    )


class Result:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self.data


class FakeGmailUsers:
    def __init__(self, principal="employee@private.invalid", status=None):
        self.principal = principal
        self.status = status
        self.history_calls = []

    def getProfile(self, **kwargs):
        assert kwargs == {"userId": "me"}
        return Result({"emailAddress": self.principal})

    def history(self):
        return self

    def list(self, **kwargs):
        self.history_calls.append(kwargs)
        return Result({"historyId": "955", "history": [{"id": "950"}]})


class FakeGmailConnector:
    def __init__(self, users):
        self._users = users

    def _get_service(self):
        return self

    def users(self):
        return self._users


class FakeGraphConnector:
    async def _get_headers(self):
        return {"Authorization": "Bearer only-synthetic-test-token", "Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_gmail_history_is_read_only_and_profile_bound_every_page():
    source = resolved()
    users = FakeGmailUsers()
    binding = binding_for_resolved_case(
        source, provider="gmail", resource_id="employee@private.invalid",
        expected_principal="employee@private.invalid",
    )
    events = []
    transport = GmailHistoryTransport(
        binding=binding, connector=FakeGmailConnector(users),
        reauthorize=lambda: events.append("authorized") or source,
    )
    result = await transport.fetch_page("900", "opaque-page-2")
    assert result["historyId"] == "955"
    assert users.history_calls == [{
        "userId": "me", "startHistoryId": "900",
        "maxResults": 100, "pageToken": "opaque-page-2",
    }]
    assert len(events) == 2


@pytest.mark.asyncio
async def test_gmail_profile_mismatch_never_fetches_private_history():
    source = resolved()
    users = FakeGmailUsers(principal="different-person@private.invalid")
    transport = GmailHistoryTransport(
        binding=binding_for_resolved_case(
            source, provider="gmail", resource_id="employee@private.invalid",
            expected_principal="employee@private.invalid",
        ),
        connector=FakeGmailConnector(users), reauthorize=lambda: source,
    )
    with pytest.raises(AuthorizedReadUnavailable):
        await transport.fetch_page("900", None)
    assert users.history_calls == []


@pytest.mark.asyncio
async def test_gmail_consent_revocation_before_first_page_rejects_request():
    source = resolved()
    users = FakeGmailUsers()
    transport = GmailHistoryTransport(
        binding=binding_for_resolved_case(
            source, provider="gmail", resource_id="employee@private.invalid",
            expected_principal="employee@private.invalid",
        ),
        connector=FakeGmailConnector(users),
        reauthorize=lambda: resolved(revision="revoked-revision"),
    )
    with pytest.raises(AuthorizedReadUnavailable):
        await transport.fetch_page("900", None)
    assert users.history_calls == []


@pytest.mark.asyncio
async def test_graph_authenticates_user_before_reading_employee_folder():
    source = resolved()
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "graph-identity-1"})
        assert str(request.url) == GRAPH
        return httpx.Response(
            200, json={"value": [{"id": "private-graph-id"}],
                       "@odata.deltaLink": GRAPH.replace("$skiptoken", "$deltatoken")},
        )
    transport = GraphFolderDeltaTransport(
        binding=binding_for_resolved_case(
            source, provider="outlook", resource_id="FOLDER-A",
            expected_principal="graph-identity-1",
        ),
        connector=FakeGraphConnector(), reauthorize=lambda: source,
        http_transport=httpx.MockTransport(handler),
    )
    result = await transport.fetch_url(GRAPH)
    assert result["value"][0]["id"] == "private-graph-id"
    assert len(seen) == 2
    assert "/me?" in seen[0]
    assert "/mailFolders/FOLDER-A/messages/delta" in seen[1]


@pytest.mark.asyncio
async def test_graph_wrong_oauth_principal_never_fetches_private_folder():
    source = resolved()
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"id": "someone-else"})
    transport = GraphFolderDeltaTransport(
        binding=binding_for_resolved_case(
            source, provider="outlook", resource_id="FOLDER-A",
            expected_principal="graph-identity-1",
        ),
        connector=FakeGraphConnector(), reauthorize=lambda: source,
        http_transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AuthorizedReadUnavailable):
        await transport.fetch_url(GRAPH)
    assert seen == ["/v1.0/me"]


@pytest.mark.asyncio
async def test_graph_wrong_folder_or_origin_fails_before_oauth_access():
    source = resolved()
    called = []
    def handler(request):
        called.append(True)
        return httpx.Response(200, json={})
    transport = GraphFolderDeltaTransport(
        binding=binding_for_resolved_case(
            source, provider="outlook", resource_id="FOLDER-A",
            expected_principal="graph-identity-1",
        ),
        connector=FakeGraphConnector(), reauthorize=lambda: source,
        http_transport=httpx.MockTransport(handler),
    )
    for bad in (
        GRAPH.replace("FOLDER-A", "OTHER-FOLDER"),
        GRAPH.replace("graph.microsoft.com", "evil.example"),
    ):
        with pytest.raises(AuthorizedReadUnavailable):
            await transport.fetch_url(bad)
    assert called == []


@pytest.mark.asyncio
async def test_graph_responding_with_redirect_does_not_leak_bearer_to_other_origin():
    source = resolved()
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "graph-identity-1"})
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    transport = GraphFolderDeltaTransport(
        binding=binding_for_resolved_case(
            source, provider="outlook", resource_id="FOLDER-A",
            expected_principal="graph-identity-1",
        ),
        connector=FakeGraphConnector(), reauthorize=lambda: source,
        http_transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AuthorizedReadUnavailable):
        await transport.fetch_url(GRAPH)
    assert len(seen) == 2
    assert all("evil.example" not in url for url in seen)


@pytest.mark.asyncio
async def test_graph_expired_delta_requires_bootstrap_and_does_not_hide_denial():
    source = resolved()
    def handler(request):
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "graph-identity-1"})
        return httpx.Response(410, json={"error": {"message": "do not log private body"}})
    transport = GraphFolderDeltaTransport(
        binding=binding_for_resolved_case(
            source, provider="outlook", resource_id="FOLDER-A",
            expected_principal="graph-identity-1",
        ),
        connector=FakeGraphConnector(), reauthorize=lambda: source,
        http_transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AuthorizedReadUnavailable) as exc:
        await transport.fetch_url(GRAPH)
    assert "do not log" not in str(exc.value)


def test_unverified_scope_refused_even_without_network():
    source = resolved()
    with pytest.raises(AuthorizedReadUnavailable):
        binding_for_resolved_case(
            source, provider="gmail", resource_id="employee@private.invalid",
            expected_principal="user\nInjected: header",
        )
