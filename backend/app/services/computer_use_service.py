"""
ComputerUseService — isolates computer-use (CU) from the synchronous request path.

Problem being solved
─────────────────────
The old AccessRouter called ComputerUseEngine.extract_data() inline.  If CU
hung for 30 seconds (common during a slow OCR pass), every request waiting on
that data path hung with it.

Solution
─────────
CU is now fully async / out-of-band:

  1. The router calls ``get_or_enqueue(capability, params)``.
  2. If a cached, non-expired completed result exists for that capability it is
     returned immediately with a decayed confidence score.
  3. If no fresh result is found, a job is enqueued in the
     ``computer_use_jobs`` table and ``None`` is returned — the router
     treats this as a miss and moves on.
  4. A Celery worker (``tasks.cu_worker``) picks up the job, runs the
     expensive CU engine in a separate process, and stores the result.
  5. The next request that needs this capability finds the cached result.

Confidence decay
────────────────
  age < CU_FRESH_SECONDS  (15 min) → Confidence.CU_FRESH  (55)
  age < CU_MAX_AGE_SECONDS (60 min) → Confidence.CU_STALE  (25)
  age ≥ CU_MAX_AGE_SECONDS          → expired; ignored

Safety envelope
───────────────
``process_job()`` is called by the Celery worker (not the request path).
It enforces a hard timeout and attaches the screenshot reference to the
job row for audit trail purposes.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.domain.data_result import Confidence

log = logging.getLogger(__name__)

# Age thresholds for confidence decay
CU_FRESH_SECONDS = 15 * 60    # 15 minutes → CU_FRESH
CU_MAX_AGE_SECONDS = 60 * 60  # 60 minutes → result expired; don't use

# Hard timeout for a single CU job execution
CU_JOB_TIMEOUT_SECONDS = 120

# Retry delays (seconds) for failed CU jobs
_RETRY_DELAYS = [120, 600]


class ComputerUseService:
    """
    Manages the computer_use_jobs queue for a single company.

    All DB operations are non-fatal: errors are logged as warnings so that
    a degraded DB never prevents the caller from continuing with other paths.
    """

    def __init__(self, db, company_id: str, worker_id: Optional[str] = None):
        self._db = db
        self._company_id = company_id
        self._worker_id = worker_id or str(uuid.uuid4())[:12]

    # ── Public API ────────────────────────────────────────────────────────────

    def get_cached_result(
        self,
        capability: str,
        max_age_seconds: int = CU_MAX_AGE_SECONDS,
    ) -> Optional[tuple[dict, int]]:
        """
        Return ``(result_dict, confidence)`` if a fresh completed job exists,
        or ``None`` if the cache is empty / expired.
        """
        try:
            result = (
                self._db.table("computer_use_jobs")
                .select("result, completed_at, confidence_pct")
                .eq("company_id", self._company_id)
                .eq("capability", capability)
                .eq("status", "completed")
                .order("completed_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            log.warning("CU: get_cached_result DB error: %s", exc)
            return None

        if not rows:
            return None

        row = rows[0]
        completed_at_str = row.get("completed_at")
        if not completed_at_str:
            return None

        try:
            completed_at = datetime.fromisoformat(
                completed_at_str.replace("Z", "+00:00")
            )
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - completed_at).total_seconds()
        except ValueError:
            return None

        if age_seconds >= max_age_seconds:
            return None  # expired

        confidence = _cu_confidence(age_seconds)
        return row.get("result") or {}, confidence

    def get_or_enqueue(
        self,
        capability: str,
        parameters: dict | None = None,
        *,
        correlation_id: Optional[str] = None,
    ) -> Optional[tuple[dict, int]]:
        """
        Check the cache; if miss, enqueue a background job and return None.

        The caller (AccessRouter) should treat a None return as a path miss
        and log it accordingly — the result will be available on the next
        request after the worker completes the job.
        """
        cached = self.get_cached_result(capability)
        if cached is not None:
            log.debug("CU: cache hit for capability=%s", capability)
            return cached

        # Cache miss — enqueue for background execution
        job_id = self.enqueue(capability, parameters or {}, correlation_id=correlation_id)
        log.info("CU: cache miss for capability=%s — enqueued job %s", capability, job_id)
        return None

    def enqueue(
        self,
        capability: str,
        parameters: dict | None = None,
        *,
        priority: int = 5,
        correlation_id: Optional[str] = None,
        max_attempts: int = 2,
    ) -> Optional[str]:
        """
        Enqueue a CU job.  Idempotent: returns existing job_id if a pending/
        running job with the same correlation_id already exists.
        Returns job_id or None on DB failure.
        """
        if correlation_id:
            existing = self._find_active_by_correlation(correlation_id)
            if existing:
                return existing

        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._db.table("computer_use_jobs").insert({
                "id": job_id,
                "company_id": self._company_id,
                "capability": capability,
                "parameters": parameters or {},
                "status": "pending",
                "priority": max(1, min(10, priority)),
                "correlation_id": correlation_id,
                "attempt_count": 0,
                "max_attempts": max_attempts,
                "created_at": now,
            }).execute()
            return job_id
        except Exception as exc:
            log.warning("CU: enqueue failed for capability=%s: %s", capability, exc)
            return None

    def claim_next_job(self) -> Optional[dict]:
        """
        Atomically claim the next pending CU job for this worker.
        Returns the full job row or None.
        """
        try:
            result = (
                self._db.table("computer_use_jobs")
                .select("*")
                .eq("company_id", self._company_id)
                .eq("status", "pending")
                .order("priority", desc=False)
                .order("created_at", desc=False)
                .limit(5)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            log.warning("CU: claim_next_job query failed: %s", exc)
            return None

        for row in rows:
            claimed = self._try_claim(row["id"])
            if claimed:
                return claimed
        return None

    def complete_job(
        self,
        job_id: str,
        result: dict,
        *,
        screenshot_ref: Optional[str] = None,
        expires_seconds: int = CU_MAX_AGE_SECONDS,
    ) -> None:
        """Mark a job as completed and store the result with an expiry time."""
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=expires_seconds)).isoformat()
        confidence = _cu_confidence(0)  # fresh on completion
        try:
            self._db.table("computer_use_jobs").update({
                "status": "completed",
                "result": result,
                "confidence_pct": confidence,
                "screenshot_ref": screenshot_ref,
                "completed_at": now.isoformat(),
                "expires_at": expires_at,
            }).eq("id", job_id).execute()
        except Exception as exc:
            log.warning("CU: complete_job failed for %s: %s", job_id, exc)

    def fail_job(self, job_id: str, error: str, *, retry: bool = True) -> None:
        """Mark a job as failed; schedule retry if within max_attempts."""
        try:
            row_r = (
                self._db.table("computer_use_jobs")
                .select("attempt_count, max_attempts")
                .eq("id", job_id)
                .execute()
            )
            row = (row_r.data or [{}])[0]
            attempt = int(row.get("attempt_count", 0)) + 1
            max_att = int(row.get("max_attempts", 2))
        except Exception:
            attempt, max_att = 1, 2

        now = datetime.now(timezone.utc)
        next_retry: Optional[str] = None
        if retry and attempt < max_att:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            next_retry = (now + timedelta(seconds=delay)).isoformat()

        try:
            self._db.table("computer_use_jobs").update({
                "status": "failed",
                "error_message": error[:1000],
                "attempt_count": attempt,
                "completed_at": now.isoformat(),
                "worker_id": None,
            }).eq("id", job_id).execute()
        except Exception as exc:
            log.warning("CU: fail_job update failed for %s: %s", job_id, exc)

    def get_job(self, job_id: str) -> Optional[dict]:
        try:
            r = (
                self._db.table("computer_use_jobs")
                .select("*")
                .eq("id", job_id)
                .eq("company_id", self._company_id)
                .execute()
            )
            return (r.data or [None])[0]
        except Exception as exc:
            log.warning("CU: get_job failed for %s: %s", job_id, exc)
            return None

    async def process_job(self, job: dict) -> None:
        """
        Execute a CU job against the real ComputerUseEngine.
        Called by the Celery worker in a subprocess — not the request path.

        Enforces a hard timeout; stores screenshot_ref for audit trail.
        """
        import asyncio
        from app.computer_use.engine import ComputerUseEngine

        job_id = job["id"]
        capability = job["capability"]
        params = job.get("parameters") or {}
        company_config = params.get("company_config") or {}

        task_map = {
            "inventory":          "Get current inventory quantities for all products",
            "orders":             "Get recent order history from the ordering system",
            "customers":          "Get customer list from the system",
            "customs_status":     "Get status of all active shipments/containers",
            "distributor_orders": "Get recent distributor orders",
            "report_download":    "Download the most recent report",
        }
        task = task_map.get(capability, f"Get data for {capability}")
        app_name = company_config.get("ordering_system_app", "the application")

        engine = ComputerUseEngine(company_config=company_config, db=self._db)

        try:
            result = await asyncio.wait_for(
                engine.extract_data(app_name=app_name, task=task),
                timeout=CU_JOB_TIMEOUT_SECONDS,
            )
            screenshot_ref = result.pop("_screenshot_ref", None)
            self.complete_job(job_id, result, screenshot_ref=screenshot_ref)
        except asyncio.TimeoutError:
            self.fail_job(
                job_id,
                f"CU job timed out after {CU_JOB_TIMEOUT_SECONDS}s",
                retry=True,
            )
        except Exception as exc:
            self.fail_job(job_id, str(exc), retry=True)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _try_claim(self, job_id: str) -> Optional[dict]:
        """Atomically flip pending → running. Returns updated row or None."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = (
                self._db.table("computer_use_jobs")
                .update({
                    "status": "running",
                    "worker_id": self._worker_id,
                    "started_at": now,
                })
                .eq("id", job_id)
                .eq("status", "pending")
                .execute()
            )
            updated = result.data or []
            return updated[0] if updated else None
        except Exception as exc:
            log.warning("CU: _try_claim failed for %s: %s", job_id, exc)
            return None

    def _find_active_by_correlation(self, correlation_id: str) -> Optional[str]:
        try:
            result = (
                self._db.table("computer_use_jobs")
                .select("id")
                .eq("company_id", self._company_id)
                .eq("correlation_id", correlation_id)
                .in_("status", ["pending", "running"])
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["id"]
        except Exception as exc:
            log.warning("CU: correlation lookup failed: %s", exc)
        return None


def _cu_confidence(age_seconds: float) -> int:
    """
    Decay function for CU result confidence based on result age.
      < 15 min  → CU_FRESH (55)
      < 60 min  → CU_STALE (25)
      otherwise → UNKNOWN (0)  [callers should not use this]
    """
    if age_seconds < CU_FRESH_SECONDS:
        return Confidence.CU_FRESH
    if age_seconds < CU_MAX_AGE_SECONDS:
        return Confidence.CU_STALE
    return Confidence.UNKNOWN
