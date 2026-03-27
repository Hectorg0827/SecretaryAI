"""Compliance alert task — runs daily at 8 AM UTC."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, task_lock

log = logging.getLogger(__name__)


@app.task(name="tasks.compliance_alerts.check_all", bind=True, max_retries=2)
def check_all(self):
    """
    Run the compliance alert check for every active company.
    Results are stored in report_snapshots — no automatic sending.
    Compliance officers review alerts via the Compliance UI.
    """
    with task_lock("compliance_alerts", ttl_seconds=3600) as acquired:
        if not acquired:
            log.info("Compliance alert check already running — skipping")
            return {"skipped": True}

    db = get_supabase()

    # Fetch all companies (compliance applies regardless of QB connection status)
    companies_result = db.table("companies").select("id,name").execute()
    companies = companies_result.data or []

    summary = {"processed": 0, "errors": 0, "total_critical": 0}

    from app.scheduler.compliance_alert_check import check_compliance_alerts

    for company in companies:
        try:
            result = asyncio.run(
                check_compliance_alerts(
                    company_id=company["id"],
                    db=db,
                )
            )
            summary["processed"] += 1
            summary["total_critical"] += result.get("critical", 0)
            if result.get("error"):
                summary["errors"] += 1
                log.warning(
                    "Compliance check partial error for %s: %s",
                    company["id"],
                    result["error"],
                )
        except Exception as exc:
            summary["errors"] += 1
            log.error("Compliance alert check failed for %s: %s", company["id"], exc)

    log.info(
        "Compliance alert check complete: %d companies, %d errors, %d critical alerts total",
        summary["processed"],
        summary["errors"],
        summary["total_critical"],
    )
    return summary
