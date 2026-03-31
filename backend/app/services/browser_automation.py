"""
BrowserAutomationService — DB-backed job queue for browser automation tasks.

Replaces the inline Playwright calls scattered across AccessRouter with a
durable, retriable queue backed by the `browser_automation_jobs` table.

Job lifecycle
─────────────
  pending → running → completed
              │
              └─ failed  ──[attempt < max]──► pending (next_retry_at set)
                          ──[attempt >= max]──► failed (terminal)

Concurrency is controlled by the DB: a job is claimed by a single worker
using a conditional update that flips status pending→running only if the
row is still pending (or retry-eligible). No distributed lock needed.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Retry back-off delays in seconds: attempt 0→1, 1→2, 2→3
_RETRY_DELAYS = [30, 120, 300]

# How long a running job is allowed before it's considered stuck and re-queued
_JOB_TIMEOUT_SECONDS = 300


class BrowserAutomationService:
    """
    Manages browser automation jobs for a single company.

    All DB operations are non-fatal where appropriate (they log warnings
    instead of raising) so that a degraded DB does not break the caller.
    """

    def __init__(self, db, company_id: str, worker_id: Optional[str] = None):
        self._db = db
        self._company_id = company_id
        self._worker_id = worker_id or str(uuid.uuid4())[:12]

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(
        self,
        capability: str,
        parameters: dict | None = None,
        *,
        priority: int = 5,
        correlation_id: Optional[str] = None,
        max_attempts: int = 3,
    ) -> Optional[str]:
        """
        Add a new job to the queue. Returns the job_id, or None on DB failure.
        If a pending/running job with the same correlation_id already exists,
        returns the existing job_id (idempotent enqueue).
        """
        if correlation_id:
            existing = self._find_active_by_correlation(correlation_id)
            if existing:
                log.debug("BA: reusing existing job %s for correlation_id=%s",
                          existing, correlation_id)
                return existing

        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._db.table("browser_automation_jobs").insert({
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
            log.info("BA: enqueued job %s capability=%s", job_id, capability)
            return job_id
        except Exception as exc:
            log.warning("BA: enqueue failed for capability=%s: %s", capability, exc)
            return None

    def claim_next_job(self) -> Optional[dict]:
        """
        Claim the highest-priority pending job for this worker.

        Returns the full job row, or None if the queue is empty.
        Also re-queues timed-out running jobs before looking for pending ones.
        """
        self._requeue_timed_out_jobs()

        now = datetime.now(timezone.utc)
        retry_cutoff = now.isoformat()

        try:
            result = (
                self._db.table("browser_automation_jobs")
                .select("*")
                .eq("company_id", self._company_id)
                .in_("status", ["pending"])
                .order("priority", desc=False)
                .order("created_at", desc=False)
                .limit(5)  # over-fetch; claim first eligible
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            log.warning("BA: claim query failed: %s", exc)
            return None

        # Also include retry-eligible failed jobs
        try:
            retry_result = (
                self._db.table("browser_automation_jobs")
                .select("*")
                .eq("company_id", self._company_id)
                .eq("status", "failed")
                .lte("next_retry_at", retry_cutoff)
                .order("priority", desc=False)
                .limit(5)
                .execute()
            )
            rows += retry_result.data or []
        except Exception as exc:
            log.warning("BA: retry query failed: %s", exc)

        for row in rows:
            claimed = self._try_claim(row["id"])
            if claimed:
                return claimed

        return None

    def complete_job(self, job_id: str, result: dict, confidence_pct: int) -> None:
        """Mark a job as successfully completed."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._db.table("browser_automation_jobs").update({
                "status": "completed",
                "result": result,
                "confidence_pct": max(0, min(100, confidence_pct)),
                "completed_at": now,
                "error_message": None,
            }).eq("id", job_id).execute()
        except Exception as exc:
            log.warning("BA: complete_job failed for %s: %s", job_id, exc)

    def fail_job(
        self,
        job_id: str,
        error: str,
        *,
        retry: bool = True,
    ) -> None:
        """
        Mark a job as failed.

        If retry=True and attempt_count < max_attempts, schedules a retry
        by setting next_retry_at and leaving status='failed' (retry-eligible).
        If not retryable, status stays 'failed' permanently.
        """
        try:
            row_result = (
                self._db.table("browser_automation_jobs")
                .select("attempt_count, max_attempts")
                .eq("id", job_id)
                .execute()
            )
            row = (row_result.data or [{}])[0]
            attempt = int(row.get("attempt_count", 0)) + 1
            max_att = int(row.get("max_attempts", 3))
        except Exception as exc:
            log.warning("BA: fail_job row fetch failed for %s: %s", job_id, exc)
            attempt, max_att = 1, 3

        now = datetime.now(timezone.utc)
        next_retry: Optional[str] = None
        if retry and attempt < max_att:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            next_retry = (now + timedelta(seconds=delay)).isoformat()

        try:
            self._db.table("browser_automation_jobs").update({
                "status": "failed",
                "error_message": error[:1000],
                "attempt_count": attempt,
                "completed_at": now.isoformat(),
                "next_retry_at": next_retry,
                "worker_id": None,
            }).eq("id", job_id).execute()
        except Exception as exc:
            log.warning("BA: fail_job update failed for %s: %s", job_id, exc)

    def cancel_job(self, job_id: str) -> None:
        """Cancel a pending or running job."""
        try:
            self._db.table("browser_automation_jobs").update({
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job_id).in_("status", ["pending", "running"]).execute()
        except Exception as exc:
            log.warning("BA: cancel_job failed for %s: %s", job_id, exc)

    def get_job(self, job_id: str) -> Optional[dict]:
        """Fetch a single job row by id."""
        try:
            result = (
                self._db.table("browser_automation_jobs")
                .select("*")
                .eq("id", job_id)
                .eq("company_id", self._company_id)
                .execute()
            )
            return (result.data or [None])[0]
        except Exception as exc:
            log.warning("BA: get_job failed for %s: %s", job_id, exc)
            return None

    def cancel_stale_running_jobs(
        self,
        timeout_seconds: int = _JOB_TIMEOUT_SECONDS,
    ) -> int:
        """
        Cancel jobs that have been 'running' longer than timeout_seconds.
        Returns the count of jobs cancelled.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        ).isoformat()
        try:
            result = (
                self._db.table("browser_automation_jobs")
                .update({
                    "status": "failed",
                    "error_message": f"Job timed out after {timeout_seconds}s",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                .eq("company_id", self._company_id)
                .eq("status", "running")
                .lt("started_at", cutoff)
                .execute()
            )
            cancelled = len(result.data or [])
            if cancelled:
                log.warning("BA: cancelled %d stale running jobs", cancelled)
            return cancelled
        except Exception as exc:
            log.warning("BA: cancel_stale_running_jobs failed: %s", exc)
            return 0

    # ── Private helpers ───────────────────────────────────────────────────────

    def _try_claim(self, job_id: str) -> Optional[dict]:
        """
        Atomically flip a job from pending/failed → running.
        Returns the updated job row, or None if someone else claimed it first.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = (
                self._db.table("browser_automation_jobs")
                .update({
                    "status": "running",
                    "worker_id": self._worker_id,
                    "started_at": now,
                })
                .eq("id", job_id)
                .in_("status", ["pending", "failed"])
                .execute()
            )
            updated = result.data or []
            if updated:
                return updated[0]
        except Exception as exc:
            log.warning("BA: _try_claim failed for %s: %s", job_id, exc)
        return None

    def _find_active_by_correlation(self, correlation_id: str) -> Optional[str]:
        """Return job_id of an active (pending/running) job with this correlation_id."""
        try:
            result = (
                self._db.table("browser_automation_jobs")
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
            log.warning("BA: correlation lookup failed: %s", exc)
        return None

    def _requeue_timed_out_jobs(self) -> None:
        """Re-queue running jobs that have exceeded the timeout."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=_JOB_TIMEOUT_SECONDS)
        ).isoformat()
        try:
            self._db.table("browser_automation_jobs").update({
                "status": "pending",
                "worker_id": None,
                "started_at": None,
            }).eq("company_id", self._company_id).eq(
                "status", "running"
            ).lt("started_at", cutoff).execute()
        except Exception as exc:
            log.warning("BA: _requeue_timed_out_jobs failed: %s", exc)
