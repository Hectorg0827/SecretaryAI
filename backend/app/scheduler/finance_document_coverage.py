"""Read-only QBO sandbox AR/AP document coverage for Lola's executive digest.

Only counts are computed. Monetary totals, currency, vendor/customer identity,
payment matching and ledger reconciliation remain intentionally unassessed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

KINDS = ("ar", "ap")
MAX_HEADERS = 5000


class FinanceDocumentCoverageInvalid(ValueError):
    """A cached document summary cannot safely be shown to management."""


def unavailable_source(kind: str) -> dict:
    if kind not in KINDS:
        raise ValueError("Unknown document kind")
    return {
        "status": "unavailable", "source_quality": "unavailable",
        "reason": f"{kind}_source_unavailable", "headers_examined": None,
        "open_count": None, "overdue_count": None, "due_date_missing_count": None,
    }


def _field(row, key: str):
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def _source_counts(rows: list, *, kind: str, as_of: date) -> dict:
    if not isinstance(rows, list) or len(rows) > MAX_HEADERS:
        raise FinanceDocumentCoverageInvalid("Finance document read is not bounded")
    seen: set[str] = set()
    open_count = overdue = due_missing = 0
    for row in rows:
        source_id = _field(row, "id") or _field(row, "qb_id")
        if not isinstance(source_id, str) or not source_id or source_id in seen:
            raise FinanceDocumentCoverageInvalid("Invalid or duplicate finance document")
        seen.add(source_id)
        raw = _field(row, "balance")
        if raw is None or isinstance(raw, bool):
            raise FinanceDocumentCoverageInvalid("Unobserved finance document balance")
        try:
            balance = Decimal(str(raw))
        except (TypeError, InvalidOperation, ValueError) as exc:
            raise FinanceDocumentCoverageInvalid("Invalid finance document balance") from exc
        if not balance.is_finite() or balance < 0:
            raise FinanceDocumentCoverageInvalid("Invalid finance document balance")
        due = _field(row, "due_date")
        if due is not None and (not isinstance(due, date) or isinstance(due, datetime)):
            raise FinanceDocumentCoverageInvalid("Invalid finance document due date")
        if balance > 0:
            open_count += 1
            if due is None:
                due_missing += 1
            elif due < as_of:
                overdue += 1
    return {
        "status": "observed", "source_quality": "partial", "reason": None,
        "headers_examined": len(rows), "open_count": open_count,
        "overdue_count": overdue, "due_date_missing_count": due_missing,
    }


def validate_finance_document_coverage(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "as_of", "ar", "ap", "currency_verified", "ledger_reconciled",
    }:
        raise FinanceDocumentCoverageInvalid("Finance document coverage shape invalid")
    try:
        as_of = date.fromisoformat(value["as_of"])
    except (TypeError, ValueError) as exc:
        raise FinanceDocumentCoverageInvalid("Finance coverage date invalid") from exc
    if as_of.isoformat() != value["as_of"]:
        raise FinanceDocumentCoverageInvalid("Finance coverage date noncanonical")
    if value["currency_verified"] is not False or value["ledger_reconciled"] is not False:
        raise FinanceDocumentCoverageInvalid("Finance coverage cannot certify money")
    required = {
        "status", "source_quality", "reason", "headers_examined",
        "open_count", "overdue_count", "due_date_missing_count",
    }
    for kind in KINDS:
        source = value[kind]
        if not isinstance(source, dict) or set(source) != required:
            raise FinanceDocumentCoverageInvalid("Finance source schema invalid")
        if source["status"] == "unavailable":
            if source != unavailable_source(kind):
                raise FinanceDocumentCoverageInvalid("Unavailable finance source is ambiguous")
            continue
        if (source["status"] != "observed" or source["source_quality"] != "partial"
                or source["reason"] is not None):
            raise FinanceDocumentCoverageInvalid("Finance source quality invalid")
        fields = ("headers_examined", "open_count", "overdue_count", "due_date_missing_count")
        if any(type(source[field]) is not int or not 0 <= source[field] <= MAX_HEADERS for field in fields):
            raise FinanceDocumentCoverageInvalid("Finance document counts invalid")
        if (source["open_count"] > source["headers_examined"]
                or source["overdue_count"] + source["due_date_missing_count"] > source["open_count"]):
            raise FinanceDocumentCoverageInvalid("Finance document counts inconsistent")
    return value


async def read_finance_document_coverage(adapter, *, as_of: date | None = None) -> dict:
    """Independent AR/AP reads: one failed source does not fake a zero for either."""
    observed_day = as_of or datetime.now(timezone.utc).date()
    if not isinstance(observed_day, date) or isinstance(observed_day, datetime):
        raise ValueError("Finance coverage requires a date")
    result = {
        "as_of": observed_day.isoformat(), "currency_verified": False,
        "ledger_reconciled": False,
    }
    for kind in KINDS:
        try:
            rows = await adapter.get_qbo_finance_document_headers_read_only(kind)
            result[kind] = _source_counts(rows, kind=kind, as_of=observed_day)
        except Exception:
            # No provider body, QBO document ID or financial amount in logs.
            result[kind] = unavailable_source(kind)
    return validate_finance_document_coverage(result)
