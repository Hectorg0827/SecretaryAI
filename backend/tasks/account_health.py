"""Account health refresh task — runs nightly at 12:30 AM UTC."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock
from app.scheduler.account_health_refresh import refresh_all_account_health

log = logging.getLogger(__name__)


@app.task(name="tasks.account_health.refresh_all", bind=True, max_retries=2)
def refresh_all(self):
    with task_lock("account_health_refresh", ttl_seconds=3600) as acquired:
        if not acquired:
            log.info("Account health refresh already running — skipping")
            return {"skipped": True}
    db = get_supabase()
    companies = get_active_companies(db)

    for company in companies:
        try:
            adapter = build_adapter(company)
            result = asyncio.run(refresh_all_account_health(company["id"], adapter, db))
            log.info(
                "Account health refresh for company %s: %d updated, %d alerts",
                company["id"],
                result.get("updated", 0),
                len(result.get("alerts", [])),
            )
        except Exception as e:
            log.error("Account health refresh failed for %s: %s", company["id"], e)
