"""
QuickBooks Online connector via Intuit REST API + OAuth 2.0.
No desktop app required — all cloud-to-cloud.
"""
from datetime import date, datetime, timedelta
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

QBO_BASE = "https://quickbooks.api.intuit.com/v3/company"
QBO_OAUTH_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


class QBOnlineAdapter(QuickBooksAdapter):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        realm_id: str,
        on_token_refresh=None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._realm_id = realm_id
        self._on_token_refresh = on_token_refresh  # callback to persist new tokens

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _refresh_access_token(self) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                QBO_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            tokens = response.json()
            self._access_token = tokens["access_token"]
            self._refresh_token = tokens["refresh_token"]

            if self._on_token_refresh:
                await self._on_token_refresh(
                    access_token=self._access_token,
                    refresh_token=self._refresh_token,
                    expires_in=tokens.get("expires_in", 3600),
                )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _post(self, entity: str, payload: dict) -> dict:
        """POST (create) a QBO entity."""
        url = f"{QBO_BASE}/{self._realm_id}/{entity}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                json=payload,
                params={"minorversion": "70"},
                headers=self._headers(),
            )
            if response.status_code == 401:
                await self._refresh_access_token()
                response = await client.post(
                    url,
                    json=payload,
                    params={"minorversion": "70"},
                    headers=self._headers(),
                )
            response.raise_for_status()
            return response.json()

    async def _get_entity(self, entity: str, entity_id: str) -> dict:
        """GET a single QBO entity by ID (needed to obtain SyncToken for updates)."""
        url = f"{QBO_BASE}/{self._realm_id}/{entity}/{entity_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                url,
                params={"minorversion": "70"},
                headers=self._headers(),
            )
            if response.status_code == 401:
                await self._refresh_access_token()
                response = await client.get(
                    url,
                    params={"minorversion": "70"},
                    headers=self._headers(),
                )
            response.raise_for_status()
            return response.json()

    async def _query(self, sql: str) -> dict:
        """Execute a QBO query using Intuit's SQL-like query language."""
        url = f"{QBO_BASE}/{self._realm_id}/query"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                url,
                params={"query": sql, "minorversion": "70"},
                headers=self._headers(),
            )

            if response.status_code == 401:
                await self._refresh_access_token()
                response = await client.get(
                    url,
                    params={"query": sql, "minorversion": "70"},
                    headers=self._headers(),
                )

            response.raise_for_status()
            return response.json()

    async def _query_all(self, entity: str, where: str = "") -> list[dict]:
        """Paginate through all results for a QBO entity."""
        results: list[dict] = []
        page_size = 1000
        start = 1

        while True:
            where_clause = f"WHERE {where} " if where else ""
            sql = f"SELECT * FROM {entity} {where_clause}STARTPOSITION {start} MAXRESULTS {page_size}"
            data = await self._query(sql)

            query_response = data.get("QueryResponse", {})
            items = query_response.get(entity, [])
            results.extend(items)

            if len(items) < page_size:
                break
            start += page_size

        return results

    # ------------------------------------------------------------------
    # Normalizers
    # ------------------------------------------------------------------

    def _normalize_customer(self, raw: dict) -> Customer:
        billing = raw.get("BillAddr", {})
        return Customer(
            id=raw["Id"],
            qb_id=raw["Id"],
            name=raw.get("DisplayName", ""),
            email=raw.get("PrimaryEmailAddr", {}).get("Address"),
            phone=raw.get("PrimaryPhone", {}).get("FreeFormNumber"),
            state=billing.get("CountrySubDivisionCode"),
            balance=Decimal(str(raw.get("Balance", 0))),
            total_sales=Decimal(str(raw.get("BalanceWithJobs", 0))),
        )

    def _normalize_invoice(self, raw: dict) -> Invoice:
        line_items = [
            {
                "description": li.get("Description", ""),
                "quantity": li.get("SalesItemLineDetail", {}).get("Qty", 0),
                "unit_price": li.get("SalesItemLineDetail", {}).get("UnitPrice", 0),
                "amount": li.get("Amount", 0),
            }
            for li in raw.get("Line", [])
            if li.get("DetailType") == "SalesItemLineDetail"
        ]
        return Invoice(
            id=raw["Id"],
            qb_id=raw["Id"],
            customer_id=raw.get("CustomerRef", {}).get("value", ""),
            customer_name=raw.get("CustomerRef", {}).get("name", ""),
            date=date.fromisoformat(raw["TxnDate"]) if raw.get("TxnDate") else date.today(),
            due_date=date.fromisoformat(raw["DueDate"]) if raw.get("DueDate") else None,
            total=Decimal(str(raw.get("TotalAmt", 0))),
            balance=Decimal(str(raw.get("Balance", 0))),
            status="open" if Decimal(str(raw.get("Balance", 0))) > 0 else "paid",
            line_items=line_items,
        )

    def _normalize_inventory_item(self, raw: dict) -> InventoryItem:
        return InventoryItem(
            id=raw["Id"],
            qb_id=raw["Id"],
            name=raw.get("Name", ""),
            sku=raw.get("Sku"),
            quantity_on_hand=Decimal(str(raw.get("QtyOnHand", 0))),
            unit_price=Decimal(str(raw.get("UnitPrice", 0))),
            purchase_cost=Decimal(str(raw.get("PurchaseCost", 0))),
            reorder_point=Decimal(str(raw["ReorderPoint"])) if raw.get("ReorderPoint") else None,
        )

    def _normalize_purchase_order(self, raw: dict) -> PurchaseOrder:
        return PurchaseOrder(
            id=raw["Id"],
            qb_id=raw["Id"],
            vendor_id=raw.get("VendorRef", {}).get("value", ""),
            vendor_name=raw.get("VendorRef", {}).get("name", ""),
            date=date.fromisoformat(raw["TxnDate"]) if raw.get("TxnDate") else date.today(),
            expected_date=None,
            total=Decimal(str(raw.get("TotalAmt", 0))),
            status="open",
            line_items=raw.get("Line", []),
        )

    # ------------------------------------------------------------------
    # Adapter methods
    # ------------------------------------------------------------------

    async def get_customers(self) -> list[Customer]:
        raw = await self._query_all("Customer", "Active = True")
        return [self._normalize_customer(c) for c in raw]

    async def get_invoices(self, date_from: date, date_to: date) -> list[Invoice]:
        where = f"TxnDate >= '{date_from.isoformat()}' AND TxnDate <= '{date_to.isoformat()}'"
        raw = await self._query_all("Invoice", where)
        return [self._normalize_invoice(i) for i in raw]

    async def get_inventory(self) -> list[InventoryItem]:
        raw = await self._query_all("Item", "Type = 'Inventory'")
        return [self._normalize_inventory_item(i) for i in raw]

    async def get_purchase_orders(self) -> list[PurchaseOrder]:
        raw = await self._query_all("PurchaseOrder")
        return [self._normalize_purchase_order(p) for p in raw]

    async def test_connection(self) -> bool:
        try:
            await self._query("SELECT COUNT(*) FROM Customer")
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
        """Create a PurchaseOrder in QBO."""
        qbo_lines = []
        for i, item in enumerate(line_items):
            line: dict = {
                "Id": str(i + 1),
                "DetailType": "ItemBasedExpenseLineDetail",
                "Amount": float(item.get("unit_cost", 0)) * float(item.get("quantity", 1)),
                "ItemBasedExpenseLineDetail": {
                    "ItemRef": {"value": item["item_id"]},
                    "Qty": float(item.get("quantity", 1)),
                    "UnitPrice": float(item.get("unit_cost", 0)),
                },
            }
            if item.get("description"):
                line["Description"] = item["description"]
            qbo_lines.append(line)

        payload: dict = {
            "VendorRef": {"value": vendor_id},
            "Line": qbo_lines,
            "TxnDate": date.today().isoformat(),
        }
        if ship_date:
            payload["ShipDate"] = ship_date.isoformat()
        if memo:
            payload["Memo"] = memo

        data = await self._post("purchaseorder", payload)
        raw = data.get("PurchaseOrder", data)
        return self._normalize_purchase_order(raw)

    async def update_invoice_status(
        self,
        invoice_id: str,
        status: str,
    ) -> Invoice:
        """
        Update invoice status in QBO.
        - "void": voids the invoice using QBO's void operation.
        Any other status raises ValueError (use create_payment() to mark as paid).
        """
        if status != "void":
            raise ValueError(
                f"QBO only supports status='void' via update_invoice_status. "
                f"To mark as paid use create_payment(). Got: {status!r}"
            )

        # QBO requires the current SyncToken to void
        data = await self._get_entity("invoice", invoice_id)
        inv = data.get("Invoice", data)
        sync_token = inv.get("SyncToken", "0")

        payload = {
            "Id": invoice_id,
            "SyncToken": sync_token,
            "sparse": True,
        }
        url = f"{QBO_BASE}/{self._realm_id}/invoice"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                json=payload,
                params={"minorversion": "70", "operation": "void"},
                headers=self._headers(),
            )
            if response.status_code == 401:
                await self._refresh_access_token()
                response = await client.post(
                    url,
                    json=payload,
                    params={"minorversion": "70", "operation": "void"},
                    headers=self._headers(),
                )
            response.raise_for_status()
            raw = response.json().get("Invoice", response.json())
        return self._normalize_invoice(raw)

    async def create_payment(
        self,
        customer_id: str,
        amount: Decimal,
        invoice_id: Optional[str] = None,
        payment_method: str = "check",
        memo: Optional[str] = None,
    ) -> Payment:
        """Record a customer payment in QBO, optionally linked to an invoice."""
        payload: dict = {
            "CustomerRef": {"value": customer_id},
            "TotalAmt": float(amount),
            "TxnDate": date.today().isoformat(),
        }

        if invoice_id:
            payload["Line"] = [
                {
                    "Amount": float(amount),
                    "LinkedTxn": [{"TxnId": invoice_id, "TxnType": "Invoice"}],
                }
            ]

        # Map payment method string to QBO PaymentMethodRef (best-effort lookup)
        _PM_MAP = {
            "check": "1",
            "cash": "2",
            "credit_card": "3",
            "ach": "4",
            "wire": "4",
        }
        pm_ref = _PM_MAP.get(payment_method.lower())
        if pm_ref:
            payload["PaymentMethodRef"] = {"value": pm_ref}

        if memo:
            payload["PrivateNote"] = memo

        data = await self._post("payment", payload)
        raw = data.get("Payment", data)
        return Payment(
            id=raw["Id"],
            qb_id=raw["Id"],
            customer_id=raw.get("CustomerRef", {}).get("value", customer_id),
            customer_name=raw.get("CustomerRef", {}).get("name", ""),
            date=date.fromisoformat(raw["TxnDate"]) if raw.get("TxnDate") else date.today(),
            amount=Decimal(str(raw.get("TotalAmt", amount))),
            invoice_id=invoice_id,
            payment_method=payment_method,
            memo=raw.get("PrivateNote"),
        )
