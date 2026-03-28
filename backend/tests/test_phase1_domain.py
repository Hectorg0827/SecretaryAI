"""
Phase 1 domain layer tests.

Tests for:
  - DataResult envelope
  - PolicyEngine evaluation logic
  - ConnectorProtocol message serialisation
  - Canonical contracts
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock


# ─── DataResult ──────────────────────────────────────────────────────────────

class TestDataResult:
    def test_ok_constructor(self):
        from app.domain.data_result import DataResult, Confidence
        from app.domain.contracts import DataPath

        r = DataResult.ok(
            data=[{"id": "1", "name": "Test"}],
            source=DataPath.API,
            confidence=Confidence.API,
            capability="inventory",
        )
        assert r.is_ok is True
        assert r.source == DataPath.API
        assert r.confidence == 100
        assert r.row_count == 1
        assert r.error is None

    def test_error_constructor(self):
        from app.domain.data_result import DataResult
        from app.domain.contracts import DataPath

        r = DataResult.from_error(
            error="QB connection refused",
            source=DataPath.API,
            capability="customers",
        )
        assert r.is_ok is False
        assert r.row_count == 0
        assert r.confidence == 0
        assert "QB connection" in r.error

    def test_is_stale_fresh(self):
        from app.domain.data_result import DataResult
        from app.domain.contracts import DataPath

        r = DataResult.ok(data=[], source=DataPath.CACHE, confidence=90)
        assert r.is_stale is False

    def test_is_stale_old(self):
        from app.domain.data_result import DataResult, DEFAULT_STALE_SECONDS
        from app.domain.contracts import DataPath

        old_time = datetime.now(timezone.utc) - timedelta(seconds=DEFAULT_STALE_SECONDS + 60)
        r = DataResult(source=DataPath.FILE, data=[], confidence=60, fetched_at=old_time)
        assert r.is_stale is True

    def test_to_dict(self):
        from app.domain.data_result import DataResult
        from app.domain.contracts import DataPath

        r = DataResult.ok(data={"k": "v"}, source=DataPath.BROWSER, confidence=75)
        d = r.to_dict()
        assert d["source"] == "browser"
        assert d["confidence"] == 75
        assert d["is_ok"] is True
        assert d["row_count"] == 1
        assert "fetched_at" in d

    def test_confidence_constants(self):
        from app.domain.data_result import Confidence
        assert Confidence.API > Confidence.BROWSER > Confidence.FILE_FRESH
        assert Confidence.FILE_FRESH > Confidence.COMPUTER_USE
        assert Confidence.COMPUTER_USE > Confidence.UNKNOWN


# ─── Contracts ────────────────────────────────────────────────────────────────

class TestContracts:
    def test_customer_defaults(self):
        from app.domain.contracts import Customer, AccountStatus, DataPath
        c = Customer(id="1", company_id="c1", external_id="QB-1", name="Acme")
        assert c.status == AccountStatus.ACTIVE
        assert c.balance == Decimal(0)
        assert c.source == DataPath.UNKNOWN
        assert c.tags == []

    def test_action_proposal(self):
        from app.domain.contracts import ActionProposal, ActionClass, DataPath
        p = ActionProposal(
            action_type="create_po",
            action_class=ActionClass.COMMIT,
            description="Create PO #100",
            company_id="c1",
            requested_by="user1",
            amount=Decimal("1500.00"),
        )
        assert p.action_class == ActionClass.COMMIT
        assert p.amount == Decimal("1500.00")

    def test_data_path_enum(self):
        from app.domain.contracts import DataPath
        assert DataPath.API.value == "api"
        assert DataPath.COMPUTER_USE.value == "computer_use"


# ─── PolicyEngine ────────────────────────────────────────────────────────────

class TestPolicyEngine:
    def _make_db(self, rules=None, features=None):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = rules or []
        db.table.return_value.select.return_value.eq.return_value \
            .execute.return_value.data = features or []
        return db

    @pytest.mark.asyncio
    async def test_default_allow_read(self):
        from app.domain.policy import PolicyEngine
        from app.domain.contracts import ActionProposal, ActionClass

        db = MagicMock()
        # _load_rules returns empty list
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        # _load_features returns empty
        db.table.return_value.select.return_value.eq.return_value \
            .execute.return_value.data = []

        engine = PolicyEngine(company_id="c1", db=db)
        proposal = ActionProposal(
            action_type="get_customers",
            action_class=ActionClass.READ,
            description="Fetch customer list",
            company_id="c1",
            requested_by="user1",
        )
        decision = await engine.evaluate(proposal)
        assert decision.effect == "allow"
        assert decision.rule_name == "default"

    @pytest.mark.asyncio
    async def test_default_require_approval_commit(self):
        from app.domain.policy import PolicyEngine
        from app.domain.contracts import ActionProposal, ActionClass

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        db.table.return_value.select.return_value.eq.return_value \
            .execute.return_value.data = []

        engine = PolicyEngine(company_id="c1", db=db)
        proposal = ActionProposal(
            action_type="create_po",
            action_class=ActionClass.COMMIT,
            description="Create PO",
            company_id="c1",
            requested_by="user1",
            amount=Decimal("5000.00"),
        )
        decision = await engine.evaluate(proposal)
        assert decision.effect == "require_approval"

    @pytest.mark.asyncio
    async def test_default_deny_destructive(self):
        from app.domain.policy import PolicyEngine
        from app.domain.contracts import ActionProposal, ActionClass

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        db.table.return_value.select.return_value.eq.return_value \
            .execute.return_value.data = []

        engine = PolicyEngine(company_id="c1", db=db)
        proposal = ActionProposal(
            action_type="delete_customer",
            action_class=ActionClass.DESTRUCTIVE,
            description="Delete customer",
            company_id="c1",
            requested_by="user1",
        )
        decision = await engine.evaluate(proposal)
        assert decision.effect == "deny"

    @pytest.mark.asyncio
    async def test_matching_rule_wins(self):
        from app.domain.policy import PolicyEngine
        from app.domain.contracts import ActionProposal, ActionClass

        rules = [{
            "id": "rule-1",
            "action_class": "commit",
            "effect": "allow",
            "min_amount": None,
            "max_amount": "200.00",
            "capabilities_match": None,
            "paths_match": None,
            "rule_name": "small_commit_auto_allow",
            "description": "Auto-allow small commits",
            "approval_roles": ["owner"],
            "approval_count": 1,
        }]

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = rules
        db.table.return_value.select.return_value.eq.return_value \
            .execute.return_value.data = []

        engine = PolicyEngine(company_id="c1", db=db)
        proposal = ActionProposal(
            action_type="create_po",
            action_class=ActionClass.COMMIT,
            description="Small PO",
            company_id="c1",
            requested_by="user1",
            amount=Decimal("100.00"),  # below max_amount=200
        )
        decision = await engine.evaluate(proposal)
        assert decision.effect == "allow"
        assert decision.rule_name == "small_commit_auto_allow"

    @pytest.mark.asyncio
    async def test_amount_threshold_auto_allow(self):
        """Commit below company threshold should auto-allow even without a matching rule."""
        from app.domain.policy import PolicyEngine
        from app.domain.contracts import ActionProposal, ActionClass

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        # company_features has require_approval_above = 500
        db.table.return_value.select.return_value.eq.return_value \
            .execute.return_value.data = [{"require_approval_above": "500.00"}]

        engine = PolicyEngine(company_id="c1", db=db)
        proposal = ActionProposal(
            action_type="create_po",
            action_class=ActionClass.COMMIT,
            description="Small PO under threshold",
            company_id="c1",
            requested_by="user1",
            amount=Decimal("250.00"),  # below 500 threshold
        )
        decision = await engine.evaluate(proposal)
        assert decision.effect == "allow"


# ─── ConnectorProtocol serialisation ─────────────────────────────────────────

class TestConnectorProtocol:
    def test_register_request_roundtrip(self):
        from app.domain.connector_protocol import RegisterRequest, ConnectorType
        req = RegisterRequest(
            company_id="c1",
            connector_type=ConnectorType.QB_DESKTOP,
            connector_id="host-abc123",
            version="1.0.0",
            install_secret="s3cr3t",
            capabilities=["customers", "invoices"],
        )
        data = req.model_dump()
        req2 = RegisterRequest(**data)
        assert req2.connector_type == ConnectorType.QB_DESKTOP
        assert req2.capabilities == ["customers", "invoices"]

    def test_heartbeat_request(self):
        from app.domain.connector_protocol import HeartbeatRequest
        req = HeartbeatRequest(registration_id="reg-1", status="ok")
        assert req.status == "ok"

    def test_task_result_status(self):
        from app.domain.connector_protocol import TaskResult, TaskStatus
        r = TaskResult(
            task_id="t1",
            registration_id="reg-1",
            company_id="c1",
            correlation_id="corr-1",
            status=TaskStatus.COMPLETED,
            rows_returned=42,
        )
        assert r.status == TaskStatus.COMPLETED
        assert r.rows_returned == 42

    def test_capability_lists(self):
        from app.domain.connector_protocol import (
            QB_DESKTOP_CAPABILITIES,
            FILE_WATCHER_CAPABILITIES,
        )
        assert "customers" in QB_DESKTOP_CAPABILITIES
        assert "invoices" in QB_DESKTOP_CAPABILITIES
        assert "file_watch_inventory" in FILE_WATCHER_CAPABILITIES

    def test_stale_threshold_gt_interval(self):
        from app.domain.connector_protocol import (
            HEARTBEAT_INTERVAL_SECONDS,
            STALE_THRESHOLD_SECONDS,
        )
        assert STALE_THRESHOLD_SECONDS > HEARTBEAT_INTERVAL_SECONDS
