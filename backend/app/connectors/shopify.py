"""
Shopify connector via Shopify Admin REST API.
Pulls orders and products to enrich QB data with e-commerce context.

Company config keys:
  shopify_shop_domain  (e.g. "mystore.myshopify.com")
  shopify_access_token
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)


class ShopifyConnector:
    def __init__(self, shop_domain: str, access_token: str):
        self._shop = shop_domain.rstrip("/")
        self._token = access_token
        self._base = f"https://{self._shop}/admin/api/2024-01"

    def _headers(self) -> dict:
        return {"X-Shopify-Access-Token": self._token, "Content-Type": "application/json"}

    async def get_recent_orders(self, days: int = 30) -> list[dict]:
        """Fetch recent Shopify orders as a list of dicts."""
        import httpx
        since = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00Z"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base}/orders.json",
                    headers=self._headers(),
                    params={
                        "status": "any",
                        "created_at_min": since,
                        "limit": 250,
                        "fields": "id,name,created_at,email,total_price,financial_status,line_items,customer",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                orders = resp.json().get("orders", [])

            return [
                {
                    "shopify_order_id": str(o["id"]),
                    "order_number": o.get("name", ""),
                    "customer_email": o.get("email", ""),
                    "customer_name": self._customer_name(o),
                    "order_date": o.get("created_at", "")[:10],
                    "total_amount": float(o.get("total_price", 0)),
                    "status": o.get("financial_status", ""),
                    "item_count": sum(li.get("quantity", 0) for li in o.get("line_items", [])),
                    "source": "shopify",
                }
                for o in orders
            ]
        except Exception as exc:
            log.error("Shopify get_recent_orders failed: %s", exc)
            return []

    def _customer_name(self, order: dict) -> str:
        c = order.get("customer") or {}
        first = c.get("first_name", "")
        last = c.get("last_name", "")
        return f"{first} {last}".strip() or order.get("email", "Unknown")

    async def get_products(self, limit: int = 250) -> list[dict]:
        """Fetch product list — useful for cross-referencing with QB inventory."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base}/products.json",
                    headers=self._headers(),
                    params={"limit": limit, "fields": "id,title,handle,variants,status"},
                    timeout=15,
                )
                resp.raise_for_status()
                products = resp.json().get("products", [])

            result = []
            for p in products:
                for variant in p.get("variants", []):
                    result.append({
                        "shopify_product_id": str(p["id"]),
                        "shopify_variant_id": str(variant["id"]),
                        "title": p.get("title", ""),
                        "sku": variant.get("sku", ""),
                        "inventory_quantity": variant.get("inventory_quantity", 0),
                        "price": float(variant.get("price", 0)),
                        "source": "shopify",
                    })
            return result
        except Exception as exc:
            log.error("Shopify get_products failed: %s", exc)
            return []
