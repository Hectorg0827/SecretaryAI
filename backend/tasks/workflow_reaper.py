"""
Workflow Reaper — finds stuck workflow_runs and fails them.

A run is "stuck" if:
  - status = 'running' for longer than its step's timeout_hours (default 4 h)
  - status = 'awaiting_approval' for longer than 48 h (already handled by
    workflow_runner, kept here as a belt-and-suspenders safety net)

Runs every 30 minutes (beat schedule in celery_app.py).
"""
import logging
from datetime import datetime, timedelta, timezone

from celery_app import app
from tasks.base import get_supabase, task_lock, locked_task

log = logging.getLogger(__name__)

# If a workflow_run stays in 'running' with no step update for this long, it's stuck.
_DEFAULT_STUCK_HOURS = 4
# Hard cap on 'awaiting_approval' before the reaper acts (belt + suspenders).
_APPROVAL_TIMEOUT_HOURS = 72


@app.task(name="tasks.workflow_reaper.reap_stuck_runs", bind=True, max_retries=1)
@locked_task("workflow_reaper", ttl_seconds=1800)
def reap_stuck_runs(self):
    """
    Scan all tenants for stuck workflow runs and fail them with a descriptive error.
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)

    failed_running = _reap_running(db, now)
    failed_approval = _reap_approval_timeout(db, now)

    summary = {
        "failed_stuck_running": failed_running,
        "failed_approval_timeout": failed_approval,
        "checked_at": now.isoformat(),
    }
    log.info("Workflow reaper complete: %s", summary)
    return summary


def _reap_running(db, now: datetime) -> int:
    """Fail 'running' runs that haven't advanced within the step timeout."""
    cutoff = (now - timedelta(hours=_DEFAULT_STUCK_HOURS)).isoformat()
    try:
        result = (
            db.table("workflow_runs")
            .select("id, company_id, workflow_name, current_step, started_at, updated_at")
            .eq("status", "running")
            .lt("updated_at", cutoff)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        log.error("Workflow reaper: DB query failed: %s", exc)
        return 0

    count = 0
    for run in rows:
        try:
            _fail_run(
                db,
                run_id=run["id"],
                company_id=run["company_id"],
                reason=f"Stuck: no progress for >{_DEFAULT_STUCK_HOURS}h "
                       f"(workflow={run['workflow_name']}, step={run['current_step']})",
                now=now,
            )
            count += 1
            log.warning(
                "Reaped stuck workflow run %s (company=%s, workflow=%s, step=%s)",
                run["id"], run["company_id"], run["workflow_name"], run["current_step"],
            )
        except Exception as exc:
            log.error("Failed to reap run %s: %s", run["id"], exc)

    return count


def _reap_approval_timeout(db, now: datetime) -> int:
    """Cancel 'awaiting_approval' runs that have exceeded the hard timeout."""
    cutoff = (now - timedelta(hours=_APPROVAL_TIMEOUT_HOURS)).isoformat()
    try:
        result = (
            db.table("workflow_runs")
            .select("id, company_id, workflow_name")
            .eq("status", "awaiting_approval")
            .lt("updated_at", cutoff)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        log.error("Workflow reaper approval: DB query failed: %s", exc)
        return 0

    if not rows:
        return 0

    count = 0
    for run in rows:
        try:
            db.table("workflow_runs").update({
                "status": "cancelled",
                "completed_at": now.isoformat(),
            }).eq("id", run["id"]).execute()

            # Audit
            _write_reaper_audit(db, run["company_id"], run["id"],
                                "workflow_approval_timeout",
                                f"Approval not received within {_APPROVAL_TIMEOUT_HOURS}h")
            count += 1
        except Exception as exc:
            log.error("Failed to cancel timed-out approval run %s: %s", run["id"], exc)

    return count


def _fail_run(db, run_id: str, company_id: str, reason: str, now: datetime) -> None:
    """Mark a single run as failed and append the error to step_results."""
    run_result = (
        db.table("workflow_runs")
        .select("step_results")
        .eq("id", run_id)
        .execute()
    )
    existing_results = []
    if run_result.data:
        existing_results = run_result.data[0].get("step_results") or []

    existing_results.append({
        "error": reason,
        "reaped_at": now.isoformat(),
    })

    db.table("workflow_runs").update({
        "status": "failed",
        "step_results": existing_results,
        "completed_at": now.isoformat(),
    }).eq("id", run_id).execute()

    _write_reaper_audit(db, company_id, run_id, "workflow_stuck_reaped", reason)


def _write_reaper_audit(db, company_id: str, run_id: str, event_type: str, reason: str) -> None:
    """Non-fatal audit event write."""
    try:
        from app.utils.audit import write_audit_event
        write_audit_event(
            db,
            company_id=company_id,
            event_type=event_type,
            metadata={"workflow_run_id": run_id, "reason": reason},
        )
    except Exception as exc:
        log.debug("Reaper audit write failed (non-fatal): %s", exc)
