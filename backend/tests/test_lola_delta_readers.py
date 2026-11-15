"""Offline authorized-provider reader integration tests for Lola delta rounds."""
import asyncio
from types import SimpleNamespace

import pytest

from app.connectors.lola_delta_readers import (
    GmailHistoryReader, OutlookFolderDeltaReader,
)
from app.intelligence.lola.delta_replay import (
    DeltaCursorExpired, DeltaUnavailable,
    collect_gmail_history, collect_graph_delta,
)
from test_lola_delta_replay import (
    NOW, GMAIL_SCOPE, GRAPH_SCOPE, GRAPH_NEXT, GRAPH_DELTA,
    gmail_pages, graph_pages,
)


class GmailService:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []
    def users(self):
        return self
    def history(self):
        return self
    def list(self, **params):
        self.requests.append(params)
        return self
    def execute(self):
        if not self.pages:
            raise AssertionError("Unexpected Gmail network request")
        payload = self.pages.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


class FakeGmailConnector:
    def __init__(self, pages):
        self.service = GmailService(list(pages))
    def _get_service(self):
        return self.service


class FakeGraphClient:
    def __init__(self, responses, requests):
        self.responses, self.requests = responses, requests
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return None
    async def get(self, url, *, headers, timeout, follow_redirects):
        self.requests.append((url, dict(headers), follow_redirects))
        if not self.responses:
            raise AssertionError("Unexpected Graph network request")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeGraphConnector:
    def __init__(self):
        self.headers_calls = 0
    async def _get_headers(self):
        self.headers_calls += 1
        return {"Authorization": "Bearer FAKE-PRIVATE-TOKEN", "Content-Type": "application/json"}


class GraphResponse:
    def __init__(self, data, status_code=200):
        self.payload, self.status_code = data, status_code
    def json(self):
        return self.payload


async def allowed(_scope):
    return True


async def denied(_scope):
    return False


def test_gmail_reader_uses_approved_connector_history_api_only():
    connector = FakeGmailConnector(gmail_pages())
    authorizations = []
    async def verify(scope):
        authorizations.append(scope)
        return True
    reader = GmailHistoryReader(
        scope=GMAIL_SCOPE, connector=connector, current_authorization=verify,
    )
    result = asyncio.run(collect_gmail_history(
        scope=GMAIL_SCOPE, start_history_id="1000",
        read_page=reader, as_of=NOW,
    ))
    assert result.terminal_cursor == "1120"
    assert result.checkpoint_persisted is False
    assert len(connector.service.requests) == 2
    assert connector.service.requests[0] == {
        "userId": "me", "startHistoryId": "1000", "maxResults": 100,
    }
    assert connector.service.requests[1]["pageToken"] == "next-opaque"
    assert authorizations == [GMAIL_SCOPE, GMAIL_SCOPE]


def test_gmail_reader_denies_before_credential_use():
    connector = FakeGmailConnector(gmail_pages())
    reader = GmailHistoryReader(
        scope=GMAIL_SCOPE, connector=connector, current_authorization=denied,
    )
    with pytest.raises(DeltaUnavailable):
        asyncio.run(reader("1000", None))
    assert connector.service.requests == []


def test_gmail_reader_revoked_between_pages_blocks_silent_approval():
    connector = FakeGmailConnector(gmail_pages())
    calls = []
    async def verify(_scope):
        calls.append("verify")
        return len(calls) == 1
    reader = GmailHistoryReader(
        scope=GMAIL_SCOPE, connector=connector, current_authorization=verify,
    )
    with pytest.raises(DeltaUnavailable):
        asyncio.run(collect_gmail_history(
            scope=GMAIL_SCOPE, start_history_id="1000",
            read_page=reader, as_of=NOW,
        ))
    assert len(connector.service.requests) == 1


def test_gmail_404_expired_history_is_not_an_empty_history_page():
    class FakeHttpError(Exception):
        def __init__(self, status):
            self.resp = SimpleNamespace(status=status)
    connector = FakeGmailConnector([FakeHttpError(404)])
    reader = GmailHistoryReader(
        scope=GMAIL_SCOPE, connector=connector, current_authorization=allowed,
    )
    with pytest.raises(DeltaCursorExpired):
        asyncio.run(reader("1000", None))


