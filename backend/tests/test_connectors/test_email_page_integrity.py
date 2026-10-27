"""Read-only email source paging, identity and token persistence fixtures."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.connectors.gmail_connector import GmailConnector
from app.connectors.outlook import OutlookConnector
from app.connectors.unified_adapter import UnifiedDataAdapter


def _gmail(pages, messages):
    connector = GmailConnector({"token": "synthetic"})
    service = MagicMock()
    service.users().messages().list().execute.side_effect = pages
    service.users().messages().get().execute.side_effect = messages
    connector._get_service = lambda: service
    return connector, service


def _gmail_msg(message_id: str):
    return {"id": message_id, "threadId": "thread-1", "snippet": "fixture",
            "payload": {"headers": [{"name": "Message-ID", "value": f"<{message_id}@example.invalid>"}]}}


def test_gmail_follows_next_page_token_for_requested_result_count():
    gmail, service = _gmail(
        [{"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "next-a"},
         {"messages": [{"id": "m3"}]}],
        [_gmail_msg("m1"), _gmail_msg("m2"), _gmail_msg("m3")],
    )
    emails = gmail._fetch_emails("in:inbox", 3)
    assert [e["id"] for e in emails] == ["m1", "m2", "m3"]
    assert service.users().messages().list.call_args_list[-1].kwargs["pageToken"] == "next-a"


def test_gmail_malformed_list_payload_is_not_a_verified_empty_inbox():
    gmail, _ = _gmail([{"error": "synthetic failure"}], [])
    with pytest.raises(ValueError, match="messages"):
        gmail._fetch_emails("", 20)


def test_gmail_explicit_zero_message_result_is_valid():
    gmail, _ = _gmail([{"resultSizeEstimate": 0}], [])
    assert gmail._fetch_emails("", 20) == []


def test_gmail_duplicate_message_across_pages_is_rejected():
    gmail, _ = _gmail(
        [{"messages": [{"id": "m1"}], "nextPageToken": "next-a"},
         {"messages": [{"id": "m1"}]}], [_gmail_msg("m1")],
    )
    with pytest.raises(ValueError, match="duplicate"):
        gmail._fetch_emails("", 2)


@pytest.mark.parametrize("count", [0, -1])
def test_gmail_invalid_limit_is_rejected(count):
    gmail, _ = _gmail([], [])
    with pytest.raises(ValueError, match="max_results"):
        gmail._fetch_emails("", count)


def _outlook():
    return OutlookConnector("synthetic-tenant", "client", "secret", "access", "refresh")


def _graph_message(index):
    return {"id": f"m{index}", "conversationId": "thread-1",
            "internetMessageId": f"<m{index}@example.invalid>",
            "from": {"emailAddress": {"address": "fixture@example.invalid", "name": "Fixture"}},
            "receivedDateTime": "2026-10-26T12:00:00Z", "isRead": False}


@pytest.mark.asyncio
async def test_outlook_follows_valid_nextlink_to_requested_count():
    connector = _outlook()
    connector._get_headers = AsyncMock(return_value={"Authorization": "Bearer synthetic"})
    next_link = "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=opaque"
    responses = [
        httpx.Response(200, json={"value": [_graph_message(i) for i in range(50)],
                                  "@odata.nextLink": next_link}, request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages")),
        httpx.Response(200, json={"value": [_graph_message(i) for i in range(50, 55)]},
                       request=httpx.Request("GET", next_link)),
    ]
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = responses
    with patch("httpx.AsyncClient", return_value=client):
        emails = await connector.get_emails(max_results=55)
    assert len(emails) == 55
    assert client.get.await_args_list[1].args[0] == next_link


@pytest.mark.asyncio
async def test_outlook_malformed_payload_is_not_verified_empty_inbox():
    connector = _outlook()
    connector._get_headers = AsyncMock(return_value={"Authorization": "Bearer synthetic"})
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200, json={"error": "fixture"},
                                            request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages"))
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="value"):
            await connector.get_emails()


@pytest.mark.asyncio
async def test_outlook_rejects_cross_origin_nextlink_before_sending_auth():
    connector = _outlook()
    connector._get_headers = AsyncMock(return_value={"Authorization": "Bearer synthetic"})
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200, json={
        "value": [_graph_message(1)],
        "@odata.nextLink": "https://example.invalid/capture-token",
    }, request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages"))
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="nextLink"):
            await connector.get_emails(max_results=2)
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_outlook_repeated_message_on_next_page_is_rejected():
    connector = _outlook()
    connector._get_headers = AsyncMock(return_value={"Authorization": "Bearer synthetic"})
    next_link = "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=next"
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = [
        httpx.Response(200, json={"value": [_graph_message(1)], "@odata.nextLink": next_link},
                       request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages")),
        httpx.Response(200, json={"value": [_graph_message(1)]}, request=httpx.Request("GET", next_link)),
    ]
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="duplicate"):
            await connector.get_emails(max_results=2)


@pytest.mark.asyncio
async def test_outlook_missing_conversation_id_does_not_create_a_fake_thread():
    connector = _outlook()
    connector._get_headers = AsyncMock(return_value={"Authorization": "Bearer synthetic"})
    msg = _graph_message(1)
    del msg["conversationId"]
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200, json={"value": [msg]},
                                            request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages"))
    with patch("httpx.AsyncClient", return_value=client):
        result = await connector.get_emails(max_results=1)
    assert result[0]["thread_id"] is None
    assert result[0]["id"] == "m1"


@pytest.mark.asyncio
async def test_outlook_rotating_credentials_require_company_scope():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._config = {}
    with pytest.raises(RuntimeError, match="company identifier"):
        await adapter._persist_outlook_tokens("rotated-access", "rotated-refresh")


@pytest.mark.asyncio
async def test_outlook_graph_network_outage_does_not_trigger_token_rotation():
    connector = _outlook()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = httpx.ConnectError("synthetic outage")
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.ConnectError):
            await connector._ensure_valid_token()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_outlook_cannot_rotate_refresh_without_durable_callback():
    connector = _outlook()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(401, request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me"))
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError, match="persistence callback"):
            await connector._ensure_valid_token()
    client.post.assert_not_awaited()


def test_unified_adapter_wires_durable_outlook_token_callback():
    config = {"ms_tenant_id": "tenant", "ms_access_token": "access",
              "ms_client_id": "client", "ms_client_secret": "secret", "ms_refresh_token": "refresh"}
    adapter = UnifiedDataAdapter(config)
    assert callable(adapter._outlook._on_token_refresh)
