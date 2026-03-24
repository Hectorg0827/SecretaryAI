"""
Outlook / Office 365 connector via Microsoft Graph API.
Mirrors GmailConnector's interface so the UnifiedDataAdapter can use either.

Company config keys:
  ms_tenant_id, ms_client_id, ms_client_secret, ms_access_token, ms_refresh_token
"""
from __future__ import annotations
import logging
from typing import Optional

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
        try:
            # Try a lightweight call to check token validity
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GRAPH_BASE}/me",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    timeout=5,
                )
                if resp.status_code != 401:
                    return self._access_token
        except Exception:
            pass

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
                if self._on_token_refresh:
                    self._on_token_refresh(self._access_token, self._refresh_token)
        except Exception as exc:
            log.error("Outlook token refresh failed: %s", exc)

        return self._access_token

    async def get_emails(self, query: str = "", max_results: int = 20) -> list[dict]:
        """Fetch emails from Outlook inbox. Returns same shape as GmailConnector."""
        import httpx
        try:
            headers = await self._get_headers()
            # Build $filter from query
            params: dict = {
                "$top": min(max_results, 50),
                "$select": "id,subject,from,bodyPreview,receivedDateTime,isRead",
                "$orderby": "receivedDateTime desc",
            }
            if "is:unread" in query:
                params["$filter"] = "isRead eq false"

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GRAPH_BASE}/me/messages",
                    headers=headers,
                    params=params,
                    timeout=15,
                )
                resp.raise_for_status()
                messages = resp.json().get("value", [])

            return [
                {
                    "id": m["id"],
                    "thread_id": m.get("conversationId", m["id"]),
                    "from": m.get("from", {}).get("emailAddress", {}).get("name", ""),
                    "from_email": m.get("from", {}).get("emailAddress", {}).get("address", ""),
                    "subject": m.get("subject", "(no subject)"),
                    "snippet": m.get("bodyPreview", "")[:200],
                    "date": m.get("receivedDateTime", ""),
                    "labels": [] if m.get("isRead") else ["UNREAD"],
                }
                for m in messages
            ]
        except Exception as exc:
            log.error("Outlook get_emails failed: %s", exc)
            return []

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
