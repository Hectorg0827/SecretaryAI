"""Morning briefing task — sends daily summary to all active companies."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock
from app.scheduler.morning_briefing import generate_morning_briefing
from app.ai.data_summarizer import build_query_context

log = logging.getLogger(__name__)


@app.task(name="tasks.morning_briefing.send_morning_briefing_all", bind=True, max_retries=2)
def send_morning_briefing_all(self):
    with task_lock("morning_briefing", ttl_seconds=3600) as acquired:
        if not acquired:
            log.info("Morning briefing already running — skipping")
            return {"skipped": True}
    db = get_supabase()
    companies = get_active_companies(db)

    for company in companies:
        try:
            adapter = build_adapter(company)
            data_summary = asyncio.run(_build_summary(adapter))
            briefing = asyncio.run(
                generate_morning_briefing(
                    company_id=company["id"],
                    company_name=company["name"],
                    preferred_language=company.get("preferred_language", "English"),
                    data_summary=data_summary,
                    recipient_email=company.get("alert_email", ""),
                )
            )
            log.info("Morning briefing sent for company %s", company["id"])
        except Exception as e:
            log.error("Morning briefing failed for %s: %s", company["id"], e)


async def _build_summary(adapter) -> str:
    try:
        customers = await adapter.get_all_customers()
        inventory = await adapter.get_inventory_merged()
        return build_query_context(
            "general_question",
            {"accounts": [{"name": c.name, "health_status": "unknown"} for c in customers],
             "inventory": inventory},
        )
    except Exception:
        return "Data unavailable for briefing."
