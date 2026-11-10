"""Staging-only, read-only QBO income comparison for the executive briefing.

An invoice-header sum is *not* equivalent to recognized ledger income: sales
receipts, credit memos, tax, journal entries and adjustments can differ. This
module records the discrepancy without claiming general-ledger reconciliation.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import httpx

from app.connectors.qb_online import QBOReportAuthenticationUnavailable

log = logging.getLogger(__name__)

_CENTS = Decimal("0.01")
_MAX_AMOUNT = Decimal("1000000000000000")
_FAILURE_REASONS = frozenset({
    "report_read_or_validation_failed",  # legacy snapshots
    "provider_auth_failed", "provider_access_denied", "provider_bad_request",
    "provider_not_found", "provider_rate_limited", "provider_service_error",
    "provider_http_error", "provider_transport_error",
    "connector_unavailable", "report_no_data", "report_header_mismatch",
    "report_currency_missing", "report_rows_missing", "report_income_missing",
    "report_income_ambiguous", "report_income_total_invalid",
    "invoice_total_invalid", "unexpected_failure",
})


class FinanceReportInvalid(ValueError):
    """A QBO financial report cannot support a trusted comparison."""

    @property
    def code(self) -> str:
        return str(self) if str(self) in _FAILURE_REASONS else "report_income_total_invalid"


def _money(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise FinanceReportInvalid("Financial amount missing or invalid")
    try:
        number = Decimal(str(value).replace(",", ""))
        if not number.is_finite() or abs(number) >= _MAX_AMOUNT:
            raise FinanceReportInvalid("Financial amount outside supported range")
        return number.quantize(_CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise FinanceReportInvalid("Financial amount invalid") from exc


def _safe_failure_reason(error: Exception) -> str:
    """Stable categories only: never serialize provider response bodies or tokens."""
    if isinstance(error, QBOReportAuthenticationUnavailable):
        return "provider_auth_failed"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return {
            400: "provider_bad_request", 401: "provider_auth_failed",
            403: "provider_access_denied", 404: "provider_not_found",
            429: "provider_rate_limited",
        }.get(status, "provider_service_error" if status >= 500 else "provider_http_error")
    if isinstance(error, httpx.RequestError):
        return "provider_transport_error"
    if isinstance(error, FinanceReportInvalid):
        return error.code
    if isinstance(error, RuntimeError):
        return "connector_unavailable"
    return "unexpected_failure"


def parse_profit_loss_income(
    report: dict, *, date_from: date, date_to: date,
) -> tuple[Decimal, str]:
    """Extract only the top-level Total Income summary from an accrual P&L.

    Fail closed for missing/multiple income sections and unrecognized periods,
    rather than treating an absent income row as verified $0.
    """
    if not isinstance(report, dict) or "Fault" in report:
        raise FinanceReportInvalid("report_rows_missing")
    header = report.get("Header")
    if isinstance(header, dict) and any(
        isinstance(option, dict) and option.get("Name") == "NoReportData"
        and str(option.get("Value", "")).lower() == "true"
        for option in (header.get("Option") if isinstance(header.get("Option"), list) else [])
    ):
        raise FinanceReportInvalid("report_no_data")
    if not isinstance(header, dict) or (
        header.get("ReportName") != "ProfitAndLoss"
        or header.get("ReportBasis") != "Accrual"
        or header.get("StartPeriod") != date_from.isoformat()
        or header.get("EndPeriod") != date_to.isoformat()
        or header.get("SummarizeColumnsBy") != "Total"
    ):
        raise FinanceReportInvalid("report_header_mismatch")
    currency = header.get("Currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise FinanceReportInvalid("report_currency_missing")
    rows = report.get("Rows")
    entries = rows.get("Row") if isinstance(rows, dict) else None
    if not isinstance(entries, list):
        raise FinanceReportInvalid("report_rows_missing")
    income_sections = [row for row in entries if isinstance(row, dict)
                       and row.get("type") == "Section" and row.get("group") == "Income"]
    if len(income_sections) != 1:
        raise FinanceReportInvalid("report_income_missing" if not income_sections else "report_income_ambiguous")
    summary = income_sections[0].get("Summary")
    columns = summary.get("ColData") if isinstance(summary, dict) else None
    if not isinstance(columns, list) or len(columns) < 2 or any(not isinstance(x, dict) for x in columns):
        raise FinanceReportInvalid("report_income_total_invalid")
    if columns[0].get("value") != "Total Income":
        raise FinanceReportInvalid("report_income_total_invalid")
    amounts = [col.get("value") for col in columns[1:]
               if col.get("value") is not None and str(col.get("value")).strip()]
    if len(amounts) != 1:
        raise FinanceReportInvalid("report_income_total_invalid")
    try:
        amount = _money(amounts[0])
    except FinanceReportInvalid as exc:
        raise FinanceReportInvalid("report_income_total_invalid") from exc
    return amount, currency


async def compare_invoice_headers_to_qbo_income(adapter, invoice_total, *,
                                                as_of: date | None = None) -> dict:
    """Read the sandbox P&L and record a non-certified aggregate comparison."""
    end = as_of or date.today()
    start = end - timedelta(days=30)
    provenance = {
        "source_system": "qbo_profit_loss",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "comparison_is_reconciliation": False,
    }
    try:
        invoice = _money(invoice_total)
    except FinanceReportInvalid:
        return {
            **provenance, "status": "unavailable", "source_quality": "unavailable",
            "reason": "invoice_total_invalid",
        }
    try:
        report = await adapter.get_qbo_profit_loss_read_only(start, end)
        income, currency = parse_profit_loss_income(report, date_from=start, date_to=end)
        delta = income - invoice
    except Exception as exc:
        # A failed independent read does not invalidate the base dashboard,
        # but can never be represented as a matched source. Log only a stable
        # category, never an error string/body that could include credentials.
        reason = _safe_failure_reason(exc)
        log.warning("Read-only QBO income comparison unavailable: %s", reason)
        return {
            **provenance, "status": "unavailable", "source_quality": "unavailable",
            "reason": reason,
        }
    return {
        **provenance,
        "status": "aligned_not_certified" if abs(delta) <= _CENTS else "variance_detected",
        "source_quality": "partial",
        "currency": currency,
        "invoice_headers_total": str(invoice),
        "ledger_income_total": str(income),
        "variance_ledger_minus_invoices": str(delta),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_finance_comparison(raw: dict) -> dict:
    """Protect cached read clients from forged or inconsistent source labels."""
    if not isinstance(raw, dict) or raw.get("source_system") != "qbo_profit_loss" or raw.get("comparison_is_reconciliation") is not False:
        raise FinanceReportInvalid("Invalid financial comparison provenance")
    try:
        start = date.fromisoformat(raw["window_start"])
        end = date.fromisoformat(raw["window_end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FinanceReportInvalid("Invalid financial comparison period") from exc
    if start >= end or (end - start).days != 30:
        raise FinanceReportInvalid("Financial comparison has an inconsistent period")
    status = raw.get("status")
    if status == "unavailable":
        if raw.get("source_quality") != "unavailable" or raw.get("reason") not in _FAILURE_REASONS:
            raise FinanceReportInvalid("Financial comparison unavailable status invalid")
        if any(key in raw for key in ("invoice_headers_total", "ledger_income_total", "variance_ledger_minus_invoices", "currency", "fetched_at")):
            raise FinanceReportInvalid("Unavailable comparison cannot contain purported financial figures")
        return raw
    if status not in ("aligned_not_certified", "variance_detected") or raw.get("source_quality") != "partial":
        raise FinanceReportInvalid("Financial comparison certification unsupported")
    currency = raw.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise FinanceReportInvalid("Financial comparison currency invalid")
    try:
        observed = datetime.fromisoformat(raw["fetched_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Missing timezone")
    except (KeyError, AttributeError, ValueError) as exc:
        raise FinanceReportInvalid("Financial comparison timestamp invalid") from exc
    invoice = _money(raw.get("invoice_headers_total"))
    income = _money(raw.get("ledger_income_total"))
    variance = _money(raw.get("variance_ledger_minus_invoices"))
    if variance != income - invoice or ((abs(variance) <= _CENTS) != (status == "aligned_not_certified")):
        raise FinanceReportInvalid("Financial comparison aggregates inconsistent")
    return raw
