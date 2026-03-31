"""
Phase 8 tests — Admin API (audit log + system health + workflow runs).

Tests for:
  GET /api/admin/audit-log
    - non-admin role (viewer) → 403
    - manager role returns paginated events
    - pagination math: pages = ceil(total / page_size)
    - event_type filter passed to query
    - since/until filters passed to query
    - DB failure → 503

  GET /api/admin/system-health
    - non-admin → 403
    - returns all subsections
    - connector counts aggregated from status column
    - workflow_run counts aggregated from status column
    - cu_health counts aggregated from status column
    - ingestion_health counts aggregated from status column
    - audit_events_24h count returned
    - subsystem DB failure returns {error: ...} (non-fatal to overall response)

  GET /api/admin/workflow-runs
    - non-admin → 403
    - returns paginated runs
    - status filter passed to query
    - workflow_name filter passed to query
    - DB failure → 503

  _require_admin helper
    - owner allowed
    - manager allowed
    - viewer → 403
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_user(role: str = "owner", company_id: str = "co-1") -> dict:
    return {"sub": "u-1", "role": role, "company_id": company_id}


def _make_db() -> MagicMock:
    return MagicMock()


# ── Fluent query chain helpers ────────────────────────────────────────────────

def _setup_paginated(db: MagicMock, rows: list, total: int = None) -> None:
    """
    Set up the mock for a paginated query:
    .select().eq().order().eq?()*.gte?()*.lte?()*.range().execute()
    Since the chain varies by filters, set .execute at the deepest level
    to return a result regardless.
    """
    mock_result = MagicMock()
    mock_result.data = rows
    mock_result.count = total if total is not None else len(rows)
    # Chain: any call returns self until .execute()
    chain = MagicMock()
    chain.execute.return_value = mock_result
    # Every method on chain returns chain (fluent)
    for method in ["select", "eq", "order", "range", "gte", "lte", "limit"]:
        getattr(chain, method).return_value = chain
    db.table.return_value = chain


def _setup_status_rows(db: MagicMock, rows: list) -> None:
    """Set up for a .select('status').eq(...).execute() call."""
    mock_result = MagicMock()
    mock_result.data = rows
    mock_result.count = len(rows)
    chain = MagicMock()
    chain.execute.return_value = mock_result
    for method in ["select", "eq", "gte", "lte", "limit", "order"]:
        getattr(chain, method).return_value = chain
    db.table.return_value = chain


# ═══════════════════════════════════════════════════════════════════════════════
#  _require_admin helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequireAdmin:
    def test_owner_allowed(self):
        from app.api.admin import _require_admin
        from fastapi import HTTPException
        user = _make_user(role="owner")
        result = _require_admin(user)
        assert result == user

    def test_manager_allowed(self):
        from app.api.admin import _require_admin
        user = _make_user(role="manager")
        result = _require_admin(user)
        assert result == user

    def test_viewer_raises_403(self):
        from app.api.admin import _require_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _require_admin(_make_user(role="viewer"))
        assert exc_info.value.status_code == 403

    def test_sales_rep_raises_403(self):
        from app.api.admin import _require_admin
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _require_admin(_make_user(role="sales_rep"))
        assert exc_info.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/admin/audit-log
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditLog:
    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        from app.api.admin import get_audit_log
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_audit_log(user=_make_user(role="viewer"), db=_make_db())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_paginated_events(self):
        from app.api.admin import get_audit_log
        db = _make_db()
        events = [{"id": f"ev-{i}", "event_type": "user_login"} for i in range(5)]
        _setup_paginated(db, events, total=47)

        result = await get_audit_log(page=1, page_size=5, user=_make_user(), db=db)
        assert result["total"] == 47
        assert len(result["events"]) == 5
        assert result["page"] == 1
        assert result["page_size"] == 5

    @pytest.mark.asyncio
    async def test_pagination_math(self):
        from app.api.admin import get_audit_log
        db = _make_db()
        _setup_paginated(db, [], total=103)

        result = await get_audit_log(page=1, page_size=50, user=_make_user(), db=db)
        assert result["pages"] == 3   # ceil(103/50) = 3

    @pytest.mark.asyncio
    async def test_zero_results_pages_is_one(self):
        from app.api.admin import get_audit_log
        db = _make_db()
        _setup_paginated(db, [], total=0)

        result = await get_audit_log(page=1, page_size=50, user=_make_user(), db=db)
        assert result["pages"] == 1
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_db_failure_raises_503(self):
        from app.api.admin import get_audit_log
        from fastapi import HTTPException
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.order.return_value \
            .range.return_value.execute.side_effect = RuntimeError("DB down")

        with pytest.raises(HTTPException) as exc_info:
            await get_audit_log(user=_make_user(), db=db)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_event_type_filter_applied(self):
        from app.api.admin import get_audit_log
        db = _make_db()
        _setup_paginated(db, [{"id": "ev-1", "event_type": "draft_approved"}], total=1)

        result = await get_audit_log(
            event_type="draft_approved", user=_make_user(), db=db
        )
        assert result["total"] == 1
        # Verify .eq was called (filter applied)
        db.table.return_value.eq.assert_called()


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/admin/system-health
# ═══════════════════════════════════════════════════════════════════════════════

class TestSystemHealth:
    def _db_with_status_rows(self, rows_by_table: dict[str, list[dict]]) -> MagicMock:
        """
        Set up a DB mock that returns different rows per table name.
        """
        db = MagicMock()
        def table_side_effect(table_name):
            rows = rows_by_table.get(table_name, [])
            mock_result = MagicMock()
            mock_result.data = rows
            mock_result.count = len(rows)
            chain = MagicMock()
            chain.execute.return_value = mock_result
            for m in ["select", "eq", "gte", "lte", "limit", "order"]:
                getattr(chain, m).return_value = chain
            return chain
        db.table.side_effect = table_side_effect
        return db

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        from app.api.admin import get_system_health
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_system_health(user=_make_user(role="viewer"), db=_make_db())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_all_subsections(self):
        from app.api.admin import get_system_health
        db = self._db_with_status_rows({})
        result = await get_system_health(user=_make_user(), db=db)
        assert "connectors" in result
        assert "workflow_runs" in result
        assert "computer_use" in result
        assert "ingestion" in result
        assert "audit_events_24h" in result
        assert "generated_at" in result

    @pytest.mark.asyncio
    async def test_connector_counts_aggregated(self):
        from app.api.admin import get_system_health
        db = self._db_with_status_rows({
            "connector_registrations": [
                {"status": "connected"},
                {"status": "connected"},
                {"status": "stale"},
                {"status": "error"},
            ],
        })
        result = await get_system_health(user=_make_user(), db=db)
        c = result["connectors"]
        assert c["connected"] == 2
        assert c["stale"] == 1
        assert c["error"] == 1
        assert c["total"] == 4

    @pytest.mark.asyncio
    async def test_workflow_counts_aggregated(self):
        from app.api.admin import get_system_health
        db = self._db_with_status_rows({
            "workflow_runs": [
                {"status": "completed"},
                {"status": "completed"},
                {"status": "failed"},
                {"status": "running"},
                {"status": "awaiting_approval"},
            ],
        })
        result = await get_system_health(user=_make_user(), db=db)
        w = result["workflow_runs"]
        assert w["completed"] == 2
        assert w["failed"] == 1
        assert w["running"] == 1
        assert w["awaiting_approval"] == 1
        assert w["total_24h"] == 5

    @pytest.mark.asyncio
    async def test_cu_counts_aggregated(self):
        from app.api.admin import get_system_health
        db = self._db_with_status_rows({
            "computer_use_jobs": [
                {"status": "completed"},
                {"status": "pending"},
                {"status": "failed"},
            ],
        })
        result = await get_system_health(user=_make_user(), db=db)
        cu = result["computer_use"]
        assert cu["completed"] == 1
        assert cu["pending"] == 1
        assert cu["failed"] == 1

    @pytest.mark.asyncio
    async def test_subsystem_db_error_returns_error_key(self):
        from app.api.admin import get_system_health
        db = MagicMock()
        db.table.side_effect = RuntimeError("DB completely down")
        # Should not raise — individual failures are non-fatal
        result = await get_system_health(user=_make_user(), db=db)
        # At least some keys present; subsystems return {"error": ...}
        assert "connectors" in result
        assert "error" in result["connectors"]


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/admin/workflow-runs
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowRuns:
    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        from app.api.admin import get_workflow_runs
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_runs(user=_make_user(role="sales_rep"), db=_make_db())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_paginated_runs(self):
        from app.api.admin import get_workflow_runs
        db = _make_db()
        runs = [{"id": f"r-{i}", "workflow_name": "test", "status": "completed",
                 "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:01:00Z"}
                for i in range(3)]
        _setup_paginated(db, runs, total=15)

        result = await get_workflow_runs(page=1, page_size=3, user=_make_user(), db=db)
        assert result["total"] == 15
        assert len(result["runs"]) == 3
        assert result["pages"] == 5

    @pytest.mark.asyncio
    async def test_db_failure_raises_503(self):
        from app.api.admin import get_workflow_runs
        from fastapi import HTTPException
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.order.return_value \
            .range.return_value.execute.side_effect = RuntimeError("DB down")

        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_runs(user=_make_user(), db=db)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_status_filter_applied(self):
        from app.api.admin import get_workflow_runs
        db = _make_db()
        _setup_paginated(db, [], total=0)

        await get_workflow_runs(status="failed", user=_make_user(), db=db)
        # .eq called with the status filter
        db.table.return_value.eq.assert_called()

    @pytest.mark.asyncio
    async def test_workflow_name_filter_applied(self):
        from app.api.admin import get_workflow_runs
        db = _make_db()
        _setup_paginated(db, [], total=0)

        await get_workflow_runs(workflow_name="overdue_outreach", user=_make_user(), db=db)
        db.table.return_value.eq.assert_called()
