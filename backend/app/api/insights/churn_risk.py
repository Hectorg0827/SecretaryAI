"""Customers at risk of leaving, from the tenant's complete QuickBooks order history.

Advisory only: ranks active customers with the rule-based screen in
app.intelligence.churn_predictor and suggests a human next step. Nothing is
sent to a customer and no record is changed.
"""
from __future__ import annotations

import logging
from datetime import date
from statistics import mean

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response, read_source, row_value
from app.auth.rbac import require_permission
from app.intelligence.churn_predictor import predict_churn_risk
from app.intelligence.sales_analytics import filter_billable_invoices

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/churn-risk"
TAG = "insights: customers"

# Earlier than any QuickBooks company file, so the invoice query returns the
# source's entire history. The predictor only scores complete histories; a
# 365-day window would make a long-lapsed customer look like "no orders".
HISTORY_START = date(1990, 1, 1)

_RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3, "not_applicable": 4}

NEXT_STEPS = {
    "high": "Call or visit this week: ask how things are going and whether anything has changed.",
    "medium": "Check in soon with a friendly call, a visit or a note about what is new.",
    "low": "No action needed; keep serving them as usual.",
    "unknown": "Not enough order history to judge; review this account yourself.",
    "not_applicable": "No orders yet; treat as a prospect rather than a churn risk.",
}


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _order_row(invoice, today: date) -> dict:
    """Normalize one invoice; the predictor's own checks decide what is a bad row."""
    row = {
        "id": _text(row_value(invoice, "qb_id")) or _text(row_value(invoice, "id")),
        "customer_id": _text(row_value(invoice, "customer_id")),
        "date": row_value(invoice, "date"),
        "status": row_value(invoice, "status") or "",
    }
    if row["customer_id"] is None:
        raise ValueError("Order has no customer")
    if not isinstance(row["status"], str):
        raise ValueError("Order status is invalid")
    # Validates the source id and calendar date (rejects future dates).
    predict_churn_risk("row check", [row], today=today)
    if isinstance(row["date"], str):
        row["date"] = date.fromisoformat(row["date"])
    return row


def _why(risk: str, evidence: str, dates: list[date], today: date) -> str:
    if risk == "not_applicable":
        return "No billed orders on record."
    if evidence == "incomplete_history":
        return "Some of this customer's orders could not be read, so their history is incomplete."
    if risk == "unknown":
        return "Only one order date on record, so there is no usual ordering pattern yet."
    days_since = (today - dates[-1]).days
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
    typical = round(mean(gaps))
    last_year = sum(1 for d in dates if (today - d).days <= 365)
    if risk == "high":
        return f"No order for {days_since} days; they usually order about every {typical} days."
    if risk == "medium":
        return (f"Ordering has slowed: last order {days_since} days ago and "
                f"{last_year} orders in the last 12 months.")
    return f"Ordering normally: last order {days_since} days ago, {last_year} orders in the last 12 months."


@router.get("")
async def churn_risk(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission("view_customers")),
    adapter=Depends(get_adapter),
):
    """Which active customers look like they are drifting away, why, and what to do."""
    today = date.today()
    customers = await read_source(adapter.get_all_customers(), source_name="Customer list")
    invoices = await read_source(
        adapter.get_orders_last_n_days((today - HISTORY_START).days),
        source_name="Order history",
    )

    rows: list[dict] = []
    rows_skipped = 0
    rows_unattributed = 0
    incomplete: set[str] = set()
    for invoice in invoices:
        try:
            rows.append(_order_row(invoice, today))
        except (ValueError, TypeError):
            rows_skipped += 1
            customer_id = _text(row_value(invoice, "customer_id"))
            if customer_id:
                incomplete.add(customer_id)
            else:
                # Cannot tell whose history this gap belongs to; reported below.
                rows_unattributed += 1

    # A repeated source id cannot be trusted for frequency: drop every copy.
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["id"]] = counts.get(row["id"], 0) + 1
    unique = []
    for row in rows:
        if counts[row["id"]] > 1:
            rows_skipped += 1
            incomplete.add(row["customer_id"])
        else:
            unique.append(row)

    by_customer: dict[str, list[dict]] = {}
    for row in filter_billable_invoices(unique):
        by_customer.setdefault(row["customer_id"], []).append({"id": row["id"], "date": row["date"]})

    tally = {risk: 0 for risk in _RISK_ORDER}
    assessed: list[dict] = []
    seen_customers: set[str] = set()
    customers_skipped = 0
    for customer in customers:
        customer_id = _text(row_value(customer, "qb_id"))
        name = _text(row_value(customer, "name"))
        if customer_id is None or name is None or customer_id in seen_customers:
            customers_skipped += 1
            continue
        seen_customers.add(customer_id)
        orders = by_customer.get(customer_id, [])
        complete = customer_id not in incomplete
        result = predict_churn_risk(
            name, orders, today=today, history_complete=complete, source_status="fresh",
        )
        risk = result["risk"]
        dates = sorted(order["date"] for order in orders)
        tally[risk] += 1
        assessed.append({
            "customer_id": customer_id,
            "name": name,
            "risk": risk,
            "score": result["score"],
            "why": _why(risk, result["evidence_status"], dates, today),
            "next_step": NEXT_STEPS[risk],
            "last_order_date": dates[-1].isoformat() if dates else None,
            "orders_last_12_months": sum(1 for d in dates if (today - d).days <= 365),
            "history_complete": complete,
        })

    assessed.sort(key=lambda c: (_RISK_ORDER[c["risk"]], -(c["score"] or 0), c["name"].casefold()))
    at_risk = [c for c in assessed if c["risk"] in ("high", "medium")]
    insufficient = [c for c in assessed if c["risk"] == "unknown"]

    return advisory_response({
        "as_of": today.isoformat(),
        "customers_assessed": len(assessed),
        "counts": tally,
        "at_risk": at_risk[:limit],
        "insufficient_data": insufficient[:limit],
        "truncated": len(at_risk) > limit or len(insufficient) > limit,
        "rows_skipped": rows_skipped,
        "rows_unattributed": rows_unattributed,
        "customers_skipped": customers_skipped,
        "method": (
            "Rule-based screen of how recently and how often each customer orders, "
            "compared with their own past pattern. It is not a calibrated prediction."
        ),
    }, source="connected_data")