def test_gmail_403_source_error_sanitized():
    class FakeHttpError(Exception):
        def __init__(self):
            self.resp = SimpleNamespace(status=403)
    reader = GmailHistoryReader(
        scope=GMAIL_SCOPE, connector=FakeGmailConnector([FakeHttpError()]),
        current_authorization=allowed,
    )
    with pytest.raises(DeltaUnavailable) as err:
        asyncio.run(reader("1000", None))
    assert "403" not in str(err.value)


def test_graph_reader_follows_opaque_exact_link_with_no_redirects():
    pages = [GraphResponse(data) for data in graph_pages()]
    requests = []
    connector = FakeGraphConnector()
    authorizations = []
    async def verify(scope):
        authorizations.append(scope)
        return True
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=connector,
        current_authorization=verify,
        client_factory=lambda: FakeGraphClient(pages, requests),
    )
    result = asyncio.run(collect_graph_delta(
        scope=GRAPH_SCOPE, previous_delta_link=None,
        read_page=reader, as_of=NOW,
    ))
    assert result.terminal_cursor == GRAPH_DELTA
    assert result.checkpoint_persisted is False
    assert len(requests) == 2
    assert requests[1][0] == GRAPH_NEXT
    assert all(redirects is False for _, _, redirects in requests)
    assert all(headers["Authorization"] == "Bearer FAKE-PRIVATE-TOKEN"
               for _, headers, _ in requests)
    assert authorizations == [GRAPH_SCOPE, GRAPH_SCOPE]
    assert connector.headers_calls == 2
    assert "FAKE-PRIVATE-TOKEN" not in str(result)


def test_graph_wrong_folder_is_rejected_before_token_access():
    connector = FakeGraphConnector()
    requests = []
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=connector,
        current_authorization=allowed,
        client_factory=lambda: FakeGraphClient([], requests),
    )
    with pytest.raises(DeltaUnavailable):
        asyncio.run(reader(
            "https://graph.microsoft.com/v1.0/me/mailFolders/other/messages/delta?$skiptoken=X"
        ))
    assert requests == []
    assert connector.headers_calls == 0


def test_graph_revoked_employee_consent_blocks_before_outlook_token_lookup():
    connector = FakeGraphConnector()
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=connector,
        current_authorization=denied,
        client_factory=lambda: FakeGraphClient([], []),
    )
    with pytest.raises(DeltaUnavailable):
        asyncio.run(reader(GRAPH_NEXT))
    assert connector.headers_calls == 0


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_graph_http_error_never_certifies_a_complete_page(status):
    connector = FakeGraphConnector()
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=connector,
        current_authorization=allowed,
        client_factory=lambda: FakeGraphClient(
            [GraphResponse({"value": [], "@odata.deltaLink": GRAPH_DELTA}, status)],
            [],
        ),
    )
    with pytest.raises(DeltaUnavailable):
        asyncio.run(reader(GRAPH_NEXT))


@pytest.mark.parametrize("status", [404, 410])
def test_graph_expired_delta_token_requires_full_folder_sync(status):
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=FakeGraphConnector(),
        current_authorization=allowed,
        client_factory=lambda: FakeGraphClient([GraphResponse({}, status)], []),
    )
    with pytest.raises(DeltaCursorExpired):
        asyncio.run(reader(GRAPH_NEXT))


def test_graph_outbound_response_malformed_fails_closed():
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=FakeGraphConnector(),
        current_authorization=allowed,
        client_factory=lambda: FakeGraphClient([GraphResponse(["not", "a", "mapping"])], []),
    )
    with pytest.raises(DeltaUnavailable):
        asyncio.run(reader(GRAPH_NEXT))


def test_authorization_lookup_error_does_not_expose_contact_data():
    async def failure(_scope):
        raise RuntimeError("PRIVATE EMPLOYEE TOKEN OR CONSENT REASON")
    reader = OutlookFolderDeltaReader(
        scope=GRAPH_SCOPE, connector=FakeGraphConnector(),
        current_authorization=failure,
        client_factory=lambda: FakeGraphClient([], []),
    )
    with pytest.raises(DeltaUnavailable) as err:
        asyncio.run(reader(GRAPH_NEXT))
    assert "PRIVATE" not in str(err.value)


def test_source_and_connector_types_cannot_be_swapped():
    with pytest.raises(DeltaUnavailable):
        GmailHistoryReader(
            scope=GRAPH_SCOPE, connector=FakeGmailConnector([]),
            current_authorization=allowed,
        )
    with pytest.raises(DeltaUnavailable):
        OutlookFolderDeltaReader(
            scope=GMAIL_SCOPE, connector=FakeGraphConnector(),
            current_authorization=allowed,
        )
