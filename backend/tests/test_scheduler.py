"""
Tests for scheduler modules: inventory_alert_check, morning_briefing.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


# ─── Inventory Alert Check ──────────────────────────────────────────────────────

class TestInventoryAlertCheck:
    def _make_adapter(self, inventory):
        adapter = AsyncMock()
        adapter.get_inventory_merged.return_value = inventory
        return adapter

    def _make_engine(self):
        engine = AsyncMock()
        engine.process.return_value = {"status": "executed"}
        return engine

    @pytest.mark.asyncio
    async def test_no_alerts_for_healthy_inventory(self):
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        inventory = [
            {"product_name": "Widget A", "warehouse_qty": 250, "qb_qty": 250,
             "weekly_sell_rate": 10, "qb_id": "ITEM001"},
        ]
        adapter = self._make_adapter(inventory)
        engine = self._make_engine()
        result = await check_and_alert_inventory("co-1", adapter, engine, "user-1")
        assert result["critical"] == []
        assert result["low"] == []
        assert result["alerts_fired"] == 0
        engine.process.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_critical_item_fires_alert(self):
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        # Gadget X: qty=2, sell_rate=10 → critically low
        inventory = [
            {"product_name": "Gadget X", "warehouse_qty": 0, "qb_qty": 2,
             "weekly_sell_rate": 10, "qb_id": "ITEM003"},
        ]
        adapter = self._make_adapter(inventory)
        engine = self._make_engine()

        with patch("app.actions.email_actions.send_alert_email", new_callable=AsyncMock) as mock_email:
            mock_email.return_value = {"status": "sent"}
            result = await check_and_alert_inventory("co-1", adapter, engine, "user-1")

        assert "Gadget X" in result["critical"]
        assert result["alerts_fired"] == 1
        engine.process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_adapter_failure_returns_empty_results(self):
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        adapter = AsyncMock()
        adapter.get_inventory_merged.side_effect = Exception("DB connection failed")
        engine = self._make_engine()
        result = await check_and_alert_inventory("co-1", adapter, engine, "user-1")
        assert result["critical"] == []
        assert result["low"] == []
        assert result["alerts_fired"] == 0

    @pytest.mark.asyncio
    async def test_no_engine_skips_alert(self):
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        inventory = [
            {"product_name": "Gadget X", "warehouse_qty": 0, "qb_qty": 2,
             "weekly_sell_rate": 10, "qb_id": "ITEM003"},
        ]
        adapter = self._make_adapter(inventory)
        # No action engine provided
        result = await check_and_alert_inventory("co-1", adapter, None, "user-1")
        assert "Gadget X" in result["critical"]
        assert result["alerts_fired"] == 0

    @pytest.mark.asyncio
    async def test_multiple_critical_items_single_alert(self):
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        inventory = [
            {"product_name": f"Widget {i}", "warehouse_qty": 0, "qb_qty": 1,
             "weekly_sell_rate": 10, "qb_id": f"ITEM{i:03d}"}
            for i in range(3)
        ]
        adapter = self._make_adapter(inventory)
        engine = self._make_engine()

        with patch("app.actions.email_actions.send_alert_email", new_callable=AsyncMock) as mock_email:
            mock_email.return_value = {"status": "sent"}
            result = await check_and_alert_inventory("co-1", adapter, engine, "user-1")

        assert len(result["critical"]) == 3
        assert result["alerts_fired"] == 1  # Only one consolidated alert

    @pytest.mark.asyncio
    async def test_alert_engine_failure_handled_gracefully(self):
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        inventory = [
            {"product_name": "Widget X", "warehouse_qty": 0, "qb_qty": 1,
             "weekly_sell_rate": 10, "qb_id": "ITEM001"},
        ]
        adapter = self._make_adapter(inventory)
        engine = AsyncMock()
        engine.process.side_effect = Exception("Action engine failure")
        # Should not raise — failure is logged and handled
        result = await check_and_alert_inventory("co-1", adapter, engine, "user-1")
        assert result["alerts_fired"] == 0


# ─── Morning Briefing ──────────────────────────────────────────────────────────

class TestMorningBriefing:
    @pytest.mark.asyncio
    async def test_generates_briefing_text(self):
        from app.scheduler.morning_briefing import generate_morning_briefing

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Good morning! Here is your daily briefing.")]

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("app.scheduler.morning_briefing.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await generate_morning_briefing(
                company_id="co-1",
                company_name="Test Co",
                preferred_language="English",
                data_summary="Revenue: $50k. 3 open invoices.",
                recipient_email="admin@testco.com",
            )

        assert result == "Good morning! Here is your daily briefing."
        mock_client.messages.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_company_name_in_prompt(self):
        from app.scheduler.morning_briefing import generate_morning_briefing

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Briefing text")]

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("app.scheduler.morning_briefing.anthropic.AsyncAnthropic", return_value=mock_client):
            await generate_morning_briefing(
                company_id="co-1",
                company_name="Acme Corp",
                preferred_language="Spanish",
                data_summary="summary data",
                recipient_email="admin@acme.com",
            )

        call_kwargs = mock_client.messages.create.call_args
        # Verify the prompt contains company_name and language
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        prompt_text = messages[0]["content"]
        assert "Acme Corp" in prompt_text
        assert "Spanish" in prompt_text


# ─── Connector Base Dataclasses ────────────────────────────────────────────────

class TestConnectorDataclasses:
    def test_customer_creation(self):
        from decimal import Decimal
        from app.connectors.base import Customer
        c = Customer(
            id="c1", qb_id="QB001", name="Acme",
            email="a@acme.com", phone="555-1234", state="CA",
            balance=Decimal("1000"), total_sales=Decimal("50000"),
        )
        assert c.id == "c1"
        assert c.name == "Acme"
        assert c.balance == Decimal("1000")

    def test_invoice_creation(self):
        from decimal import Decimal
        from datetime import date
        from app.connectors.base import Invoice
        inv = Invoice(
            id="i1", qb_id="INV001", customer_id="QB001", customer_name="Acme",
            date=date(2025, 1, 1), due_date=date(2025, 1, 31),
            total=Decimal("5000"), balance=Decimal("5000"),
            status="open", line_items=[],
        )
        assert inv.status == "open"
        assert inv.total == Decimal("5000")

    def test_inventory_item_creation(self):
        from decimal import Decimal
        from app.connectors.base import InventoryItem
        item = InventoryItem(
            id="it1", qb_id="ITEM001", name="Widget A", sku="WGT-A",
            quantity_on_hand=Decimal("100"), unit_price=Decimal("50"),
            purchase_cost=Decimal("22.50"), reorder_point=Decimal("30"),
        )
        assert item.quantity_on_hand == Decimal("100")
        assert item.reorder_point == Decimal("30")

    def test_purchase_order_creation(self):
        from decimal import Decimal
        from datetime import date
        from app.connectors.base import PurchaseOrder
        po = PurchaseOrder(
            id="po1", qb_id="PO001", vendor_id="v1", vendor_name="Supplier Co",
            date=date(2025, 1, 1), expected_date=date(2025, 2, 15),
            total=Decimal("10000"), status="open", line_items=[],
        )
        assert po.vendor_name == "Supplier Co"
        assert po.status == "open"

    def test_quickbooks_adapter_is_abstract(self):
        from app.connectors.base import QuickBooksAdapter
        import inspect
        assert inspect.isabstract(QuickBooksAdapter)
        abstract_methods = QuickBooksAdapter.__abstractmethods__
        assert "get_customers" in abstract_methods
        assert "get_invoices" in abstract_methods
        assert "get_inventory" in abstract_methods
        assert "get_purchase_orders" in abstract_methods
        assert "test_connection" in abstract_methods
