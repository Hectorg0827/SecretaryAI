"""
Phase 5 connector tests.

Tests for:
  - dispatch-task endpoint: 404 when no connector, task_id returned when connected
  - dispatch-task endpoint: stale connector still accepted
  - dispatch-task endpoint: DB insert failure → 503
  - ConnectorConfig: save_token persists to state file and updates instance attrs
  - ConnectorConfig: is_registered false without token, true after save_token
  - ConnectorConfig: connector_id defaults to hostname when env var not set
  - TaskExecutor: ping returns pong without hitting QB adapter
  - TaskExecutor: unsupported task type raises TaskError (not recoverable)
  - TaskExecutor: recoverable TaskError from QB failure
  - TaskExecutor: fetch_payments fallback when adapter has no get_payments
  - TaskExecutor: fetch_vendors fallback when adapter has no get_vendors
  - connector_status endpoint: returns connected/stale/error counts
  - connector_status endpoint: 503 on DB failure
  - single_connector_status: 404 for unknown id
  - generate_install_secret: non-owner returns 403
  - task redelivery: recently-dispatched task skipped; stale-dispatched re-eligible
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── sys.path: make connector package importable ────────────────────────────────
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_user(role: str = "owner", company_id: str = "company-1") -> dict:
    return {"sub": "user-1", "role": role, "company_id": company_id}


def _make_db() -> MagicMock:
    return MagicMock()


def _make_task_row(task_id: str, status: str, dispatched_at: str | None = None) -> dict:
    """Build a minimal task row that satisfies ConnectorTask validation."""
    return {
        "id": task_id,
        "task_id": task_id,
        "task_type": "fetch_customers",
        "company_id": "company-1",
        "correlation_id": f"corr-{task_id}",
        "status": status,
        "dispatched_at": dispatched_at,
        "priority": 5,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": None,
        "parameters": {},
        "timeout_seconds": 120,
    }


# ─── dispatch-task endpoint ───────────────────────────────────────────────────

class TestDispatchTask:
    def _db_with_connector(self, connector_rows: list) -> MagicMock:
        """
        dispatch_task queries:
          db.table("connector_registrations").select(...).eq(company_id).in_(status).limit(1).execute()
        then:
          db.table("connector_tasks").insert(...).execute()
        """
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value \
            .in_.return_value.limit.return_value.execute.return_value.data = connector_rows
        db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return db

    @pytest.mark.asyncio
    async def test_no_connector_raises_404(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        from fastapi import HTTPException
        db = self._db_with_connector([])
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_task(body=DispatchTaskRequest(task_type="fetch_customers"),
                                user=_make_user(), db=db)
        assert exc_info.value.status_code == 404
        assert "connector" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_connected_connector_returns_task_id(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        connector = {"id": "reg-1", "connector_type": "qb_desktop", "status": "connected"}
        db = self._db_with_connector([connector])
        result = await dispatch_task(
            body=DispatchTaskRequest(task_type="fetch_invoices", parameters={"days": 30}),
            user=_make_user(), db=db,
        )
        assert "task_id" in result
        assert len(result["task_id"]) == 36  # UUID
        assert result["status"] == "pending"
        assert result["connector_status"] == "connected"

    @pytest.mark.asyncio
    async def test_stale_connector_accepted(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        connector = {"id": "reg-2", "connector_type": "qb_desktop", "status": "stale"}
        db = self._db_with_connector([connector])
        result = await dispatch_task(body=DispatchTaskRequest(task_type="ping"),
                                     user=_make_user(), db=db)
        assert result["status"] == "pending"
        assert result["connector_status"] == "stale"

    @pytest.mark.asyncio
    async def test_db_failure_raises_503(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        from fastapi import HTTPException
        connector = {"id": "reg-1", "connector_type": "qb_desktop", "status": "connected"}
        db = self._db_with_connector([connector])
        db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("DB down")
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_task(body=DispatchTaskRequest(task_type="fetch_customers"),
                                user=_make_user(), db=db)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_connector_lookup_db_failure_raises_503(self):
        from app.api.connectors import dispatch_task, DispatchTaskRequest
        from fastapi import HTTPException
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value \
            .in_.return_value.limit.return_value.execute.side_effect = RuntimeError("DB down")
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_task(body=DispatchTaskRequest(task_type="fetch_customers"),
                                user=_make_user(), db=db)
        assert exc_info.value.status_code == 503


# ─── ConnectorConfig ──────────────────────────────────────────────────────────

class TestConnectorConfig:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)

    def teardown_method(self):
        os.chdir(self._orig_cwd)
        self._tmpdir.cleanup()
        for key in ["SECRETARY_CLOUD_URL", "SECRETARY_COMPANY_ID",
                    "SECRETARY_INSTALL_SECRET", "SECRETARY_CONNECTOR_ID"]:
            os.environ.pop(key, None)

    def _make_config(self, **env_overrides):
        os.environ.setdefault("SECRETARY_CLOUD_URL", "https://test.secretaryai.com")
        os.environ.setdefault("SECRETARY_COMPANY_ID", "company-test")
        for k, v in env_overrides.items():
            os.environ[k] = v
        # Reload module so it picks up env changes
        import importlib
        import connector.config as mod
        importlib.reload(mod)
        return mod.ConnectorConfig()

    def test_not_registered_before_save_token(self):
        cfg = self._make_config()
        assert not cfg.is_registered

    def test_save_token_updates_instance_attrs(self):
        cfg = self._make_config()
        cfg.save_token("tok-abc", "reg-xyz")
        assert cfg.connector_token == "tok-abc"
        assert cfg.registration_id == "reg-xyz"
        assert cfg.is_registered is True

    def test_save_token_persists_to_file(self):
        cfg = self._make_config()
        cfg.save_token("tok-file", "reg-file")
        state_file = Path("connector_state.json")
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["connector_token"] == "tok-file"
        assert state["registration_id"] == "reg-file"

    def test_token_loaded_from_existing_state_file(self):
        Path("connector_state.json").write_text(
            json.dumps({"connector_token": "persisted-tok", "registration_id": "persisted-reg"})
        )
        cfg = self._make_config()
        assert cfg.connector_token == "persisted-tok"
        assert cfg.registration_id == "persisted-reg"
        assert cfg.is_registered is True

    def test_connector_id_defaults_to_hostname(self):
        os.environ.pop("SECRETARY_CONNECTOR_ID", None)
        cfg = self._make_config()
        assert cfg.connector_id == socket.gethostname()

    def test_connector_id_from_env(self):
        cfg = self._make_config(SECRETARY_CONNECTOR_ID="my-machine")
        assert cfg.connector_id == "my-machine"

    def test_cloud_url_trailing_slash_stripped(self):
        os.environ["SECRETARY_CLOUD_URL"] = "https://example.com/"
        cfg = self._make_config()
        assert not cfg.cloud_url.endswith("/")

    def test_connector_type_is_qb_desktop(self):
        cfg = self._make_config()
        assert cfg.connector_type == "qb_desktop"


# ─── TaskExecutor ─────────────────────────────────────────────────────────────

class TestTaskExecutor:
    def _make_executor(self):
        import importlib
        import connector.executor as mod
        importlib.reload(mod)
        return mod.TaskExecutor(conductor_api_key="key", conductor_end_user_id="user")

    @pytest.mark.asyncio
    async def test_ping_returns_pong(self):
        executor = self._make_executor()
        result = await executor.execute("ping", {})
        assert result == {"status": "pong", "task_type": "ping"}

    @pytest.mark.asyncio
    async def test_ping_does_not_hit_adapter(self):
        executor = self._make_executor()
        with patch.object(executor, "_get_adapter") as mock_adapter:
            await executor.execute("ping", {})
            mock_adapter.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_task_type_raises_task_error(self):
        import connector.executor as mod
        executor = self._make_executor()
        with pytest.raises(mod.TaskError) as exc_info:
            await executor.execute("delete_everything", {})
        assert not exc_info.value.recoverable

    @pytest.mark.asyncio
    async def test_fetch_customers_returns_customers_key(self):
        executor = self._make_executor()
        mock_adapter = MagicMock()
        mock_adapter.get_customers = AsyncMock(return_value=[
            {"id": "c1", "name": "Acme"},
            {"id": "c2", "name": "Beta"},
        ])
        executor._adapter = mock_adapter
        result = await executor.execute("fetch_customers", {})
        assert "customers" in result
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_fetch_customers_adapter_error_raises_recoverable(self):
        import connector.executor as mod
        executor = self._make_executor()
        mock_adapter = MagicMock()
        mock_adapter.get_customers = AsyncMock(side_effect=RuntimeError("QB not running"))
        executor._adapter = mock_adapter
        with pytest.raises(mod.TaskError) as exc_info:
            await executor.execute("fetch_customers", {})
        assert exc_info.value.recoverable is True

    @pytest.mark.asyncio
    async def test_fetch_payments_fallback_when_no_method(self):
        executor = self._make_executor()
        executor._adapter = MagicMock(spec=[])  # no get_payments attribute
        result = await executor.execute("fetch_payments", {})
        assert result["payments"] == []
        assert result["count"] == 0
        assert "note" in result

    @pytest.mark.asyncio
    async def test_fetch_vendors_fallback_when_no_method(self):
        executor = self._make_executor()
        executor._adapter = MagicMock(spec=[])  # no get_vendors attribute
        result = await executor.execute("fetch_vendors", {})
        assert result["vendors"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_fetch_invoices_passes_date_range(self):
        executor = self._make_executor()
        mock_adapter = MagicMock()
        mock_adapter.get_invoices = AsyncMock(return_value=[])
        executor._adapter = mock_adapter
        result = await executor.execute("fetch_invoices", {"days": 7})
        assert "invoices" in result
        assert "date_from" in result
        assert "date_to" in result
        mock_adapter.get_invoices.assert_called_once()

    def test_task_error_stores_recoverable_flag(self):
        import connector.executor as mod
        err = mod.TaskError("boom", recoverable=False)
        assert err.recoverable is False
        assert str(err) == "boom"

        err2 = mod.TaskError("transient", recoverable=True)
        assert err2.recoverable is True


# ─── Connector status endpoint ────────────────────────────────────────────────

class TestConnectorStatusEndpoint:
    def _make_reg_row(self, status: str) -> dict:
        return {
            "id": f"reg-{status}",
            "company_id": "company-1",
            "connector_type": "qb_desktop",
            "connector_id": "machine-1",
            "version": "1.0.0",
            "status": status,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "last_sync_at": None,
            "last_sync_status": None,
            "last_error": None,
            "capabilities": ["customers", "invoices"],
        }

    @pytest.mark.asyncio
    async def test_returns_connector_list(self):
        from app.api.connectors import get_connector_status
        db = _make_db()
        # _mark_stale_connectors uses update chain
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .lt.return_value.execute.return_value = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            self._make_reg_row("connected"),
            self._make_reg_row("stale"),
        ]
        result = await get_connector_status(user=_make_user(), db=db)
        assert "connectors" in result
        assert len(result["connectors"]) == 2

    @pytest.mark.asyncio
    async def test_db_failure_raises_503(self):
        from app.api.connectors import get_connector_status
        from fastapi import HTTPException
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .lt.return_value.execute.return_value = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.execute.side_effect = \
            RuntimeError("DB down")
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_status(user=_make_user(), db=db)
        assert exc_info.value.status_code == 503


# ─── Single connector detail endpoint ────────────────────────────────────────

class TestSingleConnectorStatus:
    @pytest.mark.asyncio
    async def test_unknown_id_raises_404(self):
        from app.api.connectors import get_single_connector_status
        from fastapi import HTTPException
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .execute.return_value.data = []
        with pytest.raises(HTTPException) as exc_info:
            await get_single_connector_status(
                registration_id="nonexistent", user=_make_user(), db=db
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_connector_and_logs(self):
        from app.api.connectors import get_single_connector_status
        db = _make_db()
        reg_row = {
            "id": "reg-1",
            "company_id": "company-1",
            "connector_type": "qb_desktop",
            "connector_id": "machine-1",
            "version": "1.0.0",
            "status": "connected",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        }
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .execute.return_value.data = [reg_row]
        db.table.return_value.select.return_value.eq.return_value.order.return_value \
            .limit.return_value.execute.return_value.data = []
        result = await get_single_connector_status(
            registration_id="reg-1", user=_make_user(), db=db
        )
        assert result["connector"]["id"] == "reg-1"
        assert result["sync_logs"] == []


# ─── generate_install_secret ─────────────────────────────────────────────────

class TestGenerateInstallSecret:
    @pytest.mark.asyncio
    async def test_non_owner_raises_403(self):
        from app.api.connectors import generate_install_secret
        from fastapi import HTTPException
        db = _make_db()
        with pytest.raises(HTTPException) as exc_info:
            await generate_install_secret(user=_make_user(role="manager"), db=db)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_returns_secret(self):
        from app.api.connectors import generate_install_secret
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        result = await generate_install_secret(user=_make_user(role="owner"), db=db)
        assert "install_secret" in result
        assert len(result["install_secret"]) > 20
        assert "warning" in result


# ─── Task redelivery window ───────────────────────────────────────────────────

class TestTaskRedelivery:
    def _db_for_fetch(self, task_rows: list) -> MagicMock:
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .in_.return_value.order.return_value.order.return_value.limit.return_value \
            .execute.return_value.data = task_rows
        db.table.return_value.update.return_value.eq.return_value.in_.return_value \
            .execute.return_value = MagicMock()
        return db

    @pytest.mark.asyncio
    async def test_recently_dispatched_task_is_skipped(self):
        from app.api.connectors import fetch_connector_tasks
        connector = {"id": "reg-1", "company_id": "company-1", "connector_type": "qb_desktop"}
        recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        task = _make_task_row("task-1", "dispatched", dispatched_at=recent)
        db = self._db_for_fetch([task])
        response = await fetch_connector_tasks(max_tasks=3, connector=connector, db=db)
        assert len(response.tasks) == 0

    @pytest.mark.asyncio
    async def test_stale_dispatched_task_is_reeligible(self):
        from app.api.connectors import fetch_connector_tasks, _TASK_REDELIVERY_SECONDS
        connector = {"id": "reg-1", "company_id": "company-1", "connector_type": "qb_desktop"}
        old = (
            datetime.now(timezone.utc) - timedelta(seconds=_TASK_REDELIVERY_SECONDS + 60)
        ).isoformat()
        task = _make_task_row("task-2", "dispatched", dispatched_at=old)
        db = self._db_for_fetch([task])
        response = await fetch_connector_tasks(max_tasks=3, connector=connector, db=db)
        assert len(response.tasks) == 1
        assert response.tasks[0].task_id == "task-2"
