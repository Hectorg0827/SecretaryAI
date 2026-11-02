"""
Weekly report generator — runs Sunday morning (tasks/weekly_report.py).
Produces a leadership-level summary: top accounts, sales trend,
inventory status, any issues from the week.

The finished report is stored in report_snapshots (report_type
'weekly_report') for owners and managers to read in the app
(GET /api/briefing/weekly). Every run also records its outcome under
'weekly_report_run', so a data outage shows up as a failed run and is never
stored or announced as a report. No email is sent from here.

Log lines are constant events (tenant IDs and provider errors stay out).
"""
import logging
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings
from app.ai.model_router import model_for
from app.scheduler.dashboard_snapshot import (
    SnapshotReadUnavailable,
    _invoice_amount,
    _invoice_date,
    _row_value,
    compute_dashboard_payload,
    store_snapshot,
)

log = logging.getLogger(__name__)
settings = get_settings()

WEEKLY_REPORT_TYPE = "weekly_report"
WEEKLY_REPORT_RUN_TYPE = "weekly_report_run"
_TOP_ACCOUNTS = 5
_TOP_STOCK_ITEMS = 5


class WeeklyReportSourceUnavailable(RuntimeError):
    """Business data could not be read; no report may be produced from it."""


class WeeklyReportGenerationFailed(RuntimeError):
    """The writer returned no usable report text."""


class WeeklyReportInvalid(ValueError):
    """A stored weekly report or run record does not meet the read contract."""


# ─── Stored contract (validated on write and on read) ─────────────────────────

class WeeklyFiguresV1(BaseModel):
    """The source figures the report was written from, shown next to it."""
    model_config = ConfigDict(extra="forbid")

    # Sales totals may be negative in a week dominated by credits.
    sales_this_week: float = Field(allow_inf_nan=False)
    invoices_this_week: int = Field(ge=0)
    sales_prior_week: float = Field(allow_inf_nan=False)
    invoices_prior_week: int = Field(ge=0)
    sales_30d: float = Field(allow_inf_nan=False)
    accounts_total: int = Field(ge=0)
    accounts_slowing: int = Field(ge=0)
    accounts_at_risk: int = Field(ge=0)
    accounts_dormant: int = Field(ge=0)
    accounts_unscored: int = Field(ge=0)
    inventory_critical: int = Field(ge=0)
    inventory_low: int = Field(ge=0)
    inventory_unassessed: int = Field(ge=0)


class WeeklyReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"]
    report_text: str = Field(min_length=1)
    period_start: date
    period_end: date
    language: str = Field(min_length=1)
    source_quality: Literal["partial"]
    figures: WeeklyFiguresV1
    generated_at: Optional[datetime] = None


class WeeklyReportRunV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["succeeded", "failed"]
    reason: Optional[Literal["source_unavailable", "generation_failed", "storage_failed"]] = None
    period_start: date
    period_end: date
    attempted_at: Optional[datetime] = None


class WeeklyReportResponseV1(BaseModel):
    """GET /api/briefing/weekly — latest report and the latest run's outcome."""
    model_config = ConfigDict(extra="forbid")

    report: Optional[WeeklyReportV1]
    last_run: Optional[WeeklyReportRunV1]


# ─── Period and source figures ────────────────────────────────────────────────

def report_period(today: date) -> tuple[date, date]:
    """The seven complete days before the run date (today is still in progress)."""
    return today - timedelta(days=7), today - timedelta(days=1)


