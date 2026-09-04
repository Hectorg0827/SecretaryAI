"""Overnight scan — runs nightly for all companies."""
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock, locked_task
from app.scheduler.overnight_scan import run_overnight_scan
from app.actions.approval_queue import ApprovalQueue

log = logging.getLogger(__name__)


@app.task(name="tasks.overnight.run_overnight_scan_all_companies", bind=True, max_retries=2)
@locked_task("overnight_scan", ttl_seconds=7200)
def run_overnight_scan_all_companies(self):
    db = get_supabase()
    companies = get_active_companies(db)
    log.info("Overnight scan: %d companies", len(companies))

    from app.scheduler.dashboard_snapshot import store_snapshot

    results = []
    for company in companies:
        try:
            import asyncio
            adapter = build_adapter(company)
            result = asyncio.run(run_overnight_scan(company["id"], adapter))

            # Persist overnight scan results so the dashboard can read them
            store_snapshot(db, company["id"], "overnight_scan", result)

            # Expire stale pending drafts for this company
            queue = ApprovalQueue(db)
            expired = queue.expire_old_drafts(company["id"])
            if expired:
                log.info("Expired %d stale drafts for company %s", expired, company["id"])

            results.append({"company_id": company["id"], "ok": True, **result})
        except Exception as e:
            log.error("Overnight scan failed for %s: %s", company["id"], e)
            results.append({"company_id": company["id"], "ok": False, "error": str(e)})

    return results
