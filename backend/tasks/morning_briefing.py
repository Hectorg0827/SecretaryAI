"""Morning briefing task — sends daily summary to all active companies."""
import asyncio
import logging
from datetime import datetime, timezone
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock, locked_task
from app.scheduler.morning_briefing import generate_morning_briefing
from app.scheduler.dashboard_snapshot import compute_dashboard_payload, store_snapshot
from app.ai.data_summarizer import build_query_context
from app.scheduler.briefing_alerts import (
    alerts_to_text,
    build_briefing_alerts,
    load_alert_history,
    save_alert_history,
)

log = logging.getLogger(__name__)


@app.task(name="tasks.morning_briefing.send_morning_briefing_all", bind=True, max_retries=2)
@locked_task(lambda self, company_id=None: f"morning_briefing_{company_id}" if company_id else "morning_briefing", ttl_seconds=3600)
def send_morning_briefing_all(self, company_id: str | None = None):
    """
    Generate morning briefings for all active companies (or a single company
    when company_id is provided for on-demand refresh).
    Also computes and stores the dashboard_summary snapshot so employees
    read from the cache for the rest of the day instead of hitting QB.
    """
    db = get_supabase()
    companies = get_active_companies(db)
    if company_id:
        companies = [c for c in companies if c["id"] == company_id]

    for company in companies:
        try:
            adapter = build_adapter(company)
            # Needed to read inventory sell rates for the snapshot below.
            adapter.set_db(db)

            # Compute dashboard KPIs (expensive QB calls happen here, once per company)
            dashboard_payload = asyncio.run(compute_dashboard_payload(adapter))

            # Persist as the shared dashboard snapshot — all employees read this
            store_snapshot(db, company["id"], "dashboard_summary", dashboard_payload)

            # Build text summary for the briefing email
            data_summary = _payload_to_text(dashboard_payload, company["name"])

            # Proactive alerts: merge duplicates and mark what is new since
            # the last briefing vs. still open. Nothing is hidden.
            alert_result = None
            try:
                now = datetime.now(timezone.utc)
                alert_result = build_briefing_alerts(
                    dashboard_payload.get("inventory_alerts", []),
                    company_id=company["id"],
                    now=now,
                    history=load_alert_history(db, company["id"], now=now),
                )
                data_summary = f"{data_summary}\n\n{alerts_to_text(alert_result)}"
            except Exception:
                log.warning("Briefing alert section skipped for %s", company["id"])

            asyncio.run(
                generate_morning_briefing(
                    company_id=company["id"],
                    company_name=company["name"],
                    preferred_language=company.get("preferred_language", "English"),
                    data_summary=data_summary,
                    recipient_email=company.get("alert_email", ""),
                )
            )
            # Only after the briefing was produced: remember what it reported.
            if alert_result is not None:
                save_alert_history(db, company["id"], alert_result)
            log.info(
                "Morning briefing + dashboard snapshot stored for company %s",
                company["id"],
            )
        except Exception as e:
            log.error("Morning briefing failed for %s: %s", company["id"], e)


def _payload_to_text(payload: dict, company_name: str) -> str:
    """Convert the structured dashboard payload into a text summary for the LLM."""
    try:
        from app.ai.data_summarizer import build_query_context
        accounts = payload.get("accounts", {})
        inventory = payload.get("inventory_alerts", [])
        return build_query_context(
            "general_question",
            {
                "accounts": [
                    {"name": f"{v} {k}", "health_status": k}
                    for k, v in accounts.items() if k != "total" and v > 0
                ],
                "inventory": inventory,
            },
        )
    except Exception:
        return "Data unavailable for briefing."