async def collect_weekly_figures(adapter, *, period_start: date, period_end: date) -> dict:
    """
    Read this week's and last week's sales plus the fail-closed dashboard
    figures (account health, stock alerts, 30-day sales).

    Any failed or untrustworthy read raises WeeklyReportSourceUnavailable;
    it is never turned into zeros or placeholder text.
    """
    prior_start = period_start - timedelta(days=7)
    try:
        dashboard = await compute_dashboard_payload(adapter)
        invoices = await adapter.get_orders_last_n_days(14)

        sales = {"this": 0.0, "prior": 0.0}
        counts = {"this": 0, "prior": 0}
        by_customer: dict[str, float] = {}
        for inv in invoices:
            when = _invoice_date(inv)
            amount = _invoice_amount(inv)
            if period_start <= when <= period_end:
                bucket = "this"
                name = str(_row_value(inv, "customer_name", "") or "").strip()
                if name:
                    by_customer[name] = by_customer.get(name, 0.0) + amount
            elif prior_start <= when < period_start:
                bucket = "prior"
            else:
                continue
            sales[bucket] += amount
            counts[bucket] += 1
        if not all(isfinite(v) for v in sales.values()):
            raise ValueError("Weekly sales total is not finite")

        accounts = dashboard["accounts"]
        alerts = dashboard["inventory_alerts"]
        critical = [a for a in alerts if a.get("stock_status") in ("critical", "out_of_stock")]
        low = [a for a in alerts if a.get("stock_status") == "low"]
        figures = WeeklyFiguresV1(
            sales_this_week=round(sales["this"], 2),
            invoices_this_week=counts["this"],
            sales_prior_week=round(sales["prior"], 2),
            invoices_prior_week=counts["prior"],
            sales_30d=dashboard["sales_30d"],
            accounts_total=accounts["total"],
            accounts_slowing=accounts["slowing"],
            accounts_at_risk=accounts["at_risk"],
            accounts_dormant=accounts["dormant"],
            accounts_unscored=accounts.get("unknown", 0),
            inventory_critical=len(critical),
            inventory_low=len(low),
            inventory_unassessed=dashboard.get("inventory_unassessed", 0),
        ).model_dump()
    except Exception as exc:
        log.error("Weekly report: business data unavailable")
        raise WeeklyReportSourceUnavailable("Weekly report source data unavailable") from exc

    top_accounts = sorted(by_customer.items(), key=lambda kv: kv[1], reverse=True)[:_TOP_ACCOUNTS]
    return {
        "figures": figures,
        "top_accounts": [(name, round(total, 2)) for name, total in top_accounts],
        "critical_items": [str(a.get("product_name") or "") for a in critical[:_TOP_STOCK_ITEMS]],
        "low_items": [str(a.get("product_name") or "") for a in low[:_TOP_STOCK_ITEMS]],
    }


def figures_to_text(collected: dict, *, period_start: date, period_end: date) -> str:
    """Plain summary for the writer; only figures that were actually read."""
    f = collected["figures"]
    lines = [
        f"Sales {period_start.isoformat()} to {period_end.isoformat()}: "
        f"${f['sales_this_week']:,.2f} across {f['invoices_this_week']} invoices.",
        f"Sales the week before: ${f['sales_prior_week']:,.2f} across "
        f"{f['invoices_prior_week']} invoices.",
        f"Sales in the last 30 days: ${f['sales_30d']:,.2f}.",
    ]
    if collected["top_accounts"]:
        lines.append("Top accounts this week by sales:")
        lines.extend(f"  - {name}: ${total:,.2f}" for name, total in collected["top_accounts"])
    else:
        lines.append("No named accounts had sales this week.")
    lines.append(
        f"Account health ({f['accounts_total']} accounts): {f['accounts_slowing']} slowing, "
        f"{f['accounts_at_risk']} at risk, {f['accounts_dormant']} dormant, "
        f"{f['accounts_unscored']} not scored."
    )
    lines.append(
        f"Stock alerts: {f['inventory_critical']} critical or out of stock, "
        f"{f['inventory_low']} low; {f['inventory_unassessed']} in-stock products "
        "not assessed (no measured sell rate)."
    )
    if collected["critical_items"]:
        lines.append("Critical or out of stock: " + ", ".join(collected["critical_items"]))
    if collected["low_items"]:
        lines.append("Low stock: " + ", ".join(collected["low_items"]))
    return "\n".join(lines)


# ─── Writer ───────────────────────────────────────────────────────────────────

