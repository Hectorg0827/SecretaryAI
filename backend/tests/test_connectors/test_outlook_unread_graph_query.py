"""Contract tests for Microsoft Graph unread inbox queries.

All Graph calls are intercepted locally. No customer mailbox, OAuth token,
staging service or network is contacted.
"""
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.connectors.outlook import OutlookConnector


def _connector() -> OutlookConnector:
    return OutlookConnector(
        tenant_id="fixture-tenant",
        client_id="fixture-client",
        client_secret="fixture-secret",
        access_token="fixture-access",
        refresh_token="fixture-refresh",
    )


def _mock_graph(monkeypatch, *, messages_status=200, response_body=None):
    seen = []
    real_client = httpx.AsyncClient

    async def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "fixture-user"})
        if request.url.path == "/v1.0/me/messages":
            params = parse_qs(urlsplit(str(request.url)).query)
            # Match Graph's documented incompatibility; this test would have
            # rejected the prior broken unread+orderby implementation.
            if "$filter" in params and "$orderby" in params:
                return httpx.Response(400, json={
                    "error": {"code": "InefficientFilter", "message": "synthetic"}
                })
            return httpx.Response(messages_status, json=response_body if response_body is not None else {
                "value": [{
                    "id": "synthetic-message-1", "conversationId": "synthetic-thread",
                    "internetMessageId": "synthetic-message-id",
                    "from": {"emailAddress": {
                        "name": "Test sender", "address": "sender@example.invalid"}},
                    "subject": "Synthetic event", "bodyPreview": "fixture",
                    "receivedDateTime": "2026-11-13T12:00:00Z", "isRead": False,
                }],
            })
        raise AssertionError(f"Unexpected mocked Graph path: {request.url.path}")

    def client_factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(serve), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return seen


@pytest.mark.asyncio
async def test_unread_query_does_not_combine_incompatible_graph_filter_and_orderby(monkeypatch):
    seen = _mock_graph(monkeypatch)
    result = await _connector().get_emails(query="is:unread", max_results=20)
    assert len(result) == 1
    assert result[0]["labels"] == ["UNREAD"]
    calls = [r for r in seen if r.url.path.endswith("/me/messages")]
    assert len(calls) == 1
    params = parse_qs(urlsplit(str(calls[0].url)).query)
    assert params["$filter"] == ["isRead eq false"]
    assert "$orderby" not in params
    assert params["$top"] == ["20"]
    assert calls[0].headers["Authorization"] == "Bearer fixture-access"


@pytest.mark.asyncio
async def test_unfiltered_mail_keeps_recent_first_order(monkeypatch):
    seen = _mock_graph(monkeypatch)
    await _connector().get_emails(query="", max_results=5)
    messages = [r for r in seen if r.url.path.endswith("/me/messages")]
    params = parse_qs(urlsplit(str(messages[0].url)).query)
    assert params["$orderby"] == ["receivedDateTime desc"]
    assert "$filter" not in params


@pytest.mark.asyncio
async def test_provider_error_logs_only_numeric_status_not_response_or_credentials(monkeypatch, caplog):
    marker = "SENSITIVE_PROVIDER_RESPONSE_CANARY"
    _mock_graph(monkeypatch, messages_status=403, response_body={
        "error": {"message": marker, "code": "Authorization_RequestDenied"}
    })
    with caplog.at_level(logging.ERROR), pytest.raises(httpx.HTTPStatusError):
        await _connector().get_emails(query="is:unread")
    assert "graph_http_status=403" in caplog.text
    assert marker not in caplog.text
    assert "fixture-access" not in caplog.text
    assert "fixture-refresh" not in caplog.text
