"""Weekly report task — sends leadership summary to all active companies."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter
from app.scheduler.weekly_report import generate_weekly_report
from app.ai.data_summarizer import build_query_context

log = logging.getLogger(__name__)


@app.task(name="tasks.weekly_report.send_weekly_report_all", bind=True, max_retries=2)
def send_weekly_report_all(self):
    db = get_supabase()
    companies = get_active_companies(db)

    for company in companies:
        try:
            adapter = build_adapter(company)
            data_summary = asyncio.run(_build_summary(adapter))
            report = asyncio.run(
                generate_weekly_report(
                    company_id=company["id"],
                    company_name=company["name"],
                    preferred_language=company.get("preferred_language", "English"),
                    data_summary=data_summary,
                )
            )
            log.info("Weekly report generated for company %s", company["id"])
        except Exception as e:
            log.error("Weekly report failed for %s: %s", company["id"], e)


async def _build_summary(adapter) -> str:
    try:
        customers = await adapter.get_all_customers()
        inventory = await adapter.get_inventory_merged()
        orders = await adapter.get_orders_last_n_days(7)
        return build_query_context(
            "general_question",
            {
                "accounts": [{"name": c.name, "health_status": "unknown"} for c in customers],
                "inventory": inventory,
                "recent_orders": [
                    {"customer": getattr(o, "customer_name", ""), "total": float(getattr(o, "total", 0))}
                    for o in orders
                ],
            },
        )
    except Exception:
        return "Data unavailable for weekly report."
