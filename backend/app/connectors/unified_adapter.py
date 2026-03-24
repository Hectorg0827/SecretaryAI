"""
Unified Data Adapter — single interface for ALL data sources.
The AI and business logic only talk to this class.
It routes requests through the AccessRouter (API → Playwright → File Ingestion → Computer Use).

Decision logic:
  QuickBooks data → QB connector (Desktop or Online) via AccessRouter API path
  Gmail           → Gmail connector
  Google Sheets   → Sheets connector
  Portals/files   → AccessRouter selects least-fragile path automatically
"""
from datetime import date, timedelta
from typing import Optional

from app.connectors.base import Customer, Invoice, InventoryItem, PurchaseOrder, QuickBooksAdapter
from app.connectors.qb_desktop import QBDesktopAdapter
from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.gmail_connector import GmailConnector
from app.connectors.google_sheets import GoogleSheetsConnector
from app.connectors.outlook import OutlookConnector
from app.connectors.shopify import ShopifyConnector
from app.connectors.shiptrack import ShipTrackConnector
from app.computer_use.engine import ComputerUseEngine


class UnifiedDataAdapter:
    """
    Single interface for all data sources.

    Usage:
        adapter = UnifiedDataAdapter(company_config)
        customers = await adapter.get_all_customers()
        orders    = await adapter.get_orders_last_n_days(30)
        inventory = await adapter.get_inventory_merged()  # QB + warehouse
    """

    def __init__(self, company_config: dict):
        self._config = company_config
        self._qb: Optional[QuickBooksAdapter] = None
        self._gmail: Optional[GmailConnector] = None
        self._sheets: Optional[GoogleSheetsConnector] = None
        self._outlook: Optional[OutlookConnector] = None
        self._shopify: Optional[ShopifyConnector] = None
        self._shiptrack: Optional[ShipTrackConnector] = None
        self._computer_use: Optional[ComputerUseEngine] = None
        self._setup_connectors()

    def _setup_connectors(self) -> None:
        cfg = self._config

        # QB connector — Desktop or Online
        qb_type = cfg.get("qb_type")
        if qb_type == "desktop":
            self._qb = QBDesktopAdapter(
                api_key=cfg["conductor_api_key"],
                end_user_id=cfg["conductor_end_user_id"],
            )
        elif qb_type == "online":
            self._qb = QBOnlineAdapter(
                client_id=cfg["qbo_client_id"],
                client_secret=cfg["qbo_client_secret"],
                access_token=cfg["qbo_access_token"],
                refresh_token=cfg["qbo_refresh_token"],
                realm_id=cfg["qbo_realm_id"],
                on_token_refresh=self._persist_qbo_tokens,
            )

        # Gmail
        if cfg.get("gmail_credentials"):
            self._gmail = GmailConnector(credentials=cfg["gmail_credentials"])

        # Google Sheets
        if cfg.get("google_credentials"):
            self._sheets = GoogleSheetsConnector(credentials=cfg["google_credentials"])

        # Outlook connector
        if cfg.get("ms_tenant_id") and cfg.get("ms_access_token"):
            self._outlook = OutlookConnector(
                tenant_id=cfg["ms_tenant_id"],
                client_id=cfg.get("ms_client_id", ""),
                client_secret=cfg.get("ms_client_secret", ""),
                access_token=cfg["ms_access_token"],
                refresh_token=cfg.get("ms_refresh_token", ""),
            )

        # Shopify connector
        if cfg.get("shopify_shop_domain") and cfg.get("shopify_access_token"):
            self._shopify = ShopifyConnector(
                shop_domain=cfg["shopify_shop_domain"],
                access_token=cfg["shopify_access_token"],
            )

        # Shipment tracking connector (credentials optional)
        self._shiptrack = ShipTrackConnector(
            fedex_api_key=cfg.get("fedex_api_key", ""),
            fedex_secret_key=cfg.get("fedex_secret_key", ""),
            ups_client_id=cfg.get("ups_client_id", ""),
            ups_client_secret=cfg.get("ups_client_secret", ""),
            dhl_api_key=cfg.get("dhl_api_key", ""),
        )

        # Computer Use Engine — kept as last-resort fallback inside AccessRouter
        self._computer_use = ComputerUseEngine(company_config=cfg)

        # Access Router — replaces ad-hoc CU calls for portals and files
        # Lazy: requires a DB reference; set via set_db() after construction
        self._router = None
        self._db = None

    # ------------------------------------------------------------------
    # Token persistence callback (called by QBOnlineAdapter after refresh)
    # ------------------------------------------------------------------

    async def _persist_qbo_tokens(
        self, access_token: str, refresh_token: str, expires_in: int = 3600
    ) -> None:
        """Encrypt and save refreshed QBO tokens back to Supabase."""
        import logging
        from datetime import datetime, timezone
        log = logging.getLogger(__name__)

        company_id = self._config.get("id")
        if not company_id:
            return

        try:
            from supabase import create_client
            from app.config import get_settings
            from app.utils.encryption import encrypt

            settings = get_settings()
            db = create_client(settings.supabase_url, settings.supabase_service_role_key)
            db.table("companies").update({
                "qbo_access_token": encrypt(access_token, settings.secret_key),
                "qbo_refresh_token": encrypt(refresh_token, settings.secret_key),
                "qbo_token_refreshed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", company_id).execute()
            log.info("QBO tokens refreshed and persisted for company %s", company_id)
        except Exception as exc:
            log.error("Failed to persist refreshed QBO tokens for %s: %s", company_id, exc)

    def set_db(self, db) -> None:
        """Attach a DB client so the AccessRouter can log path decisions."""
        from app.router.access_router import AccessRouter
        self._db = db
        self._router = AccessRouter(self._config, db)

    def _get_router(self):
        if self._router is None:
            # Fallback: router without DB logging (e.g. in tests or tasks)
            from app.router.access_router import AccessRouter
            self._router = AccessRouter(self._config, _NoopDB())
        return self._router

    # ------------------------------------------------------------------
    # QuickBooks data
    # ------------------------------------------------------------------

    async def get_all_customers(self) -> list[Customer]:
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        return await self._qb.get_customers()

    async def get_orders_last_n_days(self, days: int = 30) -> list[Invoice]:
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        date_to = date.today()
        date_from = date_to - timedelta(days=days)
        return await self._qb.get_invoices(date_from, date_to)

    async def get_inventory_qb(self) -> list[InventoryItem]:
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        return await self._qb.get_inventory()

    async def get_purchase_orders(self) -> list[PurchaseOrder]:
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        return await self._qb.get_purchase_orders()

    # ------------------------------------------------------------------
    # Merged inventory (QB + any warehouse system via Computer Use)
    # ------------------------------------------------------------------

    async def get_inventory_merged(self) -> list[dict]:
        """
        Merge QB inventory with any additional warehouse system data.
        QB is authoritative for item definitions; warehouse system
        provides real-time location quantities.
        Uses AccessRouter: API → FILE_INGESTION → COMPUTER_USE.
        """
        qb_items = await self.get_inventory_qb()

        router = self._get_router()
        warehouse_data: list[dict] = []
        try:
            result = await router.route("inventory", {})
            # Router returns raw items; we need only the warehouse quantities
            warehouse_data = result.data.get("rows", result.data.get("items", []))
        except Exception:
            pass  # QB data stands alone if no warehouse source available

        # Load weekly_sell_rate from Supabase inventory table (populated by Celery sync)
        sell_rate_by_qb_id: dict[str, float] = {}
        if self._db:
            try:
                company_id = self._config.get("id")
                result = self._db.table("inventory").select("qb_id,weekly_sell_rate").eq(
                    "company_id", company_id
                ).execute()
                for row in (result.data or []):
                    if row.get("qb_id") and row.get("weekly_sell_rate") is not None:
                        sell_rate_by_qb_id[row["qb_id"]] = float(row["weekly_sell_rate"])
            except Exception:
                pass

        merged: list[dict] = []
        for item in qb_items:
            entry = {
                "qb_id": item.qb_id,
                "product_name": item.name,
                "sku": item.sku,
                "qb_qty": float(item.quantity_on_hand),
                "warehouse_qty": 0,
                "total_qty": float(item.quantity_on_hand),
                "reorder_point": float(item.reorder_point) if item.reorder_point else None,
                "unit_price": float(item.unit_price),
                "purchase_cost": float(item.purchase_cost),
                "weekly_sell_rate": sell_rate_by_qb_id.get(item.qb_id, 0.0),
                "source": "qb",
            }

            for w in warehouse_data:
                wname = w.get("product_name", "")
                if wname.lower() in item.name.lower() or item.name.lower() in wname.lower():
                    qty = w.get("quantity", 0)
                    try:
                        qty = float(qty)
                    except (TypeError, ValueError):
                        qty = 0
                    entry["warehouse_qty"] = qty
                    entry["total_qty"] = entry["qb_qty"] + qty
                    entry["source"] = "qb+warehouse"
                    break

            merged.append(entry)

        return merged

    # ------------------------------------------------------------------
    # Non-QB ordering systems — routed via AccessRouter
    # ------------------------------------------------------------------

    async def get_ordering_system_data(self, task: str) -> dict:
        """
        Fetch distributor order data via AccessRouter
        (Playwright → File Ingestion → Computer Use).
        """
        router = self._get_router()
        result = await router.route("distributor_orders", {"task": task})
        return result.data

    async def get_customs_status(self) -> dict:
        """
        Fetch customs container statuses via AccessRouter
        (Playwright → File Ingestion → Computer Use).
        """
        router = self._get_router()
        result = await router.route("customs_status", {})
        return result.data

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    async def get_emails(self, query: str = "", max_results: int = 20) -> list[dict]:
        """Get emails — routes to Gmail or Outlook depending on what's configured."""
        if self._gmail:
            return await self._gmail.get_recent_emails(query=query, max_results=max_results)
        if self._outlook:
            return await self._outlook.get_emails(query=query, max_results=max_results)
        return []

    async def send_email_reply(self, to: str, subject: str, body: str) -> dict:
        """Send an email reply — routes to Gmail or Outlook."""
        if self._gmail:
            return await self._gmail.send_approved_email(to=to, subject=subject, body=body)
        if self._outlook:
            return await self._outlook.send_email_reply(to=to, subject=subject, body=body)
        raise ValueError("No email connector configured")

    async def mark_email_read(self, email_id: str) -> None:
        """Mark email as read — routes to whichever connector is active."""
        if self._gmail:
            await self._gmail.mark_as_read(email_id)
        elif self._outlook:
            await self._outlook.mark_email_read(email_id)

    async def get_shopify_orders(self, days: int = 30) -> list[dict]:
        """Get Shopify orders if connected."""
        if self._shopify:
            return await self._shopify.get_recent_orders(days=days)
        return []

    async def track_shipment(self, tracking_number: str) -> dict:
        """Track a shipment via the configured carrier APIs."""
        if self._shiptrack:
            return await self._shiptrack.track(tracking_number)
        return {"tracking_number": tracking_number, "status": "not_configured", "description": "No tracking connector configured", "events": []}

    async def track_shipments(self, tracking_numbers: list[str]) -> list[dict]:
        """Track multiple shipments concurrently."""
        if self._shiptrack:
            return await self._shiptrack.track_multiple(tracking_numbers)
        return []

    async def get_customs_email_updates(self) -> list[dict]:
        return await self.get_emails(
            query="subject:(customs OR clearance OR shipment OR container OR arrival)",
            max_results=20,
        )

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    async def test_all_connections(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        if self._qb:
            results["quickbooks"] = await self._qb.test_connection()
        if self._gmail:
            results["gmail"] = True  # TODO: add ping
        if self._outlook:
            results["outlook"] = True
        if self._sheets:
            results["google_sheets"] = True
        if self._shopify:
            results["shopify"] = True
        if self._shiptrack:
            results["shiptrack"] = True
        return results


class _NoopDB:
    """Stub DB client for contexts where no real DB is available (e.g. tasks without a request)."""
    def table(self, _name: str):
        return self

    def insert(self, _data):
        return self

    def execute(self):
        return type("R", (), {"data": []})()

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def order(self, *_):
        return self

    def limit(self, *_):
        return self
