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
    from app.workflows.engine import WorkflowEngine
    from datetime import datetime, timedelta, timezone

    engine = WorkflowEngine(db, company["id"])
    company_id = company["id"]

    # Auto-start workflows for unhandled high-priority feed events
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

    TRIGGER_TO_WORKFLOW = {
        "low_stock":       "low_stock_reorder",
        "dormant_account": "dormant_account_reactivation",
        "overdue_invoice": "overdue_outreach",
    }

    for event in unhandled:
        wf_name = TRIGGER_TO_WORKFLOW.get(event["event_type"])
        if not wf_name:
            continue
        # Check if a workflow for this entity is already running
        existing = (
            db.table("workflow_runs")
            .select("id")
            .eq("company_id", company_id)
            .eq("workflow_name", wf_name)
            .in_("status", ["running", "awaiting_approval"])
            .execute()
        ).data
        if not existing:
            try:
                run_id = engine.start(wf_name, trigger_data=event.get("action_data", {}))
                # Mark first step as in-progress (draft created, awaiting approval)
                engine.advance(run_id, {"auto_started": True}, awaiting_entity_id=event["id"])
                log.info("Auto-started workflow %s (run %s) for event %s", wf_name, run_id, event["id"])
            except Exception as exc:
                log.warning("Failed to start workflow %s: %s", wf_name, exc)

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
