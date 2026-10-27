"""
Outlook / Office 365 connector via Microsoft Graph API.
Mirrors GmailConnector's interface so the UnifiedDataAdapter can use either.

Company config keys:
  ms_tenant_id, ms_client_id, ms_client_secret, ms_access_token, ms_refresh_token
"""
from __future__ import annotations
import logging
from typing import Optional
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


class OutlookConnector:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        on_token_refresh=None,
    ):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._on_token_refresh = on_token_refresh

    async def _get_headers(self) -> dict:
        """Return Authorization headers, refreshing token if needed."""
        token = await self._ensure_valid_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _ensure_valid_token(self) -> str:
        """Refresh the access token if expired."""
        import httpx
        # Only an observed 401 justifies refresh. Network failures, 403, 5xx,
        # and provider outages must surface rather than rotate credentials.
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_BASE}/me",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5,
            )
            if resp.status_code != 401:
                resp.raise_for_status()
                return self._access_token

        if not self._on_token_refresh:
            raise RuntimeError("Outlook token refresh requires a durable persistence callback")
        if not self._refresh_token:
            raise RuntimeError("Outlook token refresh requires a refresh credential")

        # Refresh
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    TOKEN_URL.format(tenant_id=self._tenant_id),
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                        "scope": "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.Send offline_access",
                    },
                )
                resp.raise_for_status()
                tokens = resp.json()
                self._access_token = tokens["access_token"]
                if "refresh_token" in tokens:
                    self._refresh_token = tokens["refresh_token"]
                import inspect
                persisted = self._on_token_refresh(self._access_token, self._refresh_token)
                if inspect.isawaitable(persisted):
                    await persisted
        except Exception as exc:
            log.error("Outlook token refresh failed (details withheld)")
            raise

        return self._access_token

    async def get_emails(self, query: str = "", max_results: int = 20) -> list[dict]:
        """Fetch emails from Outlook inbox. Returns same shape as GmailConnector."""
        import httpx
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        try:
            headers = await self._get_headers()
            # Build $filter from query
            params: dict = {
                "$top": min(max_results, 50),
                "$select": "id,conversationId,internetMessageId,subject,from,bodyPreview,receivedDateTime,isRead",
                "$orderby": "receivedDateTime desc",
            }
            if "is:unread" in query:
                params["$filter"] = "isRead eq false"

            messages: list[dict] = []
            seen_ids: set[str] = set()
            seen_urls: set[str] = set()
            url = f"{GRAPH_BASE}/me/messages"
            async with httpx.AsyncClient() as client:
                while len(messages) < max_results:
                    response = await client.get(url, headers=headers, params=params, timeout=15)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                        raise ValueError("Outlook messages response missing valid value list")
                    page = payload["value"]
                    for message in page[: max_results - len(messages)]:
                        message_id = message.get("id") if isinstance(message, dict) else None
                        if not isinstance(message_id, str) or not message_id:
                            raise ValueError("Outlook message source missing id")
                        if message_id in seen_ids:
                            raise ValueError("Outlook messages pages contain duplicate id")
                        seen_ids.add(message_id)
                        messages.append(message)
                    next_link = payload.get("@odata.nextLink")
                    if len(messages) >= max_results or not next_link:
                        break
                    # This URL comes from an external payload. Never send the
                    # OAuth bearer token to a different origin or endpoint.
                    if not isinstance(next_link, str):
                        raise ValueError("Outlook nextLink is invalid")
                    target = urlsplit(next_link)
                    if (target.scheme != "https" or target.hostname != "graph.microsoft.com"
                            or target.port not in (None, 443) or target.username or target.password
                            or target.path != "/v1.0/me/messages" or target.fragment
                            or next_link in seen_urls or not page):
                        raise ValueError("Outlook nextLink is unsafe or stalled")
                    seen_urls.add(next_link)
                    url = next_link
                    params = None  # nextLink includes the provider's full cursor query

            return [
                {
                    "id": m["id"],
                    "thread_id": m.get("conversationId", m["id"]),
                    "message_id": m.get("internetMessageId"),
                    "from": (m.get("from") or {}).get("emailAddress", {}).get("name", ""),
                    "from_email": (m.get("from") or {}).get("emailAddress", {}).get("address", ""),
                    "subject": m.get("subject", "(no subject)"),
                    "snippet": (m.get("bodyPreview") or "")[:200],
                    "date": m.get("receivedDateTime", ""),
                    "labels": [] if m.get("isRead") else ["UNREAD"],
                }
                for m in messages
            ]
        except Exception as exc:
            log.error("Outlook get_emails failed (details withheld to protect message data)")
            raise

    async def test_connection(self) -> bool:
        """Verify an actual Graph identity request; configuration is not health."""
        import httpx
        try:
            headers = await self._get_headers()
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{GRAPH_BASE}/me", headers=headers)
                response.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Outlook connectivity check failed: %s", exc)
            return False

    async def send_email_reply(self, to: str, subject: str, body: str, reply_to_id: Optional[str] = None) -> dict:
        """Send an email reply via Graph API."""
        import httpx
        try:
            headers = await self._get_headers()
            payload = {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": True,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{GRAPH_BASE}/me/sendMail",
                    headers=headers,
                    json=payload,
                    timeout=15,
                )
                resp.raise_for_status()
            return {"status": "sent"}
        except Exception as exc:
            log.error("Outlook send_email_reply failed: %s", exc)
            raise

    async def mark_email_read(self, email_id: str) -> None:
        """Mark an Outlook message as read."""
        import httpx
        try:
            headers = await self._get_headers()
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"{GRAPH_BASE}/me/messages/{email_id}",
                    headers=headers,
                    json={"isRead": True},
                    timeout=10,
                )
        except Exception as exc:
            log.warning("Outlook mark_email_read failed: %s", exc)
