"""
Proactive insights task — scans all companies for actionable feed events.
Runs daily. Avoids creating duplicate events for the same entity.
"""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock

log = logging.getLogger(__name__)


@app.task(name="tasks.proactive_insights.generate_all", bind=True, max_retries=2)
def generate_all(self):
    with task_lock("proactive_insights", ttl_seconds=3600) as acquired:
        if not acquired:
            log.info("Proactive insights already running — skipping")
            return {"skipped": True}
    db = get_supabase()
    companies = get_active_companies(db)
    total_events = 0

    for company in companies:
        try:
            adapter = build_adapter(company)
            events = asyncio.run(_generate_for_company(company["id"], adapter))
            _save_events(db, company["id"], events)
            total_events += len(events)
            log.info("Proactive insights: %d events for company %s", len(events), company["id"])
        except Exception as e:
            log.error("Proactive insights failed for %s: %s", company["id"], e)

    return {"companies": len(companies), "total_events": total_events}


async def _generate_for_company(company_id: str, adapter) -> list[dict]:
    from app.intelligence.opportunity_detector import detect_opportunities
    customers = await adapter.get_all_customers()
    invoices = await adapter.get_orders_last_n_days(90)
    inventory = await adapter.get_inventory_merged()
    return detect_opportunities(customers, invoices, inventory)


def _save_events(db, company_id: str, events: list[dict]) -> None:
    """Insert new events, skip if a non-dismissed event for the same entity+type exists today."""
    from datetime import date, timedelta, timezone
    from datetime import datetime

    if not events:
        return

    # Load existing active events for this company (last 7 days, not dismissed)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    existing = (
        db.table("feed_events")
        .select("event_type, entity_id")
        .eq("company_id", company_id)
        .eq("is_dismissed", False)
        .gte("created_at", cutoff)
        .execute()
    ).data or []
    existing_keys = {(r["event_type"], r["entity_id"]) for r in existing}

    new_events = []
    for ev in events:
        key = (ev["event_type"], ev.get("entity_id", ""))
        if key not in existing_keys:
            new_events.append({"company_id": company_id, **ev})

    if new_events:
        db.table("feed_events").insert(new_events).execute()