async def generate_weekly_report(
    company_id: str,
    company_name: str,
    preferred_language: str,
    data_summary: str,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> str:
    """Generate the weekly leadership summary text."""
    if period_start is None or period_end is None:
        period_start, period_end = report_period(datetime.now(timezone.utc).date())

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    prompt = f"""Generate a weekly business report for {company_name}.
Week: {period_start.isoformat()} to {period_end.isoformat()}

Include:
1. Sales summary (total revenue, top accounts, vs prior week)
2. Inventory status (any critical or low items)
3. Account health (accounts that are slowing, at risk or dormant)
4. Open issues / anomalies detected
5. Recommended actions for next week (max 3 bullets)

Use only the business data below. If a figure is not in it, say it is not
available; never estimate or invent numbers.
Keep it under 300 words. Executive-level — no fluff.
Language: {preferred_language}

Business data:
{data_summary}"""

    response = await client.messages.create(
        model=model_for("weekly_report"),
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ─── Storage ──────────────────────────────────────────────────────────────────

def store_weekly_report(
    db, company_id: str, *, report_text: str, period_start: date, period_end: date,
    language: str, figures: dict,
) -> None:
    """Persist the company's latest weekly report (one row per company)."""
    report = WeeklyReportV1(
        status="ready",
        report_text=report_text,
        period_start=period_start,
        period_end=period_end,
        language=language,
        source_quality="partial",
        figures=figures,
    )
    store_snapshot(
        db, company_id, WEEKLY_REPORT_TYPE,
        report.model_dump(mode="json", exclude={"generated_at"}),
        generated_by="weekly_report",
    )


def record_weekly_report_run(
    db, company_id: str, *, status: str, period_start: date, period_end: date,
    reason: Optional[str] = None,
) -> None:
    """Record the latest run's outcome; a failure never replaces the last report."""
    run = WeeklyReportRunV1(
        status=status, reason=reason,
        period_start=period_start, period_end=period_end,
    )
    store_snapshot(
        db, company_id, WEEKLY_REPORT_RUN_TYPE,
        run.model_dump(mode="json", exclude={"attempted_at"}),
        generated_by="weekly_report",
    )


def read_weekly_report(db, company_id: str) -> dict:
    """
    Return {"report": ..., "last_run": ...} for one company; either may be None
    when nothing was stored yet. A store outage raises SnapshotReadUnavailable;
    a malformed row raises WeeklyReportInvalid rather than being shown.
    """
    # A plain filtered list read: "no row yet" is an empty list, never an
    # error (postgrest's maybe_single() returns None for zero rows).
    try:
        result = (
            db.table("report_snapshots")
            .select("report_type,payload,generated_at")
            .eq("company_id", company_id)
            .in_("report_type", [WEEKLY_REPORT_TYPE, WEEKLY_REPORT_RUN_TYPE])
            .execute()
        )
    except Exception as exc:
        log.error("Weekly report: report store unavailable")
        raise SnapshotReadUnavailable("Weekly report storage unavailable") from exc
    rows = getattr(result, "data", None)
    if not isinstance(rows, list):
        log.error("Weekly report: report store returned no result")
        raise SnapshotReadUnavailable("Weekly report storage unavailable")
    by_type = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("report_type") in by_type:
            log.error("Weekly report: stored report invalid")
            raise WeeklyReportInvalid("Stored weekly report invalid")
        by_type[row.get("report_type")] = row

    report_row = by_type.get(WEEKLY_REPORT_TYPE)
    run_row = by_type.get(WEEKLY_REPORT_RUN_TYPE)
    try:
        report = None
        if report_row:
            report = WeeklyReportV1.model_validate({
                **(report_row.get("payload") or {}),
                "generated_at": report_row.get("generated_at"),
            })
        last_run = None
        if run_row:
            last_run = WeeklyReportRunV1.model_validate({
                **(run_row.get("payload") or {}),
                "attempted_at": run_row.get("generated_at"),
            })
    except (ValidationError, TypeError) as exc:
        log.error("Weekly report: stored report invalid")
        raise WeeklyReportInvalid("Stored weekly report invalid") from exc
    return WeeklyReportResponseV1(report=report, last_run=last_run).model_dump(mode="json")
