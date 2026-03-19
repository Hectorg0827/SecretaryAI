"""
Gmail connector — reads and (with approval) sends email via Google Gmail API.
Requires OAuth 2.0 credentials from Google Workspace.

Sending email is always a DRAFT_AND_WAIT action — the AI drafts it,
the user reviews and approves before anything is sent.
"""
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

log = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GmailConnector:
    def __init__(self, credentials: dict):
        """
        credentials: dict with keys token, refresh_token, client_id, client_secret, token_uri
        (standard google-auth authorized_user format)
        """
        self._creds_dict = credentials
        self._service = None

    def _get_service(self):
        if self._service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_info(self._creds_dict, GMAIL_SCOPES)
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    async def get_recent_emails(self, query: str = "", max_results: int = 20) -> list[dict]:
        """Retrieve emails matching a Gmail search query."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_emails, query, max_results)

    def _fetch_emails(self, query: str, max_results: int) -> list[dict]:
        service = self._get_service()
        result = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        emails = []
        for ref in result.get("messages", []):
            try:
                msg = service.users().messages().get(
                    userId="me", id=ref["id"], format="full"
                ).execute()

                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }

                emails.append({
                    "id": msg["id"],
                    "from": headers.get("from", ""),
                    "to": headers.get("to", ""),
                    "subject": headers.get("subject", ""),
                    "date": headers.get("date", ""),
                    "snippet": msg.get("snippet", ""),
                    "labels": msg.get("labelIds", []),
                    "body_preview": self._extract_body(msg)[:500],
                })
            except Exception as e:
                log.warning("Failed to fetch email %s: %s", ref["id"], e)

        return emails

    def _extract_body(self, message: dict) -> str:
        """Extract plain-text body from a Gmail message."""
        payload = message.get("payload", {})

        def _get_text(part: dict) -> Optional[str]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            for sub in part.get("parts", []):
                result = _get_text(sub)
                if result:
                    return result
            return None

        return _get_text(payload) or ""

    async def create_draft(self, to: str, subject: str, body: str) -> dict:
        """
        Create a Gmail draft (does NOT send it).
        User must explicitly click Send after reviewing.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._create_draft_sync, to, subject, body)

    def _create_draft_sync(self, to: str, subject: str, body: str) -> dict:
        service = self._get_service()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        return {"draft_id": draft["id"], "status": "created"}

    async def send_approved_email(self, to: str, subject: str, body: str) -> dict:
        """
        Send an email. ONLY call this after the action engine has confirmed
        DRAFT_AND_WAIT approval from the user.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._send_sync, to, subject, body)

    def _send_sync(self, to: str, subject: str, body: str) -> dict:
        service = self._get_service()
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return {"message_id": sent["id"], "status": "sent"}
