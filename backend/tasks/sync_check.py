"""Sync health check task — runs every 4 hours."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter
from app.scheduler.sync_check import run_sync_check

log = logging.getLogger(__name__)


@app.task(name="tasks.sync_check.run_sync_check_all", bind=True, max_retries=2)
def run_sync_check_all(self):
    db = get_supabase()
    companies = get_active_companies(db)

    for company in companies:
        try:
            adapter = build_adapter(company)
            result = asyncio.run(run_sync_check(company["id"], adapter, db))
            status = result.get("status", "unknown")
            log.info(
                "Sync check for company %s: %s (%d discrepancies)",
                company["id"],
                status,
                len(result.get("discrepancies", [])),
            )
        except Exception as e:
            log.error("Sync check failed for %s: %s", company["id"], e)
