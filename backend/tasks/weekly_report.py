"""Weekly report task — builds each active company's leadership summary.

The report is stored for owners and managers to read in the app
(GET /api/briefing/weekly) and a push notification says it is ready.
No email is sent. A data outage is recorded as a failed run: nothing is
sent to the AI writer, stored as a report, or announced.

Log lines are constant events (tenant IDs and provider errors stay out).
"""
import asyncio
import logging
from datetime import date, datetime, timezone

from celery_app import app
from tasks.base import get_supabase, get_active_companies, build_adapter, task_lock, locked_task  # noqa: F401
from app.scheduler.weekly_report import (
    WeeklyReportGenerationFailed,
    collect_weekly_figures,
    figures_to_text,
    generate_weekly_report,
    record_weekly_report_run,
    report_period,
    store_weekly_report,
)

log = logging.getLogger(__name__)

READY_TITLE = "Your weekly report is ready"
READY_BODY = "Last week's business summary is ready. Open SecretaryAI to read it."


@app.task(name="tasks.weekly_report.send_weekly_report_all", bind=True, max_retries=2)
@locked_task("weekly_report", ttl_seconds=3600)
def send_weekly_report_all(self):
    db = get_supabase()
    companies = get_active_companies(db)
    today = datetime.now(timezone.utc).date()

    summary = {"stored": 0, "failed": 0, "notified": 0}
    for company in companies:
        outcome = run_company_weekly_report(
            company, db=db, adapter_factory=build_adapter, today=today,
        )
        if outcome["status"] == "stored":
            summary["stored"] += 1
            summary["notified"] += int(outcome["notified"])
        else:
            summary["failed"] += 1

    log.info("Weekly report run complete")
    return summary


def run_company_weekly_report(company: dict, *, db, adapter_factory, today: date) -> dict:
    """
    Build, store and announce one company's weekly report.

    Returns {"status": "stored", "notified": bool} or
    {"status": "failed", "reason": ...}. Never raises for one company, so
    one tenant's outage cannot stop the others' reports.
    """
    company_id = company["id"]
    period_start, period_end = report_period(today)
    language = company.get("preferred_language") or "English"

    def failed(reason: str) -> dict:
        try:
            record_weekly_report_run(
                db, company_id, status="failed", reason=reason,
                period_start=period_start, period_end=period_end,
            )
        except Exception:
            log.error("Weekly report: could not record failed run")
        return {"status": "failed", "reason": reason}

    try:
        adapter = adapter_factory(company)
        # Without a DB the adapter cannot read inventory sell rates.
        adapter.set_db(db)
        collected = asyncio.run(
            collect_weekly_figures(adapter, period_start=period_start, period_end=period_end)
        )
    except Exception:
        # WeeklyReportSourceUnavailable or a broken adapter: same outcome.
        log.warning("Weekly report: source data unavailable; run recorded as failed")
        return failed("source_unavailable")

    try:
        report_text = asyncio.run(
            generate_weekly_report(
                company_id=company_id,
                company_name=company.get("name") or "your company",
                preferred_language=language,
                data_summary=figures_to_text(
                    collected, period_start=period_start, period_end=period_end,
                ),
                period_start=period_start,
                period_end=period_end,
            )
        )
        if not isinstance(report_text, str) or not report_text.strip():
            raise WeeklyReportGenerationFailed("Empty weekly report")
    except Exception:
        log.warning("Weekly report: writing the report failed; run recorded as failed")
        return failed("generation_failed")

    try:
        store_weekly_report(
            db, company_id,
            report_text=report_text.strip(),
            period_start=period_start, period_end=period_end,
            language=language, figures=collected["figures"],
        )
    except Exception:
        log.error("Weekly report: could not store report")
        return failed("storage_failed")

    try:
        record_weekly_report_run(
            db, company_id, status="succeeded",
            period_start=period_start, period_end=period_end,
        )
    except Exception:
        # The report itself is stored and readable; only the run marker is stale.
        log.warning("Weekly report: could not record successful run")

    return {"status": "stored", "notified": _notify_ready(db, company_id)}


def _notify_ready(db, company_id: str) -> bool:
    """Push 'report ready' to owners and managers; failure never undoes the report."""
    from app.api.notifications import send_push_to_company_leaders

    try:
        return asyncio.run(
            send_push_to_company_leaders(
                db=db,
                company_id=company_id,
                title=READY_TITLE,
                body=READY_BODY,
                data={"type": "weekly_report_ready"},
            )
        )
    except Exception:
        log.warning("Weekly report: ready push failed")
        return False
