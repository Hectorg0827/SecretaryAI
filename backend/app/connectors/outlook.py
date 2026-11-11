"""
Outlook / Microsoft 365 connector via Microsoft Graph.
Mirrors GmailConnector's interface so the UnifiedDataAdapter can use either.

Connection is made with Microsoft sign-in (OAuth 2.0, delegated permissions):
see /auth/outlook/* in app/api/auth.py. The app registration (client id and
secret) belongs to the operator; each company only stores its own tokens,
encrypted at rest.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MS_AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
# Kept for callers that imported the old name.
TOKEN_URL = MS_TOKEN_URL
# Read mail, mark as read, send as the signed-in user, stay signed in.
MS_SCOPES = "offline_access User.Read Mail.ReadWrite Mail.Send"


class OutlookAuthError(RuntimeError):
    """The mailbox connection is no longer valid — the user must reconnect."""


class OutlookConnector:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        on_token_refresh=None,
        expires_at: Optional[str] = None,
    ):
        self._tenant_id = tenant_id or "common"
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._on_token_refresh = on_token_refresh
        self._expires_at = _parse_ts(expires_at)

    # ── tokens ───────────────────────────────────────────────────────────────

    def _expired(self) -> bool:
        if self._expires_at is None:
            return False  # unknown: find out from a 401
        return datetime.now(timezone.utc) >= self._expires_at - timedelta(minutes=2)

    async def _refresh(self) -> None:
        """Exchange the refresh token for a new access token and persist both."""
        if not self._refresh_token or not self._client_id:
            raise OutlookAuthError("Outlook needs to be reconnected")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                MS_TOKEN_URL.format(tenant=self._tenant_id),
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "scope": MS_SCOPES,
                },
            )
        if resp.status_code >= 400:
            # invalid_grant = revoked / expired consent. Do not log the body
            # (it can echo token material).
            log.error("Outlook token refresh rejected (HTTP %s)", resp.status_code)
            raise OutlookAuthError("Outlook needs to be reconnected")
        tokens = resp.json()
        self._access_token = tokens["access_token"]
        # Microsoft rotates refresh tokens: the new one MUST be saved, or the
        # connection silently dies when the old one is retired.
        self._refresh_token = tokens.get("refresh_token") or self._refresh_token
        self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))
        if self._on_token_refresh:
            try:
                result = self._on_token_refresh(self._access_token, self._refresh_token, self._expires_at.isoformat())
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log.error("Could not save refreshed Outlook tokens: %s", exc)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Call Graph, refreshing the token before expiry and once on a 401."""
        if self._expired():
            await self._refresh()
        url = f"{GRAPH_BASE}{path}"
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 1:
                await self._refresh()
                continue
            return resp
        return resp

    # ── mail ─────────────────────────────────────────────────────────────────

    async def get_emails(self, query: str = "", max_results: int = 20) -> list[dict]:
        """Fetch inbox messages. Returns the same shape as GmailConnector."""
        params: dict = {
            "$top": max(1, min(int(max_results), 50)),
            "$select": "id,conversationId,subject,from,bodyPreview,receivedDateTime,isRead",
            "$orderby": "receivedDateTime desc",
        }
        if "is:unread" in (query or ""):
            params["$filter"] = "isRead eq false"
        try:
            resp = await self._request("GET", "/me/mailFolders/inbox/messages", params=params)
            resp.raise_for_status()
        except OutlookAuthError:
            raise
        except Exception as exc:
            log.error("Outlook get_emails failed: %s", exc)
            return []

        out = []
        for m in resp.json().get("value", []):
            sender = (m.get("from") or {}).get("emailAddress") or {}
            name, address = sender.get("name", ""), sender.get("address", "")
            out.append({
                "id": m["id"],
                "thread_id": m.get("conversationId", m["id"]),
                # "Name <address>" like Gmail, so callers parse both the same way
                "from": f"{name} <{address}>" if name and address else (address or name),
                "from_email": address,
                "subject": m.get("subject") or "(no subject)",
                "snippet": (m.get("bodyPreview") or "")[:200],
                "date": m.get("receivedDateTime", ""),
                "labels": [] if m.get("isRead") else ["UNREAD"],
            })
        return out

    async def send_email_reply(self, to: str, subject: str, body: str,
                               reply_to_id: Optional[str] = None) -> dict:
        """
        Send an email. With `reply_to_id` it is sent as a real reply in the
        original conversation (threading and quoting handled by Outlook);
        otherwise as a new message.
        """
        if reply_to_id:
            resp = await self._request(
                "POST", f"/me/messages/{reply_to_id}/reply",
                json={
                    "message": {"toRecipients": [{"emailAddress": {"address": to}}]},
                    "comment": body,
                },
            )
            if resp.status_code == 404:
                reply_to_id = None  # original is gone — fall back to a new message
            else:
                resp.raise_for_status()
                return {"status": "sent", "mode": "reply"}

        resp = await self._request(
            "POST", "/me/sendMail",
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": True,
            },
        )
        resp.raise_for_status()
        return {"status": "sent", "mode": "new"}

    async def mark_email_read(self, email_id: str) -> None:
        try:
            resp = await self._request("PATCH", f"/me/messages/{email_id}", json={"isRead": True})
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Outlook mark_email_read failed: %s", exc)

    async def test_connection(self) -> bool:
        try:
            resp = await self._request("GET", "/me", params={"$select": "id"})
            return resp.status_code == 200
        except Exception:
            return False


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        ts = datetime.fromisoformat(text)
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
