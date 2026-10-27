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
    Payment,
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

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.BASE_URL}{path}",
                headers={**self._headers, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def _patch(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.patch(
                f"{self.BASE_URL}{path}",
                headers={**self._headers, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def _paginate(self, path: str, params: Optional[dict] = None) -> list[dict]:
        """Fetch all pages from a Conductor endpoint."""
        results: list[dict] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        seen_ids: set[str] = set()
        base_params = params or {}

        while True:
            page_params = {**base_params}
            if cursor:
                page_params["cursor"] = cursor

            data = await self._get(path, page_params)
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError("Conductor response missing valid data list")
            if not isinstance(data.get("hasMore"), bool):
                raise ValueError("Conductor response missing valid hasMore flag")
            for item in data["data"]:
                item_id = item.get("id") if isinstance(item, dict) else None
                if not item_id:
                    raise ValueError("Conductor source record missing id")
                if item_id in seen_ids:
                    raise ValueError("Conductor pagination returned duplicate id")
                seen_ids.add(item_id)
            results.extend(data["data"])

            if not data["hasMore"]:
                break
            if not data["data"]:
                raise ValueError("Conductor pagination hasMore with empty page")
            next_cursor = data.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                raise ValueError("Conductor pagination missing next cursor")
            if next_cursor in seen_cursors:
                raise ValueError("Conductor pagination repeated continuation cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

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
        for field in ("id", "transactionDate", "totalAmount", "balanceRemaining"):
            if raw.get(field) is None or raw.get(field) == "":
                raise ValueError(f"Conductor invoice missing {field}")
        try:
            total = Decimal(str(raw["totalAmount"]))
            balance = Decimal(str(raw["balanceRemaining"]))
        except Exception as exc:
            raise ValueError("Conductor invoice has invalid totalAmount or balanceRemaining") from exc
        if not total.is_finite() or total < 0:
            raise ValueError("Conductor invoice has invalid totalAmount")
        if not balance.is_finite() or balance < 0:
            raise ValueError("Conductor invoice has invalid balanceRemaining")
        return Invoice(
            id=raw["id"],
            qb_id=raw["id"],
            customer_id=raw.get("customer", {}).get("id", ""),
            customer_name=raw.get("customer", {}).get("name", ""),
            date=date.fromisoformat(raw["transactionDate"]),
            due_date=date.fromisoformat(raw["dueDate"]) if raw.get("dueDate") else None,
            total=total,
            balance=balance,
            status="open" if balance > 0 else "paid",
            line_items=raw.get("lineItems", []),
        )

    def _normalize_inventory_item(self, raw: dict) -> InventoryItem:
        if raw.get("quantityOnHand") is None or raw.get("quantityOnHand") == "":
            raise ValueError("Conductor inventory missing quantityOnHand")
        try:
            quantity_on_hand = Decimal(str(raw["quantityOnHand"]))
        except Exception as exc:
            raise ValueError("Conductor inventory has invalid quantityOnHand") from exc
        if not quantity_on_hand.is_finite():
            raise ValueError("Conductor inventory has invalid quantityOnHand")
        return InventoryItem(
            id=raw["id"],
            qb_id=raw["id"],
            name=raw.get("name", ""),
            sku=raw.get("salesDescription"),
            quantity_on_hand=quantity_on_hand,
            unit_price=Decimal(str(raw.get("salesPrice", 0))),
            purchase_cost=Decimal(str(raw.get("purchaseCost", 0))),
            reorder_point=Decimal(str(raw["reorderPoint"])) if raw.get("reorderPoint") is not None else None,
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

    # ------------------------------------------------------------------
    # Write-back methods
    # ------------------------------------------------------------------

    async def create_purchase_order(
        self,
        vendor_id: str,
        line_items: list[dict],
        ship_date: Optional[date] = None,
        memo: Optional[str] = None,
    ) -> PurchaseOrder:
        """Create a PurchaseOrder via Conductor."""
        conductor_lines = [
            {
                "itemId": item["item_id"],
                "description": item.get("description", ""),
                "quantity": str(item.get("quantity", 1)),
                "unitCost": str(item.get("unit_cost", 0)),
            }
            for item in line_items
        ]

        payload: dict = {
            "vendorId": vendor_id,
            "lineItems": conductor_lines,
            "transactionDate": date.today().isoformat(),
        }
        if ship_date:
            payload["expectedDate"] = ship_date.isoformat()
        if memo:
            payload["memo"] = memo

        raw = await self._post("/quickbooks-desktop/purchase-orders", payload)
        return self._normalize_purchase_order(raw)

    async def update_invoice_status(
        self,
        invoice_id: str,
        status: str,
    ) -> Invoice:
        """
        Update invoice status via Conductor.
        Supported: "void" (sets isPaid=false, balance=total via void flag).
        """
        if status == "void":
            payload = {"isVoided": True}
        else:
            raise ValueError(
                f"QBD only supports status='void' via update_invoice_status. "
                f"To mark as paid use create_payment(). Got: {status!r}"
            )

        raw = await self._patch(f"/quickbooks-desktop/invoices/{invoice_id}", payload)
        return self._normalize_invoice(raw)

    async def create_payment(
        self,
        customer_id: str,
        amount: Decimal,
        invoice_id: Optional[str] = None,
        payment_method: str = "check",
        memo: Optional[str] = None,
    ) -> Payment:
        """Record a customer payment via Conductor."""
        payload: dict = {
            "customerId": customer_id,
            "totalAmount": str(amount),
            "paymentMethod": payment_method,
            "transactionDate": date.today().isoformat(),
        }
        if invoice_id:
            payload["appliedToTransactions"] = [
                {"transactionId": invoice_id, "appliedAmount": str(amount)}
            ]
        if memo:
            payload["memo"] = memo

        raw = await self._post("/quickbooks-desktop/payments", payload)
        return Payment(
            id=raw["id"],
            qb_id=raw["id"],
            customer_id=raw.get("customer", {}).get("id", customer_id),
            customer_name=raw.get("customer", {}).get("name", ""),
            date=date.fromisoformat(raw["transactionDate"]) if raw.get("transactionDate") else date.today(),
            amount=Decimal(str(raw.get("totalAmount", amount))),
            invoice_id=invoice_id,
            payment_method=payment_method,
            memo=raw.get("memo"),
        )
