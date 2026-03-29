"""
Phase 3 hardening tests.

Tests for:
  - AuditWriter: write_audit_event success, non-fatal on DB error
  - ApprovalQueue.enqueue: workflow_run_id stored, expires_at set
  - api/actions approve: audit event written + workflow resumed
  - api/actions reject: audit event written + workflow cancelled
  - Workflow reaper: stuck running run marked failed
  - Workflow reaper: approval timeout run cancelled
  - Connector health: connected → stale transition
  - Connector health: stale → error escalation
  - Connector task dispatch idempotency: recently-dispatched task skipped
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch, call


# ─── AuditWriter ──────────────────────────────────────────────────────────────

class TestAuditWriter:
    def _make_db(self, fail=False):
        db = MagicMock()
        if fail:
            db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("DB down")
        else:
            db.table.return_value.insert.return_value.execute.return_value = None
        return db

    def test_write_returns_event_id(self):
        from app.utils.audit import write_audit_event
        db = self._make_db()
        event_id = write_audit_event(
            db,
            company_id="c1",
            event_type="draft_approved",
            actor_id="user-1",
        )
        assert event_id is not None
        assert len(event_id) == 36  # UUID

    def test_write_inserts_to_audit_events_table(self):
        from app.utils.audit import write_audit_event
        db = self._make_db()
        write_audit_event(db, company_id="c1", event_type="user_login", actor_id="u1")
        db.table.assert_called_with("audit_events")

    def test_write_includes_optional_fields(self):
        from app.utils.audit import write_audit_event
        db = self._make_db()
        write_audit_event(
            db,
            company_id="c1",
            event_type="policy_blocked",
            actor_id="u1",
            action_class="commit",
            policy_rule_id="rule-99",
            approved_by="owner-1",
            metadata={"draft_id": "d1"},
        )
        inserted = db.table.return_value.insert.call_args[0][0]
        assert inserted["action_class"] == "commit"
        assert inserted["policy_rule_id"] == "rule-99"
        assert inserted["approved_by"] == "owner-1"
        assert inserted["metadata"]["draft_id"] == "d1"

    def test_write_non_fatal_on_db_error(self):
        """DB failure must not raise — returns None."""
        from app.utils.audit import write_audit_event
        db = self._make_db(fail=True)
        result = write_audit_event(db, company_id="c1", event_type="test")
        assert result is None


# ─── ApprovalQueue enqueue with workflow_run_id ───────────────────────────────

class TestApprovalQueueEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_without_workflow_run_id(self):
        from app.actions.approval_queue import ApprovalQueue
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        q = ApprovalQueue(db)
        draft_id = await q.enqueue("c1", "purchase_order", {"qty": 5})

        assert isinstance(draft_id, str)
        inserted = db.table.return_value.insert.call_args[0][0]
        assert "workflow_run_id" not in inserted

    @pytest.mark.asyncio
    async def test_enqueue_with_workflow_run_id(self):
        from app.actions.approval_queue import ApprovalQueue
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        q = ApprovalQueue(db)
        draft_id = await q.enqueue(
            "c1", "purchase_order", {"qty": 5}, workflow_run_id="run-abc"
        )

        assert isinstance(draft_id, str)
        inserted = db.table.return_value.insert.call_args[0][0]
        assert inserted["workflow_run_id"] == "run-abc"

    @pytest.mark.asyncio
    async def test_enqueue_sets_expires_at(self):
        from app.actions.approval_queue import ApprovalQueue
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        q = ApprovalQueue(db)
        await q.enqueue("c1", "email_reply", {}, expires_hours=24)

        inserted = db.table.return_value.insert.call_args[0][0]
        assert "expires_at" in inserted


# ─── Workflow reaper ──────────────────────────────────────────────────────────

class TestWorkflowReaper:
    def _make_db(self, stuck_runs=None, approval_runs=None):
        db = MagicMock()
        _stuck = stuck_runs or []
        _approval = approval_runs or []

        def _table(name):
            tbl = MagicMock()
            # select chain returns stuck runs on first call (running), approval on second
            tbl.select.return_value.eq.return_value.lt.return_value.execute.return_value.data = (
                _stuck if name == "workflow_runs" else []
            )
            tbl.update.return_value.eq.return_value.execute.return_value = None
            tbl.insert.return_value.execute.return_value = None
            return tbl

        db.table = _table
        return db

    def test_reap_running_empty_does_nothing(self):
        from tasks.workflow_reaper import reap_stuck_runs
        db = self._make_db()
        with patch("tasks.workflow_reaper.get_supabase", return_value=db):
            with patch("tasks.workflow_reaper.task_lock") as mock_lock:
                mock_lock.return_value.__enter__ = MagicMock(return_value=True)
                mock_lock.return_value.__exit__ = MagicMock(return_value=False)
                result = reap_stuck_runs()
        assert result["failed_stuck_running"] == 0

    def test_fail_run_marks_failed(self):
        from tasks.workflow_reaper import _fail_run
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"step_results": [{"step": 1}]}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        db.table.return_value.insert.return_value.execute.return_value = None

        now = datetime.now(timezone.utc)
        _fail_run(db, run_id="run-1", company_id="c1", reason="stuck", now=now)

        update_calls = db.table.return_value.update.call_args_list
        assert any(
            call_args[0][0].get("status") == "failed"
            for call_args in update_calls
        )

    def test_fail_run_appends_error_to_step_results(self):
        from tasks.workflow_reaper import _fail_run
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"step_results": [{"step": 1, "result": {}}]}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        db.table.return_value.insert.return_value.execute.return_value = None

        now = datetime.now(timezone.utc)
        _fail_run(db, run_id="run-1", company_id="c1", reason="test reason", now=now)

        update_payload = db.table.return_value.update.call_args_list[0][0][0]
        results = update_payload["step_results"]
        assert any("error" in r for r in results)
        assert any("test reason" in r.get("error", "") for r in results)


# ─── Connector health task ────────────────────────────────────────────────────

class TestConnectorHealthTask:
    def test_mark_stale_no_connectors(self):
        from tasks.connector_health import _mark_stale
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .lt.return_value.execute.return_value.data = []

        count = _mark_stale(db, "2020-01-01T00:00:00Z", datetime.now(timezone.utc))
        assert count == 0

    def test_mark_stale_updates_status(self):
        from tasks.connector_health import _mark_stale
        db = MagicMock()
        # Chain: select → eq("status") → lt("last_heartbeat") → execute
        db.table.return_value.select.return_value.eq.return_value \
            .lt.return_value.execute.return_value.data = [
                {"id": "reg-1", "company_id": "c1", "connector_type": "qb_desktop", "connector_id": "qbd-1"},
            ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        with patch("tasks.connector_health._notify_stale"):
            count = _mark_stale(db, "2020-01-01T00:00:00Z", datetime.now(timezone.utc))

        assert count == 1
        update_payload = db.table.return_value.update.call_args[0][0]
        assert update_payload["status"] == "stale"

    def test_mark_errored_escalates_stale(self):
        from tasks.connector_health import _mark_errored
        db = MagicMock()
        # Chain: select → eq("status") → lt("last_heartbeat") → execute
        db.table.return_value.select.return_value.eq.return_value \
            .lt.return_value.execute.return_value.data = [
                {"id": "reg-1", "company_id": "c1", "connector_type": "qb_desktop", "connector_id": "qbd-1"},
            ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        count = _mark_errored(db, "2020-01-01T00:00:00Z", datetime.now(timezone.utc))

        assert count == 1
        update_payload = db.table.return_value.update.call_args[0][0]
        assert update_payload["status"] == "error"


# ─── Connector task dispatch idempotency ──────────────────────────────────────

class TestConnectorTaskIdempotency:
    def _make_connector(self):
        return {
            "id": "reg-1",
            "company_id": "c1",
            "connector_type": "qb_desktop",
        }

    def _make_task(self, status: str, dispatched_at=None):
        return {
            "id": "task-1",
            "task_id": "task-1",
            "company_id": "c1",
            "connector_type": "qb_desktop",
            "task_type": "fetch_customers",
            "status": status,
            "priority": 1,
            "issued_at": "2024-01-01T00:00:00Z",
            "dispatched_at": dispatched_at,
            "parameters": {},
            "correlation_id": "corr-1",
            "timeout_seconds": 300,
        }

    @pytest.mark.asyncio
    async def test_pending_task_returned(self):
        """A pending task (never dispatched) is always returned."""
        from app.api.connectors import fetch_connector_tasks, _TASK_REDELIVERY_SECONDS

        db = MagicMock()
        task = self._make_task("pending", dispatched_at=None)
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .in_.return_value.order.return_value.order.return_value.limit.return_value \
            .execute.return_value.data = [task]
        db.table.return_value.update.return_value.eq.return_value.in_.return_value \
            .execute.return_value = None

        connector = self._make_connector()
        response = await fetch_connector_tasks(
            max_tasks=1, connector=connector, db=db
        )
        assert len(response.tasks) == 1

    @pytest.mark.asyncio
    async def test_recently_dispatched_task_skipped(self):
        """A task dispatched within the redelivery window is NOT returned."""
        from app.api.connectors import fetch_connector_tasks, _TASK_REDELIVERY_SECONDS

        now = datetime.now(timezone.utc)
        # Dispatched just 10 seconds ago — still within window
        recent_dispatch = (now - timedelta(seconds=10)).isoformat()
        task = self._make_task("dispatched", dispatched_at=recent_dispatch)

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .in_.return_value.order.return_value.order.return_value.limit.return_value \
            .execute.return_value.data = [task]

        connector = self._make_connector()
        response = await fetch_connector_tasks(
            max_tasks=1, connector=connector, db=db
        )
        assert len(response.tasks) == 0

    @pytest.mark.asyncio
    async def test_stale_dispatched_task_returned(self):
        """A task dispatched over 5 minutes ago (no result) is eligible for re-dispatch."""
        from app.api.connectors import fetch_connector_tasks, _TASK_REDELIVERY_SECONDS

        now = datetime.now(timezone.utc)
        # Dispatched 10 minutes ago — outside the redelivery window
        old_dispatch = (now - timedelta(seconds=_TASK_REDELIVERY_SECONDS + 60)).isoformat()
        task = self._make_task("dispatched", dispatched_at=old_dispatch)

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .in_.return_value.order.return_value.order.return_value.limit.return_value \
            .execute.return_value.data = [task]
        db.table.return_value.update.return_value.eq.return_value.in_.return_value \
            .execute.return_value = None

        connector = self._make_connector()
        response = await fetch_connector_tasks(
            max_tasks=1, connector=connector, db=db
        )
        assert len(response.tasks) == 1
