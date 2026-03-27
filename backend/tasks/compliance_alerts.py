"""Compliance alert task — runs daily at 8 AM UTC."""
import asyncio
import logging
from celery_app import app
from tasks.base import get_supabase, task_lock

log = logging.getLogger(__name__)


async def _notify_compliance(db, company_id: str, critical: int, warning: int) -> None:
    """
    Send a push notification to all devices in the company when there are
    critical or warning compliance alerts.
    """
    from app.api.notifications import send_push_notifications

    if critical > 0:
        parts = [f"{critical} critical"]
        if warning > 0:
            parts.append(f"{warning} warning")
        body = f"{', '.join(parts)} alert{'s' if (critical + warning) > 1 else ''} require your attention."
        await send_push_notifications(
            db=db,
            company_id=company_id,
            title="Compliance alerts",
            body=body,
            data={"type": "compliance_alert", "critical": critical, "warning": warning},
        )
    elif warning > 0:
        await send_push_notifications(
            db=db,
            company_id=company_id,
            title="Compliance reminders",
            body=f"{warning} compliance item{'s' if warning > 1 else ''} need attention soon.",
            data={"type": "compliance_alert", "critical": 0, "warning": warning},
        )


@app.task(name="tasks.compliance_alerts.check_all", bind=True, max_retries=2)
def check_all(self):
    """
    Run the compliance alert check for every active company.
    Results are stored in report_snapshots for review in the Compliance UI.
    Critical and warning alerts also trigger push notifications.
    """
    with task_lock("compliance_alerts", ttl_seconds=3600) as acquired:
        if not acquired:
            log.info("Compliance alert check already running — skipping")
            return {"skipped": True}

    db = get_supabase()

    # Fetch all companies (compliance applies regardless of QB connection status)
    companies_result = db.table("companies").select("id,name").execute()
    companies = companies_result.data or []

    summary = {"processed": 0, "errors": 0, "total_critical": 0, "total_warning": 0, "notified": 0}

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
            critical = result.get("critical", 0)
            warning = result.get("warning", 0)
            summary["total_critical"] += critical
            summary["total_warning"] += warning

            if result.get("error"):
                summary["errors"] += 1
                log.warning(
                    "Compliance check partial error for %s: %s",
                    company["id"],
                    result["error"],
                )

            # Push notifications for companies with actionable alerts
            if critical > 0 or warning > 0:
                try:
                    asyncio.run(_notify_compliance(db, company["id"], critical, warning))
                    summary["notified"] += 1
                except Exception as exc:
                    log.warning(
                        "Push notification failed for compliance alerts (company %s): %s",
                        company["id"],
                        exc,
                    )

        except Exception as exc:
            summary["errors"] += 1
            log.error("Compliance alert check failed for %s: %s", company["id"], exc)

    log.info(
        "Compliance alert check complete: %d companies, %d errors, "
        "%d critical / %d warning alerts, %d companies notified",
        summary["processed"],
        summary["errors"],
        summary["total_critical"],
        summary["total_warning"],
        summary["notified"],
    )
    return summary
