"""Read-only invoice-balance aging, never a substitute for the AR ledger.

This is an *internal pure calculation*, not a shared persisted contract. The
caller must pass tenant-checked, source-tagged invoice projections and attest
source coverage. No provider client, database client or payment mutation is
invoked here. Payment allocations and GL balances are not yet available from
the QuickBooks read adapters, so even a complete snapshot is *unreconciled*.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence


_BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "days_91_plus", "unaged")
_NOT_BILLED = frozenset({"void", "voided", "cancelled", "canceled", "deleted", "draft"})
_PAID = frozenset({"paid"})
_OPEN = frozenset({"open", "partially_paid", "partially paid", "overdue"})


def _money(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Invoice {name} is missing or invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invoice {name} is missing or invalid") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"Invoice {name} is missing or invalid")
    return result


def _date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"Invoice {field} must be a date")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invoice {field} is missing or invalid") from exc


def _bucket(due: date, as_of: date) -> str:
    overdue = (as_of - due).days
    if overdue <= 0:
        return "current"
    if overdue <= 30:
        return "days_1_30"
    if overdue <= 60:
        return "days_31_60"
    if overdue <= 90:
        return "days_61_90"
    return "days_91_plus"


def age_invoice_balances(
    invoices: Sequence[Mapping[str, object]],
    *,
    as_of: date,
    tenant_id: str,
    source_connection_id: str,
    source_as_of: date,
    source_observed_at: datetime,
    evaluated_at: datetime,
    source_complete: bool,
    covers_all_open_invoices: bool,
    currency_verified: bool,
    currency: str | None,
    max_source_age: timedelta = timedelta(hours=24),
) -> dict:
    """Age observed balances with explicit coverage, currency and tenant gates.

    `source_complete` means the fetch ended successfully; `covers_all_open_invoices`
    additionally means historical open balances were included (a date-limited
    `get_invoices` query does *not* establish this). Both are necessary even for
    an empty list. `source_as_of` is the source balance date, never inferred from
    the issue date; historical balances cannot be reconstructed from today's
    connector output.

    Money stays Decimal. The returned `total_open` is a source snapshot sum,
    *not* GL-reconciled AR, booked revenue or a cash-flow forecast.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise PermissionError("Tenant context required for AR aging")
    if not isinstance(source_connection_id, str) or not source_connection_id.strip():
        raise PermissionError("Source connection context required for AR aging")

    # Check all row scopes before presenting even a partial source outcome.
    for invoice in invoices:
        if not isinstance(invoice, Mapping):
            raise ValueError("Invoice observation must be a mapping")
        if (invoice.get("tenant_id") != tenant_id
                or invoice.get("source_connection_id") != source_connection_id):
            raise PermissionError("Invoice tenant or source connection is not authorized for this snapshot")

    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError("as_of must be a business date")
    if not isinstance(source_as_of, date) or isinstance(source_as_of, datetime):
        raise ValueError("source_as_of must be a business date")
    for name, timestamp in (("source_observed_at", source_observed_at), ("evaluated_at", evaluated_at)):
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if not isinstance(max_source_age, timedelta) or max_source_age <= timedelta(0):
        raise ValueError("max_source_age must be positive")

    age = evaluated_at.astimezone(timezone.utc) - source_observed_at.astimezone(timezone.utc)
    if age < timedelta(0):
        raise ValueError("Source observation is from the future")

    exceptions: list[str] = []
    if not source_complete:
        exceptions.append("source_incomplete")
    if not covers_all_open_invoices:
        exceptions.append("history_not_exhaustive")
    if source_as_of != as_of:
        exceptions.append("snapshot_date_mismatch")
    if age > max_source_age:
        exceptions.append("source_stale")
    if not currency_verified or not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        exceptions.append("currency_unverified")

    result = {
        "status": "unavailable", "as_of": as_of.isoformat(),
        "tenant_id": tenant_id, "source_connection_id": source_connection_id,
        "source_as_of": source_as_of.isoformat(),
        "source_observed_at": source_observed_at.isoformat(),
        "currency": currency if "currency_unverified" not in exceptions else None,
        "total_open": None, "buckets": None, "invoice_count": None,
        "excluded_count": None, "contributions": [],
        "exceptions": exceptions,
        "reconciliation_status": "not_reconciled_to_payments_or_gl",
    }
    if exceptions:
        return result

    buckets = {label: Decimal("0") for label in _BUCKETS}
    seen: set[str] = set()
    contributions: list[dict] = []
    excluded = 0

    for invoice in invoices:
        invoice_id = invoice.get("source_invoice_id")
        customer_id = invoice.get("customer_id")
        source_ref = invoice.get("source_ref")
        for field, value in (("source_invoice_id", invoice_id), ("customer_id", customer_id), ("source_ref", source_ref)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invoice missing {field}")
        if invoice_id in seen:
            raise ValueError("AR source has duplicate invoice id")
        seen.add(invoice_id)
        if invoice.get("currency") != currency:
            raise ValueError("Invoice currency does not match confirmed source currency")
        issued_at = _date(invoice.get("date"), "date")
        if issued_at > as_of:
            raise ValueError("Invoice date is in the future relative to snapshot")
        total = _money(invoice.get("total"), "total")
        balance = _money(invoice.get("balance"), "balance")
        if balance > total:
            raise ValueError("Invoice balance exceeds total")

        status = str(invoice.get("status") or "").strip().lower()
        if status not in (_OPEN | _PAID | _NOT_BILLED):
            raise ValueError("Invoice status is unknown")
        if status in (_PAID | _NOT_BILLED):
            if balance != 0:
                raise ValueError("Invoice status conflicts with open balance")
            excluded += 1
            continue
        if balance == 0:
            continue

        due_raw = invoice.get("due_date")
        if due_raw is None:
            bucket = "unaged"
            if "missing_due_date" not in result["exceptions"]:
                result["exceptions"].append("missing_due_date")
        else:
            bucket = _bucket(_date(due_raw, "due_date"), as_of)
        buckets[bucket] += balance
        contributions.append({
            "source_invoice_id": invoice_id, "source_ref": source_ref,
            "customer_id": customer_id, "bucket": bucket, "balance": balance,
        })

    contributions.sort(key=lambda entry: entry["source_invoice_id"])
    result.update({
        "status": "partial_aging" if result["exceptions"] else "available_unreconciled",
        "total_open": sum(buckets.values(), Decimal("0")),
        "buckets": buckets, "invoice_count": len(contributions),
        "excluded_count": excluded, "contributions": contributions,
    })
    return result
