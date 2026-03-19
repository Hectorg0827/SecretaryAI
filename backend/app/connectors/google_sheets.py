"""
Google Sheets connector — reads and writes business data in Google Sheets.
Common use cases: price lists, depletion report templates, territory maps.

Writing is always a DRAFT_AND_WAIT action.
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleSheetsConnector:
    def __init__(self, credentials: dict):
        self._creds_dict = credentials
        self._service = None

    def _get_service(self):
        if self._service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_info(self._creds_dict, SHEETS_SCOPES)
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    async def read_range(self, spreadsheet_id: str, range_notation: str) -> list[list]:
        """
        Read a range from a Google Sheet.
        range_notation: e.g., "Sheet1!A1:E100"
        Returns a list of rows, each row a list of cell values.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._read_range_sync, spreadsheet_id, range_notation
        )

    def _read_range_sync(self, spreadsheet_id: str, range_notation: str) -> list[list]:
        service = self._get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_notation)
            .execute()
        )
        return result.get("values", [])

    async def read_as_dicts(self, spreadsheet_id: str, range_notation: str) -> list[dict]:
        """
        Read a range and return as list of dicts using the first row as keys.
        """
        rows = await self.read_range(spreadsheet_id, range_notation)
        if not rows:
            return []
        headers = [str(h).strip().lower().replace(" ", "_") for h in rows[0]]
        result = []
        for row in rows[1:]:
            padded = row + [""] * (len(headers) - len(row))
            result.append(dict(zip(headers, padded)))
        return result

    async def append_rows(
        self, spreadsheet_id: str, range_notation: str, rows: list[list]
    ) -> dict:
        """
        Append rows to a sheet.
        DRAFT_AND_WAIT approval required before calling.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._append_sync, spreadsheet_id, range_notation, rows
        )

    def _append_sync(self, spreadsheet_id: str, range_notation: str, rows: list[list]) -> dict:
        service = self._get_service()
        body = {"values": rows}
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_notation,
                valueInputOption="USER_ENTERED",
                body=body,
            )
            .execute()
        )
        return {
            "updated_range": result.get("updates", {}).get("updatedRange"),
            "rows_appended": result.get("updates", {}).get("updatedRows", 0),
        }
