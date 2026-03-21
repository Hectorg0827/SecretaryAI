"""
Tests for the actions module: audit logger, PO generator, action engine.
"""
import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


# ─── Audit Tests ───────────────────────────────────────────────────────────────

class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_action_supabase_pattern(self):
        from app.actions.audit import log_action
        db = MagicMock()
        table_mock = MagicMock()
        table_mock.insert.return_value = table_mock
        table_mock.execute.return_value = MagicMock(data=[])
        db.table.return_value = table_mock

        result = await log_action(
            db,
            company_id="co-1",
            actor="ai",
            action_type="send_low_stock_alert",
            autonomy_level="notify",
            payload={"item": "Widget A"},
            status="executed",
        )

        assert isinstance(result, str)  # Returns UUID
        db.table.assert_called_with("action_log")
        table_mock.insert.assert_called_once()
        inserted = table_mock.insert.call_args[0][0]
        assert inserted["company_id"] == "co-1"
        assert inserted["actor"] == "ai"
        assert inserted["action_type"] == "send_low_stock_alert"
        assert inserted["status"] == "executed"

    @pytest.mark.asyncio
    async def test_log_action_sqlalchemy_fallback(self):
        from app.actions.audit import log_action
        db = MagicMock(spec=[])  # No 'table' attribute → SQLAlchemy path
        db.execute = MagicMock()

        result = await log_action(
            db,
            company_id="co-1",
            actor="system",
            action_type="delete_qb_data",
            autonomy_level="prohibited",
            payload={},
            status="blocked",
        )

        assert isinstance(result, str)
        db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_action_with_approved_by(self):
        from app.actions.audit import log_action
        db = MagicMock()
        table_mock = MagicMock()
        table_mock.insert.return_value = table_mock
        table_mock.execute.return_value = MagicMock(data=[])
        db.table.return_value = table_mock

        await log_action(
            db,
            company_id="co-1",
            actor="ai",
            action_type="draft_purchase_order",
            autonomy_level="draft_and_wait",
            payload={},
            status="pending_approval",
            approved_by="user-manager",
        )

        inserted = table_mock.insert.call_args[0][0]
        assert inserted["approved_by"] == "user-manager"

    @pytest.mark.asyncio
    async def test_log_action_returns_unique_ids(self):
        from app.actions.audit import log_action
        db = MagicMock()
        table_mock = MagicMock()
        table_mock.insert.return_value = table_mock
        table_mock.execute.return_value = MagicMock(data=[])
        db.table.return_value = table_mock

        ids = set()
        for _ in range(5):
            result = await log_action(
                db, company_id="co-1", actor="ai",
                action_type="run_sync_check", autonomy_level="autonomous",
                payload={}, status="executed",
            )
            ids.add(result)

        assert len(ids) == 5  # All UUIDs unique


# ─── PO Generator Tests ────────────────────────────────────────────────────────

class TestPOLineItem:
    def test_total_cost_calculated_on_init(self):
        from app.actions.po_generator import POLineItem
        li = POLineItem(
            product_name="Widget A",
            qb_item_id="ITEM001",
            quantity=100,
            unit_cost=Decimal("22.50"),
        )
        assert li.total_cost == Decimal("2250.00")

    def test_line_item_with_zero_cost(self):
        from app.actions.po_generator import POLineItem
        li = POLineItem(product_name="Sample", qb_item_id=None, quantity=10, unit_cost=Decimal("0"))
        assert li.total_cost == Decimal("0")


class TestGenerateReorderPO:
    def _make_items(self, count=1):
        return [
            {
                "product_name": f"Widget {i}",
                "qb_item_id": f"ITEM{i:03d}",
                "recommended_order_qty": 80,
                "purchase_cost": 25.0,
                "weekly_sell_rate": 10.0,
            }
            for i in range(count)
        ]

    def test_basic_po_generation(self):
        from app.actions.po_generator import generate_reorder_po
        today = date(2025, 1, 15)
        items = self._make_items(2)
        po = generate_reorder_po(
            company_id="co-1",
            vendor_name="Acme Supplier",
            vendor_id="v-001",
            items_needing_reorder=items,
            today=today,
        )
        assert po.company_id == "co-1"
        assert po.vendor_name == "Acme Supplier"
        assert len(po.line_items) == 2
        assert po.subtotal == Decimal("4000.00")  # 2 × 80 × 25
        assert po.suggested_delivery_date == date(2025, 2, 26)  # +6 weeks
        assert po.created_at == today

    def test_po_has_unique_draft_id(self):
        from app.actions.po_generator import generate_reorder_po
        items = self._make_items(1)
        po1 = generate_reorder_po("co-1", "Supplier", None, items)
        po2 = generate_reorder_po("co-1", "Supplier", None, items)
        assert po1.draft_id != po2.draft_id

    def test_minimum_quantity_is_1(self):
        from app.actions.po_generator import generate_reorder_po
        items = [{"product_name": "Test", "qb_item_id": None, "recommended_order_qty": 0,
                  "purchase_cost": 10.0, "weekly_sell_rate": 1.0}]
        po = generate_reorder_po("co-1", "Supplier", None, items)
        assert po.line_items[0].quantity == 1

    def test_to_dict_serialization(self):
        from app.actions.po_generator import generate_reorder_po
        items = self._make_items(1)
        po = generate_reorder_po("co-1", "Supplier", "v-1", items)
        d = po.to_dict()
        assert d["status"] == "pending_approval"
        assert d["vendor_name"] == "Supplier"
        assert isinstance(d["subtotal"], float)
        assert len(d["line_items"]) == 1
        assert isinstance(d["line_items"][0]["total_cost"], float)

    def test_notes_list_many_products(self):
        from app.actions.po_generator import generate_reorder_po
        items = self._make_items(5)
        po = generate_reorder_po("co-1", "Supplier", None, items)
        assert "(+2 more)" in po.notes

    def test_to_dict_delivery_date_is_string(self):
        from app.actions.po_generator import generate_reorder_po
        items = self._make_items(1)
        po = generate_reorder_po("co-1", "Supplier", None, items, today=date(2025, 1, 15))
        d = po.to_dict()
        assert isinstance(d["suggested_delivery_date"], str)


