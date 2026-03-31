"""
Computer-use worker — Celery task that processes queued CU jobs.

Runs every 2 minutes.  For each company, claims one pending CU job and
runs it through ComputerUseService.process_job() in the worker process.

Design notes:
  - max_retries=0: if this task crashes, the next scheduled run will pick
    up any un-claimed jobs automatically (no stale running jobs).
  - One job per company per run: CU is expensive; we don't want multiple
    simultaneous sessions for the same company.
  - task_lock: prevents two concurrent cu_worker invocations from claiming
    the same job on different workers.
"""
from __future__ import annotations

import asyncio
import logging

from celery_app import app
from tasks.base import get_supabase, get_active_companies, task_lock

log = logging.getLogger(__name__)


@app.task(name="tasks.cu_worker.process_pending_jobs", bind=True, max_retries=0)
def process_pending_jobs(self):
    """
    Claim and execute one pending CU job per active company.
    """
    with task_lock("cu_worker", ttl_seconds=120) as acquired:
        if not acquired:
            log.info("CU worker already running — skipping")
            return {"skipped": True}

    db = get_supabase()
    companies = get_active_companies(db)

    results = []
    for company in companies:
        company_id = company["id"]
        # Only process companies with computer_use_enabled
        features = company.get("features") or {}
        if not features.get("computer_use_enabled", False):
            continue

        try:
            outcome = asyncio.run(_process_one_job(db, company_id))
            if outcome:
                results.append({"company_id": company_id, **outcome})
        except Exception as exc:
            log.error("CU worker: company %s failed: %s", company_id, exc)
            results.append({"company_id": company_id, "ok": False, "error": str(exc)})

    return results


async def _process_one_job(db, company_id: str) -> dict | None:
    """
    Claim the next pending CU job for this company and execute it.
    Returns a summary dict, or None if there are no pending jobs.
    """
    from app.services.computer_use_service import ComputerUseService

    worker_id = f"celery-cu-{company_id[:8]}"
    cu = ComputerUseService(db, company_id, worker_id=worker_id)

    job = cu.claim_next_job()
    if job is None:
        return None

    log.info(
        "CU worker: claiming job %s capability=%s company=%s",
        job["id"], job["capability"], company_id,
    )

    try:
        await cu.process_job(job)
        return {"ok": True, "job_id": job["id"], "capability": job["capability"]}
    except Exception as exc:
        log.error("CU worker: job %s failed: %s", job["id"], exc)
        cu.fail_job(job["id"], str(exc))
        return {"ok": False, "job_id": job["id"], "error": str(exc)}
