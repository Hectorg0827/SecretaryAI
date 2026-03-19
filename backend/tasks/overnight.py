"""Overnight scan — runs nightly for all companies."""
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter
from app.scheduler.overnight_scan import run_overnight_scan

log = logging.getLogger(__name__)


@app.task(name="tasks.overnight.run_overnight_scan_all_companies", bind=True, max_retries=2)
def run_overnight_scan_all_companies(self):
    db = get_supabase()
    companies = get_active_companies(db)
    log.info("Overnight scan: %d companies", len(companies))

    results = []
    for company in companies:
        try:
            import asyncio
            adapter = build_adapter(company)
            result = asyncio.run(run_overnight_scan(company["id"], adapter))
            results.append({"company_id": company["id"], "ok": True, **result})
        except Exception as e:
            log.error("Overnight scan failed for %s: %s", company["id"], e)
            results.append({"company_id": company["id"], "ok": False, "error": str(e)})

    return results