# ─── Action Engine Tests ───────────────────────────────────────────────────────

class TestActionEngine:
    def _make_engine(self, notifier=None):
        from app.actions.engine import ActionEngine
        db = MagicMock()
        table_mock = MagicMock()
        table_mock.insert.return_value = table_mock
        table_mock.execute.return_value = MagicMock(data=[])
        db.table.return_value = table_mock
        return ActionEngine(db, notifier=notifier)

    @pytest.mark.asyncio
    async def test_prohibited_action_raises(self):
        from app.actions.engine import ActionProhibitedError
        engine = self._make_engine()
        with pytest.raises(ActionProhibitedError, match="permanently blocked"):
            await engine.process("delete_qb_data", {}, "co-1", "user-1")

    @pytest.mark.asyncio
    async def test_computer_use_prohibitions_blocked(self):
        from app.actions.engine import ActionProhibitedError
        engine = self._make_engine()
        for action in ["cu_install_app", "cu_system_settings", "cu_terminal_shell"]:
            with pytest.raises(ActionProhibitedError):
                await engine.process(action, {}, "co-1", "user-1")

    @pytest.mark.asyncio
    async def test_autonomous_action_executes(self):
        engine = self._make_engine()
        result = await engine.process("calculate_health_score", {}, "co-1", "user-1")
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_autonomous_computer_use_executes(self):
        engine = self._make_engine()
        result = await engine.process("cu_read_screen", {}, "co-1", "user-1")
        assert "status" in result

    @pytest.mark.asyncio
    async def test_notify_action_calls_notifier(self):
        notifier = AsyncMock()
        engine = self._make_engine(notifier=notifier)
        with patch("app.actions.email_actions.send_alert_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "sent"}
            await engine.process("send_morning_briefing", {"to": "test@test.com"}, "co-1", "user-1")
        notifier.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_action_no_notifier(self):
        engine = self._make_engine(notifier=None)
        with patch("app.actions.email_actions.send_alert_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "sent"}
            result = await engine.process("send_low_stock_alert", {}, "co-1", "user-1")
        assert result is not None  # Completes without error

    @pytest.mark.asyncio
    async def test_draft_and_wait_returns_pending(self):
        engine = self._make_engine()
        with patch("app.actions.approval_queue.ApprovalQueue") as MockQueue:
            mock_q = AsyncMock()
            mock_q.enqueue.return_value = "draft-uuid-123"
            MockQueue.return_value = mock_q
            result = await engine.process("draft_purchase_order", {"vendor": "X"}, "co-1", "user-1")
        assert result["status"] == "pending_approval"
        assert result["draft_id"] == "draft-uuid-123"

    @pytest.mark.asyncio
    async def test_unknown_action_type_prohibited_by_default(self):
        from app.actions.engine import ActionProhibitedError
        engine = self._make_engine()
        with pytest.raises(ActionProhibitedError):
            await engine.process("totally_unknown_action", {}, "co-1", "user-1")

    def test_autonomy_rules_completeness(self):
        from app.actions.engine import ActionEngine
        rules = ActionEngine.AUTONOMY_RULES
        levels = set(rules.values())
        assert levels == {"autonomous", "notify", "draft_and_wait", "prohibited"}

    def test_all_prohibited_actions_are_destructive(self):
        from app.actions.engine import ActionEngine
        prohibited = [k for k, v in ActionEngine.AUTONOMY_RULES.items() if v == "prohibited"]
        # All prohibited actions should exist and be non-empty
        assert len(prohibited) > 0
        # Key destructive ones must be prohibited
        assert "delete_qb_data" in prohibited
        assert "modify_financial_records" in prohibited
        assert "access_bank_accounts" in prohibited
