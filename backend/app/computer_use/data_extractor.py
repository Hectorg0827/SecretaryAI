"""
Data Extractor — parses structured data out of Claude's Computer Use responses.
Claude is instructed to return a JSON block when extraction is complete.
This module finds and validates that block.
"""
import json
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


class DataExtractor:
    @staticmethod
    def parse_json_block(text: str) -> Optional[dict]:
        """
        Find ```json ... ``` in Claude's response and parse it.
        Returns None if no valid JSON block is found.
        """
        pattern = r"```json\s*([\s\S]*?)```"
        matches = re.findall(pattern, text, re.IGNORECASE)

        for match in matches:
            try:
                data = json.loads(match.strip())
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue

        return None

    @staticmethod
    def normalize_orders(raw: dict) -> list[dict]:
        """Normalize extracted order data to the standard format."""
        records = raw.get("records", raw.get("orders", []))
        normalized = []
        for r in records:
            normalized.append({
                "customer_name": r.get("customer") or r.get("customer_name") or r.get("account", ""),
                "order_date": r.get("date") or r.get("order_date") or r.get("txn_date", ""),
                "total_amount": _parse_amount(r.get("total") or r.get("amount") or r.get("total_amount", 0)),
                "order_id": r.get("id") or r.get("order_id") or r.get("po_number", ""),
                "items": r.get("items", []),
                "source": "computer_use",
            })
        return normalized

    @staticmethod
    def normalize_inventory(raw: dict) -> list[dict]:
        """Normalize extracted inventory data."""
        records = raw.get("records", raw.get("inventory", raw.get("items", [])))
        normalized = []
        for r in records:
            normalized.append({
                "product_name": r.get("name") or r.get("product") or r.get("item", ""),
                "quantity": _parse_qty(r.get("qty") or r.get("quantity") or r.get("on_hand", 0)),
                "location": r.get("location") or r.get("warehouse", ""),
                "source": "computer_use",
            })
        return normalized


def _parse_amount(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _parse_qty(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return 0
