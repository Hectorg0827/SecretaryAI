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
from app.scheduler.finance_comparison import compare_invoice_headers_to_qbo_income
from app.scheduler.purchasing_coverage import read_purchasing_coverage
from app.scheduler.finance_document_coverage import read_finance_document_coverage

log = logging.getLogger(__name__)


def refresh_company_snapshot(
    company_id: str, *, db, companies, adapter_factory,
    generated_by: str = "manual_dashboard_refresh",
    include_finance_comparison: bool = False,
    include_purchasing_coverage: bool = False,
    include_finance_document_coverage: bool = False,
    stage_hook=None,
) -> dict:
    """Purely read from the provider; persist one tenant-scoped dashboard snapshot."""
    if not isinstance(company_id, str) or not company_id.strip():
        raise ValueError("company_id is required")
    company = next((item for item in companies if item.get("id") == company_id), None)
    if company is None:
        raise ValueError("Company is not eligible for dashboard refresh")

    # The cron supplies a fixed-label stage tracker; standard callers may omit it.
    # Never pass provider content or identifiers through this hook.
    def stage(name: str) -> None:
        if stage_hook is not None:
            stage_hook(name)

    stage("setup_provider_adapter")
    adapter = adapter_factory(company)
    # Without a DB the adapter cannot read inventory sell rates, so no item
    # would ever be assessed; request-time adapters attach it the same way.
    stage("attach_inventory_database")
    adapter.set_db(db)

    async def collect() -> dict:
        stage("read_required_dashboard_sources")
        payload = await compute_dashboard_payload(adapter)
        if include_finance_comparison:
            stage("read_optional_income_comparison")
            # This optional cross-check is only enabled by the hard-gated
            # single-company staging cron. A failed P&L read is exposed as
            # unavailable evidence, not as a verified $0 ledger report.
            payload["finance_comparison"] = await compare_invoice_headers_to_qbo_income(
                adapter, payload["sales_30d"]
            )
        if include_finance_document_coverage:
            stage("read_optional_finance_documents")
            payload["finance_document_coverage"] = await read_finance_document_coverage(adapter)
        return payload

    payload = asyncio.run(collect())
    if include_purchasing_coverage:
        stage("read_optional_purchasing")
        # Only the staging allowlisted cron opts into this source. Aggregate
        # tenant-scoped DB reads never call external systems or take actions.
        payload["purchasing_coverage"] = read_purchasing_coverage(db, company_id)
    stage("persist_snapshot")
    store_snapshot(
        db, company_id, "dashboard_summary", payload,
        generated_by=generated_by,
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
