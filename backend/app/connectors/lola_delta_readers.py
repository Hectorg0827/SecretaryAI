"""Read-only provider adapters for Lola's bounded delta replay collectors.

These adapters use already-existing Gmail and Outlook connector instances
supplied by trusted backend code. They never look up credentials by a browser
provided tenant/account id, never send messages, never log provider payloads,
and require CURRENT source authorization before EACH provider page read.

The authorization callback must be backed by a fresh server-side active
employee/consent/connection/permission check. Passing a constant True from a
web request or LLM would be a security defect. The classes are not mounted
as HTTP endpoints or background jobs in this stage.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Mapping

from app.intelligence.lola.delta_replay import (
    DeltaCursorExpired, DeltaUnavailable, Scope, _graph_url, _history,
)

CurrentAuthorization = Callable[[Scope], Awaitable[bool]]
MAX_READ_SECONDS = 15


async def _authorize(scope: Scope, current_authorization: CurrentAuthorization) -> None:
    try:
        allowed = await asyncio.wait_for(
            current_authorization(scope), timeout=MAX_READ_SECONDS
        )
    except Exception:
        raise DeltaUnavailable("Current provider authorization could not be verified") from None
    if allowed is not True:
        raise DeltaUnavailable("Current provider authorization or employee consent unavailable")


class GmailHistoryReader:
    """Read Gmail history.list only, using the approved employee connector.

    The connector may refresh its own access token. No tokens, headers,
    response bodies, or raw provider request errors are logged or returned.
    """

    def __init__(
        self, *, scope: Scope, connector: object,
        current_authorization: CurrentAuthorization,
    ):
        if scope.provider != "gmail":
            raise DeltaUnavailable("Gmail connection scope does not match connector")
        self.scope = scope
        self.connector = connector
        self.current_authorization = current_authorization

    async def __call__(self, start_history_id: str, page_token: str | None) -> Mapping[str, object]:
        start = _history(start_history_id, "Gmail history cursor")
        if page_token is not None:
            if not isinstance(page_token, str) or not page_token or len(page_token) > 8192:
                raise DeltaUnavailable("Gmail page token invalid")
        await _authorize(self.scope, self.current_authorization)

        def fetch():
            service = self.connector._get_service()
            params = {
                "userId": "me",
                "startHistoryId": start,
                "maxResults": 100,
            }
            if page_token is not None:
                params["pageToken"] = page_token
            return service.users().history().list(**params).execute()

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(fetch), timeout=MAX_READ_SECONDS
            )
        except Exception as exc:
            # Google API HttpError embeds response bodies and may include
            # mailbox data. Only inspect the numeric status; never stringify.
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status == 404:
                raise DeltaCursorExpired("Gmail history cursor expired; full resync required") from None
            raise DeltaUnavailable("Gmail history provider read failed") from None
        if not isinstance(result, Mapping):
            raise DeltaUnavailable("Gmail history source returned no valid page")
        return result


class OutlookFolderDeltaReader:
    """Read one verified Graph folder delta path using OutlookConnector.

    Unlike the existing /me/messages list, Graph folder-delta stores a
    distinct terminal deltaLink for EACH included mail folder. Neither
    redirects nor URL reconstruction from arbitrary user text are allowed.
    """

    def __init__(
        self, *, scope: Scope, connector: object,
        current_authorization: CurrentAuthorization,
        client_factory: Callable[[], object] | None = None,
    ):
        if scope.provider != "outlook" or not scope.folder_id:
            raise DeltaUnavailable("Graph delta connection requires an exact folder")
        self.scope = scope
        self.connector = connector
        self.current_authorization = current_authorization
        self.client_factory = client_factory

    async def __call__(self, url: str) -> Mapping[str, object]:
        # The outgoing request is always constrained to this account's exact
        # folder, including for opaque provider-supplied continuation URLs.
        checked_url = _graph_url(url, self.scope.folder_id)
        await _authorize(self.scope, self.current_authorization)
        try:
            headers = await asyncio.wait_for(
                self.connector._get_headers(), timeout=MAX_READ_SECONDS
            )
            if not isinstance(headers, dict) or not isinstance(headers.get("Authorization"), str):
                raise DeltaUnavailable("Graph connector access token unavailable")
            if self.client_factory is None:
                import httpx
                client = httpx.AsyncClient(follow_redirects=False)
            else:
                client = self.client_factory()
            async with client:
                result = await client.get(
                    checked_url, headers=headers, timeout=MAX_READ_SECONDS,
                    follow_redirects=False,
                )
            status = getattr(result, "status_code", 0)
            if status in (404, 410):
                raise DeltaCursorExpired("Graph folder delta cursor expired; full resync required")
            if not isinstance(status, int) or not 200 <= status < 300:
                raise DeltaUnavailable("Graph delta API did not authorize a complete page")
            body = result.json()
        except DeltaCursorExpired:
            raise
        except Exception:
            raise DeltaUnavailable("Graph folder delta provider read failed") from None
        if not isinstance(body, Mapping):
            raise DeltaUnavailable("Graph delta source returned no valid page")
        return body
