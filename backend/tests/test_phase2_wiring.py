"""
Phase 2 wiring tests.

Tests for:
  - AccessRouter returns DataResult with correct confidence / source
  - CommitRequiresApprovalError raised on commit escalation
  - AllPathsFailedError → DataResult.from_error (new behaviour)
  - WorkflowEngine policy hooks (evaluate_step_policy, pause_for_policy)
  - WorkflowEngine action_class annotations present in definitions
  - ComputerUseEngine.perform_action calls PolicyEngine and raises PolicyBlockedError
  - _seed_company_defaults writes company_features + seeds policy rules
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch


# ─── AccessRouter ─────────────────────────────────────────────────────────────

class TestAccessRouterDataResult:
    def _make_router(self, api_data: dict | None = None, api_error: str | None = None):
        """Return an AccessRouter whose _try_api is mocked."""
        from app.router.access_router import AccessRouter

        company_config = {"id": "c1", "qb_type": "online"}
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        router = AccessRouter(company_config=company_config, db=db)

        if api_error:
            router._try_api = AsyncMock(side_effect=RuntimeError(api_error))
            router._try_file_ingestion = AsyncMock(side_effect=RuntimeError("no file"))
            router._try_playwright = AsyncMock(side_effect=RuntimeError("no playwright"))
            router._try_computer_use = AsyncMock(side_effect=RuntimeError("no cu"))
        else:
            router._try_api = AsyncMock(return_value=api_data or {"items": []})

        return router

    @pytest.mark.asyncio
    async def test_successful_route_returns_data_result(self):
        from app.domain.data_result import DataResult
        from app.domain.contracts import DataPath

        router = self._make_router(api_data={"items": [{"id": "1"}]})
        result = await router.route("inventory", {})

        assert isinstance(result, DataResult)
        assert result.is_ok is True
        assert result.source == DataPath.API
        assert result.confidence == 100
        assert result.capability == "inventory"

    @pytest.mark.asyncio
    async def test_fallback_lowers_confidence(self):
        from app.domain.data_result import DataResult
        from app.domain.contracts import DataPath
        from app.domain.data_result import Confidence

        router = self._make_router()
        # Make API fail, file_ingestion succeed
        router._try_api = AsyncMock(side_effect=RuntimeError("api down"))
        # Phase 4: _try_file_ingestion returns (data, confidence) tuple
        router._try_file_ingestion = AsyncMock(
            return_value=({"rows": [], "source": "file_ingestion"}, Confidence.FILE_FRESH)
        )

        result = await router.route("inventory", {})

        assert result.is_ok is True
        assert result.source == DataPath.FILE
        assert result.confidence == Confidence.FILE_FRESH
        assert result.metadata["fallback_used"] is True

    @pytest.mark.asyncio
    async def test_all_paths_fail_returns_error_result(self):
        from app.domain.data_result import DataResult
        from app.domain.contracts import DataPath

        router = self._make_router(api_error="down")
        result = await router.route("inventory", {})

        assert isinstance(result, DataResult)
        assert result.is_ok is False
        assert result.source == DataPath.UNKNOWN
        assert result.confidence == 0
        assert "inventory" in result.error

    @pytest.mark.asyncio
    async def test_commit_escalation_raises(self):
        from app.router.access_router import CommitRequiresApprovalError

        router = self._make_router(api_error="api down")

        with pytest.raises(CommitRequiresApprovalError) as exc_info:
            await router.route("inventory", {}, operation_type="commit")

        assert exc_info.value.capability == "inventory"

    @pytest.mark.asyncio
    async def test_correlation_id_propagated(self):
        from app.domain.data_result import DataResult

        router = self._make_router(api_data={"customers": []})
        result = await router.route("customers", {}, correlation_id="corr-xyz")

        assert result.correlation_id == "corr-xyz"

    @pytest.mark.asyncio
    async def test_computer_use_confidence(self):
        from app.domain.data_result import Confidence
        from app.domain.contracts import DataPath

        router = self._make_router(api_error="down")
        router._try_file_ingestion = AsyncMock(side_effect=RuntimeError("no file"))
        # Phase 7: CU path now goes through _try_computer_use_isolated which
        # returns (data, confidence) — mock accordingly.
        router._try_computer_use_isolated = AsyncMock(
            return_value=({"records": []}, Confidence.CU_FRESH)
        )

        result = await router.route("inventory", {})

        assert result.is_ok is True
        assert result.source == DataPath.COMPUTER_USE
        assert result.confidence == Confidence.CU_FRESH


# ─── WorkflowEngine policy hooks ──────────────────────────────────────────────

class TestWorkflowEnginePolicyHooks:
    def _make_engine(self, run: dict | None = None):
        from app.workflows.engine import WorkflowEngine

        db = MagicMock()
        # _get_run is now tenant-scoped: .select().eq(id).eq(company_id).maybe_single()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value \
            .execute.return_value.data = run
        db.table.return_value.insert.return_value.execute.return_value = None
        # updates come in both shapes: .update().eq(id).execute() and the
        # tenant-scoped .update().eq(id).eq(company_id).execute()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        return WorkflowEngine(db=db, company_id="c1")

    def test_get_current_step_def_read_step(self):
        run = {
            "workflow_name": "overdue_outreach",
            "current_step": 0,
            "total_steps": 4,
            "step_results": [],
        }
        engine = self._make_engine(run)
        step = engine.get_current_step_def("run-1")
        assert step is not None
        assert step["action_class"] == "draft"

    def test_get_current_step_def_commit_step(self):
        run = {
            "workflow_name": "overdue_outreach",
            "current_step": 2,   # "Send Reminder Email" — commit
            "total_steps": 4,
            "step_results": [],
        }
        engine = self._make_engine(run)
        step = engine.get_current_step_def("run-1")
        assert step is not None
        assert step["action_class"] == "commit"

    @pytest.mark.asyncio
    async def test_evaluate_step_policy_draft_returns_none(self):
        run = {
            "workflow_name": "low_stock_reorder",
            "current_step": 0,   # draft step
            "total_steps": 3,
            "step_results": [],
        }
        engine = self._make_engine(run)
        decision = await engine.evaluate_step_policy("run-1")
        assert decision is None  # draft steps skip policy check

    @pytest.mark.asyncio
    async def test_evaluate_step_policy_commit_calls_policy_engine(self):
        run = {
            "workflow_name": "low_stock_reorder",
            "current_step": 2,   # "Send to Vendor" — commit
            "total_steps": 3,
            "step_results": [],
        }
        engine = self._make_engine(run)

        # Mock PolicyEngine.evaluate to return allow
        mock_decision = MagicMock()
        mock_decision.effect = "allow"
        mock_decision.rule_name = "default"

        with patch("app.domain.policy.PolicyEngine") as MockPE:
            MockPE.return_value.evaluate = AsyncMock(return_value=mock_decision)
            decision = await engine.evaluate_step_policy("run-1")

        assert decision is not None
        assert decision.effect == "allow"

    def test_pause_for_policy_updates_status(self):
        run = {
            "workflow_name": "overdue_outreach",
            "current_step": 2,
            "total_steps": 4,
            "step_results": [],
        }
        engine = self._make_engine(run)

        mock_decision = MagicMock()
        mock_decision.rule_id = "rule-1"
        mock_decision.rule_name = "require_approval_commits"
        mock_decision.approval_roles = ["owner", "manager"]

        engine.pause_for_policy("run-1", mock_decision)

        # Verify update was called with awaiting_approval status
        update_call = engine._db.table.return_value.update.call_args[0][0]
        assert update_call["status"] == "awaiting_approval"
        assert update_call["policy_rule_id"] == "rule-1"

    def test_resume_after_approval_sets_approved_by(self):
        run = {
            "workflow_name": "low_stock_reorder",
            "current_step": 2,
            "total_steps": 3,
            "step_results": [],
        }
        engine = self._make_engine(run)
        engine.resume_after_approval("run-1", approved=True, approved_by="user-99")

        update_call = engine._db.table.return_value.update.call_args[0][0]
        assert update_call["approved_by"] == "user-99"
        assert "approved_at" in update_call

    def test_resume_after_approval_denied_cancels(self):
        engine = self._make_engine()
        engine.resume_after_approval("run-1", approved=False)

        update_call = engine._db.table.return_value.update.call_args[0][0]
        assert update_call["status"] == "cancelled"


# ─── WorkflowDefinitions action_class ─────────────────────────────────────────

class TestWorkflowDefinitions:
    def test_all_steps_have_action_class(self):
        from app.workflows.definitions import WORKFLOWS

        for wf_name, wf in WORKFLOWS.items():
            for step in wf["steps"]:
                assert "action_class" in step, (
                    f"Workflow {wf_name!r} step {step['step']} missing action_class"
                )
                assert step["action_class"] in ("read", "draft", "commit", "destructive"), (
                    f"Invalid action_class in {wf_name!r} step {step['step']}"
                )

    def test_await_approval_steps_are_read(self):
        from app.workflows.definitions import WORKFLOWS

        for wf_name, wf in WORKFLOWS.items():
            for step in wf["steps"]:
                if step["action_type"] == "await_approval":
                    assert step["action_class"] == "read", (
                        f"await_approval step in {wf_name!r} should be action_class=read"
                    )

    def test_send_email_steps_are_commit(self):
        from app.workflows.definitions import WORKFLOWS

        commit_actions = {"send_vendor_email", "send_customer_email"}
        for wf_name, wf in WORKFLOWS.items():
            for step in wf["steps"]:
                if step["action_type"] in commit_actions:
                    assert step["action_class"] == "commit", (
                        f"{step['action_type']!r} in {wf_name!r} should be action_class=commit"
                    )


# ─── ComputerUseEngine PolicyEngine wire ──────────────────────────────────────

class TestComputerUseEnginePolicy:
    @pytest.mark.asyncio
    async def test_perform_action_allow_proceeds(self):
        from app.computer_use.engine import ComputerUseEngine

        db = MagicMock()
        engine = ComputerUseEngine(company_config={"id": "c1"}, db=db)

        allow_decision = MagicMock()
        allow_decision.effect = "allow"
        allow_decision.rule_name = "default"

        with patch("app.domain.policy.PolicyEngine") as MockPE:
            MockPE.return_value.evaluate = AsyncMock(return_value=allow_decision)
            engine.extract_data = AsyncMock(return_value={"records": []})

            result = await engine.perform_action("ScribeBase", "Submit order #100")

        assert result == {"records": []}

    @pytest.mark.asyncio
    async def test_perform_action_denied_raises(self):
        from app.computer_use.engine import ComputerUseEngine, PolicyBlockedError

        db = MagicMock()
        engine = ComputerUseEngine(company_config={"id": "c1"}, db=db)

        deny_decision = MagicMock()
        deny_decision.effect = "deny"
        deny_decision.rule_name = "deny_computer_use_commits"

        with patch("app.domain.policy.PolicyEngine") as MockPE:
            MockPE.return_value.evaluate = AsyncMock(return_value=deny_decision)

            with pytest.raises(PolicyBlockedError) as exc_info:
                await engine.perform_action("ScribeBase", "Delete all records")

        assert exc_info.value.effect == "deny"

    @pytest.mark.asyncio
    async def test_perform_action_require_approval_raises(self):
        from app.computer_use.engine import ComputerUseEngine, PolicyBlockedError

        db = MagicMock()
        engine = ComputerUseEngine(company_config={"id": "c1"}, db=db)

        approval_decision = MagicMock()
        approval_decision.effect = "require_approval"
        approval_decision.rule_name = "require_approval_commits"

        with patch("app.domain.policy.PolicyEngine") as MockPE:
            MockPE.return_value.evaluate = AsyncMock(return_value=approval_decision)

            with pytest.raises(PolicyBlockedError) as exc_info:
                await engine.perform_action("ScribeBase", "Submit PO #500")

        assert exc_info.value.effect == "require_approval"

    @pytest.mark.asyncio
    async def test_perform_action_no_db_skips_policy(self):
        """Without a db, policy check is skipped — extract_data proceeds."""
        from app.computer_use.engine import ComputerUseEngine

        engine = ComputerUseEngine(company_config={"id": "c1"}, db=None)
        engine.extract_data = AsyncMock(return_value={"records": [1, 2]})

        result = await engine.perform_action("App", "do something")
        assert result == {"records": [1, 2]}

    @pytest.mark.asyncio
    async def test_perform_action_uses_policy_engine_action_class_commit(self):
        """perform_action must build a COMMIT-class proposal for PolicyEngine."""
        from app.domain.contracts import ActionClass
        from app.computer_use.engine import ComputerUseEngine

        db = MagicMock()
        captured = {}

        allow_decision = MagicMock()
        allow_decision.effect = "allow"
        allow_decision.rule_name = "default"

        async def _fake_evaluate(proposal):
            captured["proposal"] = proposal
            return allow_decision

        with patch("app.domain.policy.PolicyEngine") as MockPE:
            MockPE.return_value.evaluate = _fake_evaluate
            engine = ComputerUseEngine(company_config={"id": "c1"}, db=db)
            engine.extract_data = AsyncMock(return_value={})
            await engine.perform_action("App", "do a thing")

        assert "proposal" in captured
        assert captured["proposal"].action_class == ActionClass.COMMIT


# ─── Registration seed ────────────────────────────────────────────────────────

class TestRegistrationSeed:
    @pytest.mark.asyncio
    async def test_seed_company_defaults_writes_features(self):
        from app.api.auth import _seed_company_defaults

        db = MagicMock()
        # policy_rules load returns empty (so seed_defaults inserts)
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        db.table.return_value.upsert.return_value.execute.return_value = None
        db.table.return_value.insert.return_value.execute.return_value = None

        await _seed_company_defaults("company-1", db)

        # company_features upsert was called
        calls = [str(c) for c in db.table.call_args_list]
        assert any("company_features" in c for c in calls)

    @pytest.mark.asyncio
    async def test_seed_company_defaults_calls_policy_seed(self):
        from app.api.auth import _seed_company_defaults

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.execute.return_value.data = []
        db.table.return_value.upsert.return_value.execute.return_value = None
        db.table.return_value.insert.return_value.execute.return_value = None

        with patch("app.domain.policy.PolicyEngine.seed_defaults", new_callable=AsyncMock) as mock_seed:
            await _seed_company_defaults("company-1", db)
            mock_seed.assert_awaited_once()
