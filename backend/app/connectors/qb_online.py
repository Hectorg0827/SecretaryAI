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
