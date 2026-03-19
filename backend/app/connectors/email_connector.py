"""
Email connector — reads Gmail and Outlook for customs broker updates,
supplier communications, and other business-relevant messages.
"""
import base64
from typing import Optional

import httpx


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector:
    def __init__(self, access_token: str):
        self._token = access_token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def list_messages(
        self, query: str = "", max_results: int = 50
    ) -> list[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages",
                headers=self._headers(),
                params={"q": query, "maxResults": max_results},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("messages", [])

    async def get_message(self, message_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
                headers=self._headers(),
                params={"format": "full"},
            )
            response.raise_for_status()
            return response.json()

    def extract_body(self, message: dict) -> str:
        """Extract plain text body from a Gmail message."""
        payload = message.get("payload", {})

        def get_text_from_part(part: dict) -> Optional[str]:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                data = part.get("body", {}).get("data", "")
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            for sub in part.get("parts", []):
                result = get_text_from_part(sub)
                if result:
                    return result
            return None

        return get_text_from_part(payload) or ""

    async def fetch_customs_updates(self) -> list[dict]:
        """Fetch emails likely related to customs and shipping."""
        queries = [
            "subject:(customs OR clearance OR shipment OR arrival) newer_than:7d",
            "subject:(invoice OR bill of lading OR POD) newer_than:7d",
        ]
        messages = []
        for q in queries:
            msgs = await self.list_messages(query=q, max_results=20)
            for m in msgs:
                full = await self.get_message(m["id"])
                messages.append(
                    {
                        "id": m["id"],
                        "subject": next(
                            (
                                h["value"]
                                for h in full.get("payload", {}).get("headers", [])
                                if h["name"].lower() == "subject"
                            ),
                            "",
                        ),
                        "from": next(
                            (
                                h["value"]
                                for h in full.get("payload", {}).get("headers", [])
                                if h["name"].lower() == "from"
                            ),
                            "",
                        ),
                        "body_preview": self.extract_body(full)[:500],
                    }
                )
        return messages
