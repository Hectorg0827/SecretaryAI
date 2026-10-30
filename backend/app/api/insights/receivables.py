"""Money customers owe: aging buckets, overdue accounts and who to contact first.

Built on app.intelligence.receivables.age_invoice_balances over the tenant's
QuickBooks invoices. Advisory only: no reminder is sent and no invoice or
payment is touched. Amounts are a source snapshot, not reconciled to payments
or the general ledger.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response, read_source, row_value
from app.auth.rbac import require_permission
from app.intelligence.receivables import age_invoice_balances

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/receivables"
TAG = "insights: finance"

# Earlier than any QuickBooks company file: the invoice query then covers every
# open invoice, which the aging module requires (a recent window would miss
# old unpaid invoices, exactly the ones that matter most).
HISTORY_START = date(1990, 1, 1)
SOURCE_CONNECTION = "quickbooks"
BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "days_91_plus", "unaged")
OVERDUE = ("days_1_30", "days_31_60", "days_61_90", "days_91_plus")
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}

NEXT_STEPS = {
    "high": "Call this week about the invoices more than 60 days overdue, and review further credit until they are settled.",
    "medium": "Send a statement and follow up with a phone call.",
    "low": "Send a friendly payment reminder.",
}


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _customer_balance(customer) -> Decimal | None:
    raw = row_value(customer, "balance")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        balance = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return balance if balance.is_finite() else None


def _invoice_row(invoice, tenant_id: str, currency: str) -> dict:
    source_id = _text(row_value(invoice, "qb_id")) or _text(row_value(invoice, "id"))
    return {
        "tenant_id": tenant_id,
        "source_connection_id": SOURCE_CONNECTION,
        "source_invoice_id": source_id,
        "source_ref": f"quickbooks:invoice:{source_id}" if source_id else None,
        "customer_id": _text(row_value(invoice, "customer_id")),
        "customer_name": _text(row_value(invoice, "customer_name")),
        "date": row_value(invoice, "date"),
        "due_date": row_value(invoice, "due_date"),
        "total": row_value(invoice, "total"),
        "balance": row_value(invoice, "balance"),
        "status": row_value(invoice, "status"),
        "currency": currency,
    }


def _priority(buckets: dict[str, Decimal]) -> str | None:
    if buckets["days_61_90"] or buckets["days_91_plus"]:
        return "high"
    if buckets["days_31_60"]:
        return "medium"
    if buckets["days_1_30"]:
        return "low"
    return None


def _next_step(priority: str | None, buckets: dict[str, Decimal]) -> str:
    if priority:
        return NEXT_STEPS[priority]
    if buckets["unaged"]:
        return "Some invoices have no due date in QuickBooks; check their payment terms."
    return "Nothing overdue yet."


@router.get("")
async def receivables(
    currency: str | None = Query(None, pattern=r"^[A-Z]{3}$"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
):
    """Aged open balances per customer with a suggested collection order.

    QuickBooks invoices do not reach SecretaryAI with a currency code yet, so
    the owner confirms it (``currency``); without it no amount is shown.
    """
    today = date.today()
    tenant_id = user["company_id"]
    base = {"as_of": today.isoformat(), "currency": currency}
    if currency is None:
        return advisory_response({
            **base, "aging_status": "unavailable", "exceptions": ["currency_unverified"],
            "total_owed": None, "overdue_total": None, "buckets": None,
        }, source="connected_data")

    customers = await read_source(adapter.get_all_customers(), source_name="Customer list")
    invoices = await read_source(
        adapter.get_orders_last_n_days((today - HISTORY_START).days),
        source_name="Invoices",
    )
    observed_at = datetime.now(timezone.utc)

    def age(rows: list[dict]) -> dict:
        return age_invoice_balances(
            rows, as_of=today, tenant_id=tenant_id, source_connection_id=SOURCE_CONNECTION,
            source_as_of=today, source_observed_at=observed_at,
            evaluated_at=datetime.now(timezone.utc), source_complete=True,
            covers_all_open_invoices=True, currency_verified=True, currency=currency,
        )

    gate = age([])
    if gate["status"] == "unavailable":
        log.warning("Receivables source gate not met")
        return advisory_response({
            **base, "aging_status": "unavailable", "exceptions": gate["exceptions"],
            "total_owed": None, "overdue_total": None, "buckets": None,
        }, source="connected_data")

    # The aging module's own checks decide which rows are unusable; a customer
    # with any unusable or duplicated invoice is listed as "not aged" rather
    # than shown with an understated balance.
    valid: list[dict] = []
    rows_skipped = 0
    rows_unattributed = 0
    incomplete: set[str] = set()
    invoice_names: dict[str, str] = {}
    for invoice in invoices:
        row = _invoice_row(invoice, tenant_id, currency)
        if row["customer_id"] and row["customer_name"]:
            invoice_names.setdefault(row["customer_id"], row["customer_name"])
        try:
            age([row])
        except (ValueError, TypeError):
            rows_skipped += 1
            if row["customer_id"]:
                incomplete.add(row["customer_id"])
            else:
                # Belongs to no known customer, so the totals may be understated.
                rows_unattributed += 1
            continue
        valid.append(row)

    counts: dict[str, int] = {}
    for row in valid:
        counts[row["source_invoice_id"]] = counts.get(row["source_invoice_id"], 0) + 1
    for row in valid:
        if counts[row["source_invoice_id"]] > 1:
            rows_skipped += 1
            incomplete.add(row["customer_id"])
    aged_rows = [row for row in valid if row["customer_id"] not in incomplete]
    report = age(aged_rows)

    names: dict[str, str] = {}
    qb_balances: dict[str, Decimal | None] = {}
    for customer in customers:
        customer_id = _text(row_value(customer, "qb_id"))
        if customer_id:
            names[customer_id] = _text(row_value(customer, "name")) or customer_id
            qb_balances[customer_id] = _customer_balance(customer)
    for customer_id, name in invoice_names.items():
        names.setdefault(customer_id, name)

    due_dates = {row["source_invoice_id"]: row["due_date"] for row in aged_rows}
    per_customer: dict[str, dict] = {}
    for item in report["contributions"]:
        entry = per_customer.setdefault(item["customer_id"], {
            "buckets": {bucket: Decimal("0") for bucket in BUCKETS},
            "open_invoices": 0, "oldest_days_overdue": 0,
        })
        entry["buckets"][item["bucket"]] += item["balance"]
        entry["open_invoices"] += 1
        if item["bucket"] in OVERDUE:
            days = (today - _as_date(due_dates[item["source_invoice_id"]])).days
            entry["oldest_days_overdue"] = max(entry["oldest_days_overdue"], days)

    accounts = []
    for customer_id, entry in per_customer.items():
        buckets = entry["buckets"]
        owed = sum(buckets.values(), Decimal("0"))
        overdue = sum((buckets[b] for b in OVERDUE), Decimal("0"))
        priority = _priority(buckets)
        qb_balance = qb_balances.get(customer_id)
        accounts.append({
            "customer_id": customer_id,
            "name": names.get(customer_id, customer_id),
            "total_owed": _money(owed),
            "overdue": _money(overdue),
            "buckets": {b: _money(v) for b, v in buckets.items()},
            "open_invoices": entry["open_invoices"],
            "oldest_days_overdue": entry["oldest_days_overdue"] or None,
            "priority": priority,
            "next_step": _next_step(priority, buckets),
            "quickbooks_balance": _money(qb_balance),
            # A mismatch usually means credits or unapplied payments in QuickBooks.
            "balance_matches_quickbooks": None if qb_balance is None else qb_balance == owed,
        })
    accounts.sort(key=lambda a: (
        _PRIORITY_ORDER[a["priority"]], -a["overdue"], -a["total_owed"], a["name"].casefold(),
    ))

    not_aged = [{
        "customer_id": customer_id,
        "name": names.get(customer_id, customer_id),
        "quickbooks_balance": _money(qb_balances.get(customer_id)),
        "reason": "Some of this customer's invoices could not be read, so their balance was not aged.",
    } for customer_id in sorted(incomplete)]
    for customer_id, balance in qb_balances.items():
        if balance and balance > 0 and customer_id not in per_customer and customer_id not in incomplete:
            not_aged.append({
                "customer_id": customer_id,
                "name": names[customer_id],
                "quickbooks_balance": _money(balance),
                "reason": "QuickBooks shows a balance but no open invoices were found to age.",
            })
    not_aged.sort(key=lambda c: c["name"].casefold())

    buckets = report["buckets"]
    return advisory_response({
        **base,
        "aging_status": report["status"],
        "exceptions": report["exceptions"],
        "total_owed": _money(report["total_open"]),
        "overdue_total": _money(sum((buckets[b] for b in OVERDUE), Decimal("0"))),
        "buckets": {b: _money(v) for b, v in buckets.items()},
        "open_invoice_count": report["invoice_count"],
        "accounts_total": len(accounts),
        "accounts": accounts[:limit],
        "not_aged": not_aged[:limit],
        "truncated": len(accounts) > limit or len(not_aged) > limit,
        "rows_skipped": rows_skipped,
        "rows_unattributed": rows_unattributed,
        "reconciliation_status": report["reconciliation_status"],
    }, source="connected_data")
