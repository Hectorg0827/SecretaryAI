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
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        service = self._get_service()
        emails = []
        seen_ids: set[str] = set()
        seen_tokens: set[str] = set()
        next_token = None
        while len(emails) < max_results:
            params = {"userId": "me", "q": query, "maxResults": min(max_results - len(emails), 500)}
            if next_token:
                params["pageToken"] = next_token
            result = service.users().messages().list(**params).execute()
            if not isinstance(result, dict):
                raise ValueError("Gmail messages response must be a mapping")
            refs = result.get("messages")
            if refs is None:
                if result.get("resultSizeEstimate") == 0 and not result.get("nextPageToken"):
                    refs = []
                else:
                    raise ValueError("Gmail messages response missing message results")
            if not isinstance(refs, list):
                raise ValueError("Gmail messages response has invalid message results")

            for ref in refs[: max_results - len(emails)]:
                ref_id = ref.get("id") if isinstance(ref, dict) else None
                if not isinstance(ref_id, str) or not ref_id:
                    raise ValueError("Gmail message reference missing id")
                if ref_id in seen_ids:
                    raise ValueError("Gmail pages contain duplicate message id")
                seen_ids.add(ref_id)
                try:
                    msg = service.users().messages().get(
                        userId="me", id=ref_id, format="full"
                    ).execute()
                    if not isinstance(msg, dict) or msg.get("id") != ref_id:
                        raise ValueError("Gmail message source identity mismatch")
                    headers = {
                        h["name"].lower(): h["value"]
                        for h in msg.get("payload", {}).get("headers", [])
                    }
                    emails.append({
                        "id": msg["id"],
                        "thread_id": msg.get("threadId"),
                        "message_id": headers.get("message-id"),
                        "from": headers.get("from", ""),
                        "to": headers.get("to", ""),
                        "subject": headers.get("subject", ""),
                        "date": headers.get("date", ""),
                        "snippet": msg.get("snippet", ""),
                        "labels": msg.get("labelIds", []),
                        "body_preview": self._extract_body(msg)[:500],
                    })
                except Exception:
                    # Never hide a partial mailbox read or log message PII.
                    log.warning("Gmail message retrieval failed")
                    raise

            new_token = result.get("nextPageToken")
            if len(emails) >= max_results or not new_token:
                break
            if not refs or not isinstance(new_token, str) or new_token in seen_tokens:
                raise ValueError("Gmail messages pagination stalled or repeated token")
            seen_tokens.add(new_token)
            next_token = new_token

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

    async def mark_as_read(self, email_id: str) -> None:
        """Remove the UNREAD label from a Gmail message."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._mark_as_read_sync, email_id)

    def _mark_as_read_sync(self, email_id: str) -> None:
        service = self._get_service()
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

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
