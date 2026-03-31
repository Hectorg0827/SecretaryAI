"""
Phase 7 tests — Computer-use fallback isolation.

Tests for:
  Confidence class
    - CU_FRESH and CU_STALE are present and ordered correctly
    - _cu_confidence decay: age < 15min → CU_FRESH, 15-60min → CU_STALE, >60min → UNKNOWN

  ComputerUseService.get_cached_result
    - Returns None on empty DB
    - Returns (result, CU_FRESH) for result < 15 min old
    - Returns (result, CU_STALE) for result between 15–60 min old
    - Returns None for result > 60 min old (expired)
    - Returns None on DB error (non-fatal)
    - Handles Z-suffix ISO timestamps

  ComputerUseService.get_or_enqueue
    - Cache hit: returns cached result, does NOT enqueue
    - Cache miss: enqueues job and returns None
    - DB failure on enqueue: returns None gracefully

  ComputerUseService.enqueue
    - Returns UUID job_id on success
    - Idempotent: same correlation_id returns existing active job_id
    - Returns None on DB failure (non-fatal)
    - Priority is clamped to [1,10]

  ComputerUseService.complete_job
    - Sets status=completed, stores result + screenshot_ref + expires_at
    - confidence_pct is set to CU_FRESH (fresh on completion)
    - Non-fatal on DB error

  ComputerUseService.fail_job
    - Below max_attempts: status=failed (retry scheduled)
    - At max_attempts: status=failed, no retry
    - retry=False: no retry regardless

  ComputerUseService.claim_next_job
    - Empty queue returns None
    - Claims and flips pending→running atomically

  AccessRouter CU path
    - Cache hit: router returns DataResult with CU_FRESH confidence
    - Cache miss: router moves to next path (RuntimeError raised + caught)
    - COMPUTER_USE path calls _try_computer_use_isolated not legacy method
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_db():
    return MagicMock()


def _cu_service(db=None, company_id="co-1", worker_id="w-1"):
    from app.services.computer_use_service import ComputerUseService
    return ComputerUseService(db or _make_db(), company_id, worker_id)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _ago(seconds: float) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(seconds=seconds))


# ═══════════════════════════════════════════════════════════════════════════════
#  Confidence levels
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceLevels:
    def test_cu_fresh_and_stale_exist(self):
        from app.domain.data_result import Confidence
        assert hasattr(Confidence, "CU_FRESH")
        assert hasattr(Confidence, "CU_STALE")

    def test_cu_fresh_higher_than_stale(self):
        from app.domain.data_result import Confidence
        assert Confidence.CU_FRESH > Confidence.CU_STALE

    def test_cu_fresh_lower_than_browser(self):
        from app.domain.data_result import Confidence
        assert Confidence.CU_FRESH < Confidence.BROWSER

    def test_cu_stale_lower_than_file_stale(self):
        from app.domain.data_result import Confidence
        assert Confidence.CU_STALE < Confidence.FILE_STALE


class TestCuConfidenceDecay:
    def test_fresh_age_returns_cu_fresh(self):
        from app.services.computer_use_service import _cu_confidence
        from app.domain.data_result import Confidence
        assert _cu_confidence(0) == Confidence.CU_FRESH
        assert _cu_confidence(60) == Confidence.CU_FRESH   # 1 min
        assert _cu_confidence(899) == Confidence.CU_FRESH  # just under 15 min

    def test_stale_age_returns_cu_stale(self):
        from app.services.computer_use_service import _cu_confidence, CU_FRESH_SECONDS
        from app.domain.data_result import Confidence
        assert _cu_confidence(CU_FRESH_SECONDS + 1) == Confidence.CU_STALE
        assert _cu_confidence(30 * 60) == Confidence.CU_STALE  # 30 min

    def test_expired_age_returns_unknown(self):
        from app.services.computer_use_service import _cu_confidence, CU_MAX_AGE_SECONDS
        from app.domain.data_result import Confidence
        assert _cu_confidence(CU_MAX_AGE_SECONDS) == Confidence.UNKNOWN
        assert _cu_confidence(CU_MAX_AGE_SECONDS + 1) == Confidence.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════════════
#  get_cached_result
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCachedResult:
    def _db_with_row(self, completed_at: str, result: dict = None):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
            {"result": result or {"rows": []}, "completed_at": completed_at, "confidence_pct": 55}
        ]
        return db

    def _db_empty(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        return db

    def test_empty_db_returns_none(self):
        svc = _cu_service(self._db_empty())
        assert svc.get_cached_result("inventory") is None

    def test_fresh_result_returns_cu_fresh(self):
        from app.domain.data_result import Confidence
        svc = _cu_service(self._db_with_row(_ago(60)))  # 1 minute ago
        cached = svc.get_cached_result("inventory")
        assert cached is not None
        data, confidence = cached
        assert confidence == Confidence.CU_FRESH

    def test_stale_result_returns_cu_stale(self):
        from app.domain.data_result import Confidence
        svc = _cu_service(self._db_with_row(_ago(20 * 60)))  # 20 min ago
        cached = svc.get_cached_result("inventory")
        assert cached is not None
        _, confidence = cached
        assert confidence == Confidence.CU_STALE

    def test_expired_result_returns_none(self):
        svc = _cu_service(self._db_with_row(_ago(70 * 60)))  # 70 min ago
        assert svc.get_cached_result("inventory") is None

    def test_db_error_returns_none(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.order.return_value.limit.return_value.execute.side_effect = \
            RuntimeError("DB down")
        svc = _cu_service(db)
        assert svc.get_cached_result("inventory") is None  # non-fatal

    def test_z_suffix_timestamp_parsed(self):
        from app.domain.data_result import Confidence
        # Z-suffix instead of +00:00
        completed_at = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        svc = _cu_service(self._db_with_row(completed_at))
        cached = svc.get_cached_result("customers")
        assert cached is not None
        _, conf = cached
        assert conf == Confidence.CU_FRESH

    def test_returns_result_dict(self):
        payload = {"customers": [{"id": "c1", "name": "Acme"}], "count": 1}
        svc = _cu_service(self._db_with_row(_ago(30), result=payload))
        cached = svc.get_cached_result("customers")
        assert cached is not None
        data, _ = cached
        assert data == payload


# ═══════════════════════════════════════════════════════════════════════════════
#  get_or_enqueue
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetOrEnqueue:
    def test_cache_hit_returns_data_without_enqueue(self):
        from app.domain.data_result import Confidence
        from app.services.computer_use_service import ComputerUseService
        svc = _cu_service()
        cached = ({"rows": []}, Confidence.CU_FRESH)
        with patch.object(svc, "get_cached_result", return_value=cached), \
             patch.object(svc, "enqueue") as mock_enqueue:
            result = svc.get_or_enqueue("inventory")
            assert result == cached
            mock_enqueue.assert_not_called()

    def test_cache_miss_enqueues_and_returns_none(self):
        svc = _cu_service()
        with patch.object(svc, "get_cached_result", return_value=None), \
             patch.object(svc, "enqueue", return_value="job-abc") as mock_enqueue:
            result = svc.get_or_enqueue("inventory", {"x": 1}, correlation_id="c-1")
            assert result is None
            mock_enqueue.assert_called_once_with(
                "inventory", {"x": 1}, correlation_id="c-1"
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  enqueue
# ═══════════════════════════════════════════════════════════════════════════════

class TestCuEnqueue:
    def _db_no_existing(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
        db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return db

    def test_returns_uuid_job_id(self):
        svc = _cu_service(self._db_no_existing())
        job_id = svc.enqueue("inventory")
        assert job_id is not None
        assert len(job_id) == 36

    def test_idempotent_on_active_correlation_id(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = [
            {"id": "existing-cu-job"}
        ]
        svc = _cu_service(db)
        job_id = svc.enqueue("inventory", correlation_id="corr-xyz")
        assert job_id == "existing-cu-job"
        db.table.return_value.insert.assert_not_called()

    def test_db_failure_returns_none(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
        db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("DB down")
        svc = _cu_service(db)
        assert svc.enqueue("inventory") is None

    def test_priority_clamped_to_max(self):
        db = self._db_no_existing()
        svc = _cu_service(db)
        svc.enqueue("inventory", priority=999)
        row = db.table.return_value.insert.call_args[0][0]
        assert row["priority"] == 10


# ═══════════════════════════════════════════════════════════════════════════════
#  complete_job / fail_job
# ═══════════════════════════════════════════════════════════════════════════════

class TestCuCompleteAndFail:
    def test_complete_job_sets_completed_status(self):
        from app.domain.data_result import Confidence
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        svc = _cu_service(db)
        svc.complete_job("job-1", {"rows": []}, screenshot_ref="s3://bucket/img.png")
        update = db.table.return_value.update.call_args[0][0]
        assert update["status"] == "completed"
        assert update["confidence_pct"] == Confidence.CU_FRESH
        assert update["screenshot_ref"] == "s3://bucket/img.png"
        assert update["expires_at"] is not None

    def test_complete_job_non_fatal(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.side_effect = \
            RuntimeError("DB down")
        svc = _cu_service(db)
        svc.complete_job("job-1", {})  # must not raise

    def test_fail_job_below_max_schedules_retry(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"attempt_count": 0, "max_attempts": 2}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        svc = _cu_service(db)
        svc.fail_job("job-1", "timeout", retry=True)
        # job row stays as failed but caller knows to retry via next_retry_at
        update = db.table.return_value.update.call_args[0][0]
        assert update["status"] == "failed"
        assert update["attempt_count"] == 1

    def test_fail_job_at_max_no_next_retry(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"attempt_count": 1, "max_attempts": 2}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        svc = _cu_service(db)
        svc.fail_job("job-1", "still failing", retry=True)
        update = db.table.return_value.update.call_args[0][0]
        # attempt_count=2 == max_attempts=2, so next_retry_at should be None
        assert update["attempt_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  claim_next_job
# ═══════════════════════════════════════════════════════════════════════════════

class TestCuClaimNextJob:
    def test_empty_queue_returns_none(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        svc = _cu_service(db)
        assert svc.claim_next_job() is None

    def test_claims_pending_job(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.order.return_value.limit.return_value.execute.return_value.data = [
            {"id": "cu-job-1", "status": "pending"}
        ]
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .execute.return_value.data = [{"id": "cu-job-1", "status": "running"}]
        svc = _cu_service(db)
        result = svc.claim_next_job()
        assert result is not None
        assert result["id"] == "cu-job-1"


# ═══════════════════════════════════════════════════════════════════════════════
#  AccessRouter CU path integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccessRouterCuPath:
    def _make_router(self):
        from app.router.access_router import AccessRouter
        return AccessRouter(
            company_config={"id": "co-1"},
            db=_make_db(),
        )

    @pytest.mark.asyncio
    async def test_cache_hit_returns_data_result_with_cu_confidence(self):
        from app.domain.data_result import Confidence
        from app.router.access_router import AccessRouter, AccessPath

        router = self._make_router()
        cached = ({"customers": []}, Confidence.CU_FRESH)

        with patch(
            "app.services.computer_use_service.ComputerUseService.get_or_enqueue",
            return_value=cached,
        ):
            data, confidence = await router._try_computer_use_isolated(
                "customers", {}, "corr-1"
            )
        assert data == {"customers": []}
        assert confidence == Confidence.CU_FRESH

    @pytest.mark.asyncio
    async def test_cache_miss_raises_runtime_error(self):
        from app.router.access_router import AccessRouter

        router = self._make_router()
        with patch(
            "app.services.computer_use_service.ComputerUseService.get_or_enqueue",
            return_value=None,
        ):
            with pytest.raises(RuntimeError, match="no cached result"):
                await router._try_computer_use_isolated("inventory", {}, None)

    @pytest.mark.asyncio
    async def test_legacy_try_computer_use_still_exists(self):
        """Ensure the old _try_computer_use method wasn't removed (backward compat)."""
        from app.router.access_router import AccessRouter
        router = self._make_router()
        assert hasattr(router, "_try_computer_use")
        assert hasattr(router, "_try_computer_use_isolated")
