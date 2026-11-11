"""
On-demand data refresh for ONE company.

Used by:
  * QuickBooks Online / Conductor webhooks ("something changed in the books");
  * the dashboard "Refresh" button.

It re-reads QuickBooks and rewrites the shared dashboard snapshot. It does NOT
send anything to anyone. (The Refresh button used to call the morning-briefing
task, which re-sent the briefing email on every click, and the webhooks called
task names that did not exist at all.)
"""
import asyncio
import logging

from celery_app import app
from tasks.base import build_adapter, get_supabase, locked_task

log = logging.getLogger(__name__)


@app.task(name="tasks.data_refresh.refresh_company", bind=True, max_retries=1)
@locked_task(lambda self, company_id=None, reason="": f"data_refresh_{company_id}", ttl_seconds=600)
def refresh_company(self, company_id: str, reason: str = ""):
    from app.scheduler.dashboard_snapshot import compute_dashboard_payload, store_snapshot

    if not company_id:
        return {"ok": False, "error": "no company"}
    db = get_supabase()
    result = db.table("companies").select("*").eq("id", company_id).execute()
    if not result.data:
        log.warning("data_refresh: company %s not found", company_id)
        return {"ok": False, "error": "company not found"}
    company = result.data[0]
    if company.get("qb_connection_status") == "disconnected" or not company.get("qb_type"):
        return {"ok": False, "error": "quickbooks not connected"}

    try:
        adapter = build_adapter(company)
        payload = asyncio.run(compute_dashboard_payload(adapter))
        store_snapshot(db, company_id, "dashboard_summary", payload, generated_by=reason or "on_demand")
    except Exception as exc:
        log.error("data_refresh failed for %s: %s", company_id, exc)
        return {"ok": False, "error": "refresh failed"}
    log.info("data_refresh: snapshot rebuilt for %s (%s)", company_id, reason or "on_demand")
    return {"ok": True}
