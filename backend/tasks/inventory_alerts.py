"""Inventory alert task — runs every 6 hours."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock, locked_task
from app.scheduler.inventory_alert_check import check_and_alert_inventory

log = logging.getLogger(__name__)


@app.task(name="tasks.inventory_alerts.check_all", bind=True, max_retries=2)
@locked_task("inventory_alerts", ttl_seconds=3600)
def check_all(self):
    db = get_supabase()
    companies = get_active_companies(db)

    from app.scheduler.dashboard_snapshot import store_snapshot

    for company in companies:
        try:
            adapter = build_adapter(company)
            # Alerts go through the ActionEngine: it applies the autonomy
            # rules, sends the email, and records the action. (This used to
            # pass None, so critical-stock alerts were computed and never sent.)
            from app.actions.engine import ActionEngine
            result = asyncio.run(
                check_and_alert_inventory(
                    company_id=company["id"],
                    adapter=adapter,
                    action_engine=ActionEngine(db),
                    user_id="scheduler",
                    alert_email=company.get("alert_email"),
                )
            )
            log.info(
                "Inventory alert check for company %s: %d critical, %d low, %d alerts fired",
                company["id"],
                len(result.get("critical", [])),
                len(result.get("low", [])),
                result.get("alerts_fired", 0),
            )
            # Persist results so all employees read the same snapshot
            store_snapshot(db, company["id"], "inventory_alerts", result)
        except Exception as e:
            log.error("Inventory alert check failed for %s: %s", company["id"], e)
