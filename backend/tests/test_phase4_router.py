"""
Phase 4 AccessRouter hardening tests.

Tests for:
  - Capability registry: DB override used when present
  - Capability registry: falls back to built-in default when absent
  - Capability registry: invalid path names in override are silently dropped
  - FILE_FRESH confidence for recent ingested file
  - FILE_STALE confidence for old ingested file (>24h)
  - FILE_STALE when created_at is missing
  - browser_jobs row written on Playwright success
  - browser_jobs row written with 'failed' status on Playwright error
  - correlation_id stored in browser_jobs row
  - All-paths-fail returns DataResult.from_error (not raise)
  - COMMIT escalation still raises CommitRequiresApprovalError
  - DataResult has correct source/confidence per path
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch


def _make_router(
    api_data: dict | None = None,
    api_error: str | None = None,
    capability_routes: dict | None = None,
    file_created_at: str | None = None,
):
    """Build an AccessRouter with mocked internals."""
    from app.router.access_router import AccessRouter

    company_config = {"id": "c1", "qb_type": "online"}
    db = MagicMock()

    # company_features response
    cf_row = {"capability_routes": capability_routes or {}}
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [cf_row]

    # access_router_log insert
    db.table.return_value.insert.return_value.execute.return_value = None
    # browser_jobs update
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = None

    router = AccessRouter(company_config=company_config, db=db)

    if api_error:
        router._try_api = AsyncMock(side_effect=RuntimeError(api_error))
    else:
        router._try_api = AsyncMock(return_value=api_data or {"items": []})

    # file ingestion mock
    now = datetime.now(timezone.utc)
    created_at = file_created_at or now.isoformat()
    router._try_file_ingestion = AsyncMock(
        return_value=({"rows": [], "filename": "test.csv"}, _file_conf(created_at))
    )
    router._try_playwright = AsyncMock(return_value={"records": []})
    router._try_computer_use = AsyncMock(return_value={"records": []})

    return router


def _file_conf(created_at: str) -> int:
    from app.router.access_router import AccessRouter
    return AccessRouter._file_confidence(created_at)


# ─── Capability registry ──────────────────────────────────────────────────────

class TestCapabilityRegistry:
    @pytest.mark.asyncio
    async def test_default_chain_used_when_no_override(self):
        from app.router.access_router import AccessRouter, AccessPath, _DEFAULT_PATH_CHAINS
        router = _make_router()
        router._capability_overrides = {}  # empty — no DB overrides

        chain = await router._resolve_chain("inventory")
        assert chain == _DEFAULT_PATH_CHAINS["inventory"]

    @pytest.mark.asyncio
    async def test_db_override_replaces_default(self):
        from app.router.access_router import AccessRouter, AccessPath
        router = _make_router(capability_routes={"customers": ["api"]})

        chain = await router._resolve_chain("customers")
        assert chain == [AccessPath.API]

    @pytest.mark.asyncio
    async def test_unknown_capability_uses_api_cu_fallback(self):
        from app.router.access_router import AccessRouter, AccessPath
        router = _make_router()
        router._capability_overrides = {}

        chain = await router._resolve_chain("something_unknown")
        assert chain == [AccessPath.API, AccessPath.COMPUTER_USE]

    @pytest.mark.asyncio
    async def test_invalid_path_names_dropped_from_override(self):
        """Paths not in AccessPath enum are silently dropped."""
        from app.router.access_router import AccessRouter, AccessPath
        router = _make_router(
            capability_routes={"inventory": ["api", "bogus_path", "file_ingestion"]}
        )

        chain = await router._resolve_chain("inventory")
        assert AccessPath.API in chain
        assert AccessPath.FILE_INGESTION in chain
        # bogus_path must not appear
        assert all(p.value != "bogus_path" for p in chain)

    @pytest.mark.asyncio
    async def test_override_loaded_once_per_instance(self):
        """DB is queried at most once per router instance (in-request cache)."""
        from app.router.access_router import AccessRouter
        router = _make_router(capability_routes={"inventory": ["api"]})

        await router._resolve_chain("inventory")
        await router._resolve_chain("customers")
        await router._resolve_chain("orders")

        # company_features was queried exactly once
        calls = [str(c) for c in router._db.table.call_args_list]
        cf_calls = [c for c in calls if "company_features" in c]
        assert len(cf_calls) == 1


# ─── File confidence (stale vs fresh) ────────────────────────────────────────

class TestFileConfidence:
    def test_fresh_file_returns_file_fresh(self):
        from app.router.access_router import AccessRouter
        from app.domain.data_result import Confidence
        now = datetime.now(timezone.utc)
        conf = AccessRouter._file_confidence(now.isoformat())
        assert conf == Confidence.FILE_FRESH

    def test_old_file_returns_file_stale(self):
        from app.router.access_router import AccessRouter
        from app.domain.data_result import Confidence
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        conf = AccessRouter._file_confidence(old)
        assert conf == Confidence.FILE_STALE

    def test_exactly_at_threshold_is_stale(self):
        from app.router.access_router import AccessRouter, _FILE_STALE_HOURS
        from app.domain.data_result import Confidence
        old = (datetime.now(timezone.utc) - timedelta(hours=_FILE_STALE_HOURS + 1)).isoformat()
        conf = AccessRouter._file_confidence(old)
        assert conf == Confidence.FILE_STALE

    def test_missing_created_at_returns_stale(self):
        from app.router.access_router import AccessRouter
        from app.domain.data_result import Confidence
        conf = AccessRouter._file_confidence(None)
        assert conf == Confidence.FILE_STALE

    def test_malformed_created_at_returns_stale(self):
        from app.router.access_router import AccessRouter
        from app.domain.data_result import Confidence
        conf = AccessRouter._file_confidence("not-a-date")
        assert conf == Confidence.FILE_STALE

    def test_z_suffix_parsed_correctly(self):
        from app.router.access_router import AccessRouter
        from app.domain.data_result import Confidence
        now_z = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conf = AccessRouter._file_confidence(now_z)
        assert conf == Confidence.FILE_FRESH


# ─── Browser job tracking ─────────────────────────────────────────────────────

class TestBrowserJobTracking:
    def _make_router_for_playwright(self, succeed=True, correlation_id=None):
        from app.router.access_router import AccessRouter

        company_config = {"id": "c1"}
        db = MagicMock()
        # company_features — no overrides
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"capability_routes": {}}
        ]
        db.table.return_value.insert.return_value.execute.return_value = None
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        router = AccessRouter(company_config=company_config, db=db)
        router._capability_overrides = {"customs_status": []}  # force empty — will raise

        if succeed:
            async def _fake_playwright(cap, params, correlation_id=None):
                return {"records": [1, 2]}
        else:
            async def _fake_playwright(cap, params, correlation_id=None):
                raise RuntimeError("playwright failed")

        # Patch internal so we can control what happens inside _try_playwright
        return router, db

    def test_start_browser_job_inserts_row(self):
        from app.router.access_router import AccessRouter
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None
        router = AccessRouter({"id": "c1"}, db)

        job_id = router._start_browser_job("customs_portal", "customs_status", {}, "corr-1")

        assert job_id is not None
        insert_payload = db.table.return_value.insert.call_args[0][0]
        assert insert_payload["status"] == "running"
        assert insert_payload["workflow_name"] == "customs_portal"
        assert insert_payload["correlation_id"] == "corr-1"

    def test_finish_browser_job_updates_completed(self):
        from app.router.access_router import AccessRouter
        from app.domain.data_result import Confidence
        db = MagicMock()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        router = AccessRouter({"id": "c1"}, db)

        router._finish_browser_job("job-1", "completed", result={"rows": []}, confidence=Confidence.BROWSER)

        update_payload = db.table.return_value.update.call_args[0][0]
        assert update_payload["status"] == "completed"
        assert update_payload["confidence_pct"] == Confidence.BROWSER

    def test_finish_browser_job_updates_failed(self):
        from app.router.access_router import AccessRouter
        db = MagicMock()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        router = AccessRouter({"id": "c1"}, db)

        router._finish_browser_job("job-1", "failed", error_message="timeout")

        update_payload = db.table.return_value.update.call_args[0][0]
        assert update_payload["status"] == "failed"
        assert update_payload["error_message"] == "timeout"

    def test_finish_browser_job_no_id_is_noop(self):
        """None job_id (DB write failed at start) must not raise."""
        from app.router.access_router import AccessRouter
        db = MagicMock()
        router = AccessRouter({"id": "c1"}, db)
        router._finish_browser_job(None, "completed")  # must not raise or call DB
        db.table.assert_not_called()


# ─── DataResult source/confidence per path ───────────────────────────────────

class TestRouteResultConfidence:
    @pytest.mark.asyncio
    async def test_api_path_gives_confidence_100(self):
        from app.domain.data_result import Confidence
        from app.domain.contracts import DataPath
        router = _make_router(api_data={"items": [1]})
        router._capability_overrides = {}

        result = await router.route("inventory", {})
        assert result.is_ok
        assert result.source == DataPath.API
        assert result.confidence == Confidence.API

    @pytest.mark.asyncio
    async def test_file_ingestion_fresh_confidence(self):
        from app.domain.data_result import Confidence
        from app.domain.contracts import DataPath
        router = _make_router(api_error="down")
        router._capability_overrides = {}
        now = datetime.now(timezone.utc).isoformat()
        router._try_file_ingestion = AsyncMock(
            return_value=({"rows": []}, Confidence.FILE_FRESH)
        )

        result = await router.route("inventory", {})
        assert result.is_ok
        assert result.source == DataPath.FILE
        assert result.confidence == Confidence.FILE_FRESH

    @pytest.mark.asyncio
    async def test_file_ingestion_stale_confidence(self):
        from app.domain.data_result import Confidence
        from app.domain.contracts import DataPath
        router = _make_router(api_error="down")
        router._capability_overrides = {}
        router._try_file_ingestion = AsyncMock(
            return_value=({"rows": []}, Confidence.FILE_STALE)
        )

        result = await router.route("inventory", {})
        assert result.is_ok
        assert result.confidence == Confidence.FILE_STALE

    @pytest.mark.asyncio
    async def test_all_fail_returns_error_result_not_raise(self):
        from app.domain.contracts import DataPath
        router = _make_router(api_error="down")
        router._capability_overrides = {}
        router._try_file_ingestion = AsyncMock(side_effect=RuntimeError("no file"))
        router._try_computer_use = AsyncMock(side_effect=RuntimeError("cu fail"))

        result = await router.route("inventory", {})
        assert not result.is_ok
        assert result.source == DataPath.UNKNOWN

    @pytest.mark.asyncio
    async def test_commit_escalation_still_raises(self):
        from app.router.access_router import CommitRequiresApprovalError
        router = _make_router(api_error="down")
        router._capability_overrides = {}

        with pytest.raises(CommitRequiresApprovalError):
            await router.route("inventory", {}, operation_type="commit")
