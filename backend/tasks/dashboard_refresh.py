"""Dashboard-only refresh task.

No email, billing, purchase order, or external mutations are performed.
The dashboard refresh route must never dispatch the morning-briefing task,
which has independent notification policy and scheduling.
"""
from __future__ import annotations

import asyncio
import logging

from celery_app import app
from tasks.base import (
    get_supabase, get_active_companies, build_adapter, task_lock, locked_task,
)
from app.scheduler.dashboard_snapshot import compute_dashboard_payload, store_snapshot

log = logging.getLogger(__name__)


def refresh_company_snapshot(company_id: str, *, db, companies, adapter_factory) -> dict:
    """Purely read from the provider; persist one tenant-scoped dashboard snapshot."""
    if not isinstance(company_id, str) or not company_id.strip():
        raise ValueError("company_id is required")
    company = next((item for item in companies if item.get("id") == company_id), None)
    if company is None:
        raise ValueError("Company is not eligible for dashboard refresh")

    adapter = adapter_factory(company)
    # Without a DB the adapter cannot read inventory sell rates, so no item
    # would ever be assessed; request-time adapters attach it the same way.
    adapter.set_db(db)
    payload = asyncio.run(compute_dashboard_payload(adapter))
    store_snapshot(
        db, company_id, "dashboard_summary", payload,
        generated_by="manual_dashboard_refresh",
    )
    return {
        "company_id": company_id,
        "status": "refreshed",
        "source_quality": payload["source_quality"],
        "source_read_status": payload["source_read_status"],
    }


@app.task(name="tasks.dashboard_refresh.refresh_dashboard_snapshot", bind=True, max_retries=2)
@locked_task(lambda self, company_id: f"dashboard_refresh_{company_id}", ttl_seconds=3600)
def refresh_dashboard_snapshot(self, company_id: str) -> dict:
    """Read-only dashboard refresh; no communications or financial writes."""
    db = get_supabase()
    return refresh_company_snapshot(
        company_id, db=db, companies=get_active_companies(db),
        adapter_factory=build_adapter,
    )
