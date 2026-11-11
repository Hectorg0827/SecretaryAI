"""
Workflow runner task — checks for pending workflow triggers and advances stalled runs.
Runs every hour.
"""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock, locked_task

log = logging.getLogger(__name__)


@app.task(name="tasks.workflow_runner.run_all", bind=True, max_retries=2)
@locked_task("workflow_runner", ttl_seconds=3600)
def run_all(self):
    """Trigger new workflows from recent feed events + expire timed-out awaiting-approval runs."""
    db = get_supabase()
    companies = get_active_companies(db)

    for company in companies:
        try:
            _process_company(db, company)
        except Exception as e:
            log.error("Workflow runner failed for %s: %s", company["id"], e)

    return {"companies": len(companies)}


def _process_company(db, company: dict) -> None:
    from app.workflows.executor import run_steps, start_from_event
    from datetime import datetime, timedelta, timezone

    company_id = company["id"]
    adapter = None

    def _adapter():
        nonlocal adapter
        if adapter is None:
            adapter = build_adapter(company)
        return adapter

    # Feed events that should start a workflow
    unhandled = (
        db.table("feed_events")
        .select("*")
        .eq("company_id", company_id)
        .eq("is_dismissed", False)
        .in_("event_type", ["low_stock", "dormant_account", "overdue_invoice"])
        .eq("is_read", False)
        .limit(5)
        .execute()
    ).data or []

    # One workflow per feed event, ever — otherwise the same customer would be
    # drafted the same email every hour.
    previous = (
        db.table("workflow_runs").select("trigger_data").eq("company_id", company_id).execute()
    ).data or []
    started_for = {str((r.get("trigger_data") or {}).get("feed_event_id") or "") for r in previous}

    for event in unhandled:
        if str(event.get("id") or "") in started_for:
            continue
        try:
            run_id = asyncio.run(start_from_event(db, _adapter(), company, event))
            if run_id:
                started_for.add(str(event.get("id")))
                log.info("Started workflow run %s for event %s", run_id, event["id"])
        except Exception as exc:
            log.warning("Failed to start workflow for event %s: %s", event.get("id"), exc)

    # Runs left 'running' (e.g. a worker stopped mid-step): carry them forward.
    stalled = (
        db.table("workflow_runs").select("id").eq("company_id", company_id).eq("status", "running").limit(20).execute()
    ).data or []
    for run in stalled:
        try:
            asyncio.run(run_steps(db, _adapter(), company, run["id"]))
        except Exception as exc:
            log.warning("Failed to continue workflow %s: %s", run.get("id"), exc)

    # Expire timed-out awaiting-approval runs (default 48h)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    (
        db.table("workflow_runs")
        .update({"status": "cancelled", "completed_at": datetime.now(timezone.utc).isoformat()})
        .eq("company_id", company_id)
        .eq("status", "awaiting_approval")
        .lt("started_at", cutoff)
        .execute()
    )
