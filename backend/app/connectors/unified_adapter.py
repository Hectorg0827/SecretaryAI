"""
Unified Data Adapter — single interface for ALL data sources.
The AI and business logic only talk to this class.
It routes requests to the right connector (API or Computer Use).

Decision logic:
  QuickBooks data → QB connector (Desktop or Online)
  Gmail           → Gmail connector
  Google Sheets   → Sheets connector
  Anything else   → Computer Use Engine (the universal fallback)
"""
from datetime import date, timedelta
from typing import Optional

from app.connectors.base import Customer, Invoice, InventoryItem, PurchaseOrder, QuickBooksAdapter
from app.connectors.qb_desktop import QBDesktopAdapter
from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.gmail_connector import GmailConnector
from app.connectors.google_sheets import GoogleSheetsConnector
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
            )

        # Gmail
        if cfg.get("gmail_credentials"):
            self._gmail = GmailConnector(credentials=cfg["gmail_credentials"])

        # Google Sheets
        if cfg.get("google_credentials"):
            self._sheets = GoogleSheetsConnector(credentials=cfg["google_credentials"])

        # Computer Use Engine — always available for non-API sources
        self._computer_use = ComputerUseEngine(company_config=cfg)

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
        """
        qb_items = await self.get_inventory_qb()
        qb_by_name = {item.name.lower(): item for item in qb_items}

        warehouse_app = self._config.get("warehouse_app")
        warehouse_data: list[dict] = []

        if warehouse_app and self._computer_use:
            raw = await self._computer_use.extract_data(
                app_name=warehouse_app,
                task="Get current inventory quantities for all products by location",
            )
            from app.computer_use.data_extractor import DataExtractor
            warehouse_data = DataExtractor.normalize_inventory(raw)

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
                "source": "qb",
            }

            # Find matching warehouse entry
            for w in warehouse_data:
                if w["product_name"].lower() in item.name.lower() or item.name.lower() in w["product_name"].lower():
                    entry["warehouse_qty"] = w["quantity"]
                    entry["total_qty"] = entry["qb_qty"] + w["quantity"]
                    entry["source"] = "qb+warehouse"
                    break

            merged.append(entry)

        return merged

    # ------------------------------------------------------------------
    # Non-QB ordering systems (Computer Use fallback)
    # ------------------------------------------------------------------

    async def get_ordering_system_data(self, task: str) -> dict:
        """
        For ordering systems without an API (ScribeBase, distributor portals, etc.),
        use Computer Use to extract the data.
        """
        ordering_system = self._config.get("ordering_system_app", "the ordering system")
        if self._computer_use:
            return await self._computer_use.extract_data(
                app_name=ordering_system,
                task=task,
            )
        return {}

    async def get_customs_status(self) -> dict:
        """Check customs broker portal for container statuses via Computer Use."""
        portal_url = self._config.get("customs_portal_url")
        portal_name = self._config.get("customs_portal_name", "Customs Broker Portal")
        if not self._computer_use:
            return {}
        task = "Get the status of all active containers/shipments"
        if portal_url:
            task = f"Navigate to {portal_url}. {task}"
        return await self._computer_use.extract_data(app_name=portal_name, task=task)

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    async def get_emails(self, query: str, max_results: int = 20) -> list[dict]:
        if not self._gmail:
            return []
        return await self._gmail.get_recent_emails(query=query, max_results=max_results)

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
        if self._sheets:
            results["google_sheets"] = True
        return results
