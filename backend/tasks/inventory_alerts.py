"""Inventory alert task — runs every 6 hours."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock
from app.scheduler.inventory_alert_check import check_and_alert_inventory

log = logging.getLogger(__name__)


@app.task(name="tasks.inventory_alerts.check_all", bind=True, max_retries=2)
def check_all(self):
    with task_lock("inventory_alerts", ttl_seconds=3600) as acquired:
        if not acquired:
            log.info("Inventory alert check already running — skipping")
            return {"skipped": True}
    db = get_supabase()
    companies = get_active_companies(db)

    for company in companies:
        try:
            adapter = build_adapter(company)
            # action_engine requires async context; pass None — alerts are
            # logged but not fired in this lightweight task wrapper.
            # Full alert firing happens inside check_and_alert_inventory when
            # action_engine is provided.
            result = asyncio.run(
                check_and_alert_inventory(
                    company_id=company["id"],
                    adapter=adapter,
                    action_engine=None,
                    user_id=company.get("owner_user_id", "system"),
                )
            )
            log.info(
                "Inventory alert check for company %s: %d critical, %d low, %d alerts fired",
                company["id"],
                len(result.get("critical", [])),
                len(result.get("low", [])),
                result.get("alerts_fired", 0),
            )
        except Exception as e:
            log.error("Inventory alert check failed for %s: %s", company["id"], e)
