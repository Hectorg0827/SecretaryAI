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

from app.connectors.base import Customer, Invoice, InventoryItem, Payment, PurchaseOrder, QuickBooksAdapter
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
                on_token_refresh=self._persist_outlook_tokens,
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
        """Durably persist rotating tokens or fail the refresh as incomplete."""
        import logging
        from datetime import datetime, timezone
        log = logging.getLogger(__name__)

        company_id = self._config.get("id")
        if not company_id:
            raise RuntimeError("Cannot persist QBO token refresh without a company identifier")

        try:
            from supabase import create_client
            from app.config import get_settings
            from app.utils.encryption import encrypt

            settings = get_settings()
            db = create_client(settings.supabase_url, settings.supabase_service_role_key)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            db.table("companies").update({
                "qbo_access_token": encrypt(access_token, settings.secret_key),
                "qbo_refresh_token": encrypt(refresh_token, settings.secret_key),
                "qbo_token_refreshed_at": datetime.now(timezone.utc).isoformat(),
                "qbo_token_expires_at": expires_at.isoformat(),
            }).eq("id", company_id).execute()
            log.info("QBO tokens refreshed and persisted for company %s", company_id)
        except Exception as exc:
            log.error("Failed to persist refreshed QBO tokens for company %s", company_id)
            raise RuntimeError("QBO token refresh could not be persisted") from exc

    async def _persist_outlook_tokens(self, access_token: str, refresh_token: str) -> None:
        """Persist rotating Microsoft credentials before treating refresh as complete."""
        import logging

        log = logging.getLogger(__name__)
        company_id = self._config.get("id")
        if not company_id:
            raise RuntimeError("Cannot persist Outlook token refresh without a company identifier")
        try:
            from supabase import create_client
            from app.config import get_settings
            from app.utils.encryption import encrypt

            settings = get_settings()
            db = create_client(settings.supabase_url, settings.supabase_service_role_key)
            result = db.table("companies").update({
                "ms_access_token": encrypt(access_token, settings.secret_key),
                "ms_refresh_token": encrypt(refresh_token, settings.secret_key),
            }).eq("id", company_id).execute()
            if not getattr(result, "data", None):
                raise RuntimeError("Outlook token persistence matched no company")
            log.info("Outlook tokens refreshed and persisted for a company")
        except Exception as exc:
            log.error("Outlook token refresh could not be persisted")
            raise RuntimeError("Outlook token refresh could not be persisted") from exc

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

    async def create_purchase_order(
        self,
        vendor_id: str,
        line_items: list[dict],
        ship_date: Optional[date] = None,
        memo: Optional[str] = None,
    ) -> PurchaseOrder:
        """Create a PO in QuickBooks (Desktop or Online)."""
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        return await self._qb.create_purchase_order(
            vendor_id=vendor_id,
            line_items=line_items,
            ship_date=ship_date,
            memo=memo,
        )

    async def update_invoice_status(
        self,
        invoice_id: str,
        status: str,
    ) -> Invoice:
        """Update invoice status in QuickBooks (currently supports 'void')."""
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        return await self._qb.update_invoice_status(invoice_id=invoice_id, status=status)

    async def create_payment(
        self,
        customer_id: str,
        amount,
        invoice_id: Optional[str] = None,
        payment_method: str = "check",
        memo: Optional[str] = None,
    ) -> Payment:
        """Record a customer payment in QuickBooks."""
        if not self._qb:
            raise RuntimeError("No QB connector configured")
        from decimal import Decimal
        return await self._qb.create_payment(
            customer_id=customer_id,
            amount=Decimal(str(amount)),
            invoice_id=invoice_id,
            payment_method=payment_method,
            memo=memo,
        )

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

        policy = self._config.get("inventory_quantity_policy", "qb_authoritative")
        if policy not in ("qb_authoritative", "warehouse_authoritative"):
            raise ValueError("Unknown inventory quantity authority policy")

        router = self._get_router()
        warehouse_data: list[dict] = []
        warehouse_source_status = "unavailable"
        warehouse_source_path = None
        warehouse_fetched_at = None
        try:
            result = await router.route("inventory", {})
            if getattr(result, "is_ok", True) is False:
                raise RuntimeError("Warehouse read returned a source error")
            source = getattr(result, "source", None)
            warehouse_source_path = getattr(source, "value", None)
            fetched_at = getattr(result, "fetched_at", None)
            warehouse_fetched_at = fetched_at.isoformat() if fetched_at else None
            # AccessRouter's default inventory API path queries QuickBooks
            # again. It is NOT an independent warehouse observation.
            if warehouse_source_path == "api":
                warehouse_source_status = "not_independent"
            else:
                payload = result.data
                if isinstance(payload, list):
                    warehouse_data = payload
                elif isinstance(payload, dict):
                    warehouse_data = payload.get("rows", payload.get("items", []))
                else:
                    raise ValueError("Warehouse response lacks item data")
                if not isinstance(warehouse_data, list):
                    raise ValueError("Warehouse response is not a list of items")
                file_is_outdated = (
                    warehouse_source_path == "file"
                    and getattr(result, "confidence", 100) <= 45
                )
                if getattr(result, "is_stale", False) or file_is_outdated:
                    warehouse_source_status = "stale"
                else:
                    warehouse_source_status = "available" if warehouse_data else "missing"
        except Exception:
            warehouse_source_status = "unavailable"
        if policy == "warehouse_authoritative" and warehouse_source_status in (
            "unavailable", "not_independent", "missing", "stale"
        ):
            raise RuntimeError("Required independent warehouse source unavailable or stale")

        # Load weekly_sell_rate from Supabase inventory table (populated by Celery sync)
        sell_rate_by_qb_id: dict[str, float] = {}
        sell_rate_source_status = "missing"
        if self._db:
            try:
                company_id = self._config.get("id")
                result = self._db.table("inventory").select("qb_id,weekly_sell_rate").eq(
                    "company_id", company_id
                ).execute()
                for row in (result.data or []):
                    if row.get("qb_id") and row.get("weekly_sell_rate") is not None:
                        value = float(row["weekly_sell_rate"])
                        if 0 <= value < float("inf"):
                            sell_rate_by_qb_id[row["qb_id"]] = value
            except Exception:
                sell_rate_source_status = "unavailable"

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
                # Legacy numeric fields remain compatible, but are not proof of
                # a real zero unless the corresponding observed flag is true.
                "weekly_sell_rate_observed": item.qb_id in sell_rate_by_qb_id,
                "weekly_sell_rate_source_status": (
                    "observed" if item.qb_id in sell_rate_by_qb_id else sell_rate_source_status
                ),
                "warehouse_qty_observed": False,
                "warehouse_source_status": warehouse_source_status,
                "warehouse_source_path": warehouse_source_path,
                "warehouse_fetched_at": warehouse_fetched_at,
                "source_quality": "partial",
                "source": "qb",
            }

            # Do not add QB and warehouse counts together: they normally
            # describe the *same* physical stock. No additive policy is
            # supported until locations and lot ownership are reconciled.
            entry["quantity_source"] = "quickbooks"
            entry["reconciliation_required"] = False
            entry["inventory_quantity_policy"] = policy
            for w in warehouse_data:
                if not isinstance(w, dict):
                    continue
                wsku = str(w.get("sku") or "").strip().casefold()
                is_same_sku = bool(item.sku and wsku and item.sku.strip().casefold() == wsku)
                is_same_name = (
                    not wsku
                    and str(w.get("product_name") or "").strip().casefold()
                    == item.name.strip().casefold()
                )
                if is_same_sku or is_same_name:
                    try:
                        qty = float(w.get("quantity", 0))
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= qty < float("inf"):
                        continue
                    entry["warehouse_qty"] = qty
                    entry["warehouse_qty_observed"] = True
                    entry["warehouse_source_status"] = (
                        "stale" if warehouse_source_status == "stale" else "observed"
                    )
                    entry["source"] = "qb+warehouse"
                    entry["reconciliation_required"] = qty != entry["qb_qty"]
                    if policy == "warehouse_authoritative":
                        entry["total_qty"] = qty
                        entry["quantity_source"] = "warehouse"
                    # Default: QB stays authoritative until the business
                    # explicitly selects an authoritative warehouse source.
                    break

            if not entry["warehouse_qty_observed"] and warehouse_source_status == "available":
                entry["warehouse_source_status"] = "missing"
            if policy == "warehouse_authoritative" and not entry["warehouse_qty_observed"]:
                raise RuntimeError("Required warehouse quantity missing or invalid")

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
        raise RuntimeError("No email connector configured")

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
            try:
                credentials = getattr(self._gmail, "_creds_dict", None)
                token = credentials.get("token") if isinstance(credentials, dict) else getattr(self._gmail, "_token", None)
                if not isinstance(token, str) or not token:
                    results["gmail"] = False
                else:
                    async with __import__("httpx").AsyncClient(timeout=5) as client:
                        r = await client.get(
                            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        results["gmail"] = r.status_code == 200
            except Exception:
                results["gmail"] = False
        if self._outlook:
            health_check = getattr(self._outlook, "test_connection", None)
            try:
                results["outlook"] = bool(await health_check()) if callable(health_check) else False
            except Exception:
                results["outlook"] = False
        if self._sheets:
            results["google_sheets"] = False  # no probe implemented; unverified is not healthy
        if self._shopify:
            results["shopify"] = False  # no probe implemented; unverified is not healthy
        if self._shiptrack:
            results["shiptrack"] = False  # carrier configuration does not establish connectivity
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
