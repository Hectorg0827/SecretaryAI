"""
QuickBooks Desktop connector via Conductor API.
Conductor abstracts the QBXML/SOAP Web Connector protocol into clean JSON REST.
See: https://docs.conductor.is
"""
from datetime import date
from decimal import Decimal
from typing import Optional

import httpx

from app.connectors.base import (
    Customer,
    Invoice,
    InventoryItem,
    PurchaseOrder,
    QuickBooksAdapter,
)


class QBDesktopAdapter(QuickBooksAdapter):
    BASE_URL = "https://api.conductor.is/v1"

    def __init__(self, api_key: str, end_user_id: str):
        self._api_key = api_key
        self._end_user_id = end_user_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Conductor-End-User-Id": end_user_id,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                f"{self.BASE_URL}{path}",
                headers=self._headers,
                params=params or {},
            )
            response.raise_for_status()
            return response.json()

    async def _paginate(self, path: str, params: Optional[dict] = None) -> list[dict]:
        """Fetch all pages from a Conductor endpoint."""
        results: list[dict] = []
        cursor: Optional[str] = None
        base_params = params or {}

        while True:
            page_params = {**base_params}
            if cursor:
                page_params["cursor"] = cursor

            data = await self._get(path, page_params)
            results.extend(data.get("data", []))

            if not data.get("hasMore"):
                break
            cursor = data.get("nextCursor")

        return results

    # ------------------------------------------------------------------
    # Adapter methods
    # ------------------------------------------------------------------

    def _normalize_customer(self, raw: dict) -> Customer:
        return Customer(
            id=raw["id"],
            qb_id=raw["id"],
            name=raw.get("name", ""),
            email=raw.get("email"),
            phone=raw.get("phone"),
            state=raw.get("billingAddress", {}).get("state"),
            balance=Decimal(str(raw.get("balance", 0))),
            total_sales=Decimal(str(raw.get("totalBalance", 0))),
        )

    def _normalize_invoice(self, raw: dict) -> Invoice:
        return Invoice(
            id=raw["id"],
            qb_id=raw["id"],
            customer_id=raw.get("customer", {}).get("id", ""),
            customer_name=raw.get("customer", {}).get("name", ""),
            date=date.fromisoformat(raw["transactionDate"]) if raw.get("transactionDate") else date.today(),
            due_date=date.fromisoformat(raw["dueDate"]) if raw.get("dueDate") else None,
            total=Decimal(str(raw.get("totalAmount", 0))),
            balance=Decimal(str(raw.get("balanceRemaining", 0))),
            status="open" if Decimal(str(raw.get("balanceRemaining", 0))) > 0 else "paid",
            line_items=raw.get("lineItems", []),
        )

    def _normalize_inventory_item(self, raw: dict) -> InventoryItem:
        return InventoryItem(
            id=raw["id"],
            qb_id=raw["id"],
            name=raw.get("name", ""),
            sku=raw.get("salesDescription"),
            quantity_on_hand=Decimal(str(raw.get("quantityOnHand", 0))),
            unit_price=Decimal(str(raw.get("salesPrice", 0))),
            purchase_cost=Decimal(str(raw.get("purchaseCost", 0))),
            reorder_point=Decimal(str(raw["reorderPoint"])) if raw.get("reorderPoint") else None,
        )

    def _normalize_purchase_order(self, raw: dict) -> PurchaseOrder:
        return PurchaseOrder(
            id=raw["id"],
            qb_id=raw["id"],
            vendor_id=raw.get("vendor", {}).get("id", ""),
            vendor_name=raw.get("vendor", {}).get("name", ""),
            date=date.fromisoformat(raw["transactionDate"]) if raw.get("transactionDate") else date.today(),
            expected_date=date.fromisoformat(raw["expectedDate"]) if raw.get("expectedDate") else None,
            total=Decimal(str(raw.get("totalAmount", 0))),
            status=raw.get("status", "open"),
            line_items=raw.get("lineItems", []),
        )

    async def get_customers(self) -> list[Customer]:
        raw = await self._paginate("/quickbooks-desktop/customers")
        return [self._normalize_customer(c) for c in raw]

    async def get_invoices(self, date_from: date, date_to: date) -> list[Invoice]:
        raw = await self._paginate(
            "/quickbooks-desktop/invoices",
            {
                "transactionDateFrom": date_from.isoformat(),
                "transactionDateTo": date_to.isoformat(),
            },
        )
        return [self._normalize_invoice(i) for i in raw]

    async def get_inventory(self) -> list[InventoryItem]:
        raw = await self._paginate("/quickbooks-desktop/inventory-items")
        return [self._normalize_inventory_item(i) for i in raw]

    async def get_purchase_orders(self) -> list[PurchaseOrder]:
        raw = await self._paginate("/quickbooks-desktop/purchase-orders")
        return [self._normalize_purchase_order(p) for p in raw]

    async def test_connection(self) -> bool:
        try:
            await self._get("/quickbooks-desktop/customers", {"limit": 1})
            return True
        except Exception:
            return False
