"""Unusual changes in orders, revenue and customer ordering behaviour.

Built on app.intelligence.anomaly_detection.detect_order_anomalies over the
tenant's recent QuickBooks invoices: each customer's order sizes are compared
with that customer's own recent orders, and weekly revenue and order counts
with the other weeks in the window. Advisory only; nothing is changed.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response, read_source, row_value
from app.auth.rbac import require_permission
from app.intelligence.anomaly_detection import detect_order_anomalies
from app.intelligence.sales_analytics import filter_billable_invoices

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/anomalies"
TAG = "insights: operations"

# The detector needs at least four points per series.
MIN_POINTS = 4
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

SUGGESTED_CHECKS = {
    ("customer_order", "high"): "Confirm quantities and prices before shipping, and check stock and credit for an order this size.",
    ("customer_order", "low"): "Check whether the order was keyed correctly or is only part of what they usually buy.",
    ("weekly_revenue", "high"): "Find which orders drove the jump and whether it is a one-off or a new trend.",
    ("weekly_revenue", "low"): "Check for missing or delayed invoices and which regular customers did not order that week.",
    ("weekly_orders", "high"): "Make sure stock and the warehouse can keep up if this continues.",
    ("weekly_orders", "low"): "Look at which regular customers did not order that week.",
}


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _order_row(invoice, today: date) -> dict:
    """Normalize one invoice or raise ValueError; bad rows are skipped and counted."""
    source_id = _text(row_value(invoice, "qb_id")) or _text(row_value(invoice, "id"))
    customer_id = _text(row_value(invoice, "customer_id"))
    if source_id is None or customer_id is None:
        raise ValueError("Order is missing its source or customer id")
    raw_date = row_value(invoice, "date")
    if raw_date is None:
        raw_date = row_value(invoice, "order_date")
    if isinstance(raw_date, datetime):
        raise ValueError("Order date must be a calendar date")
    order_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
    if order_date > today:
        raise ValueError("Order date is in the future")
    raw_total = row_value(invoice, "total")
    if raw_total is None:
        raw_total = row_value(invoice, "total_amount")
    if raw_total is None or isinstance(raw_total, bool):
        raise ValueError("Order total is missing")
    try:
        total = Decimal(str(raw_total))
    except InvalidOperation as exc:
        raise ValueError("Order total is invalid") from exc
    if not total.is_finite() or total < 0:
        raise ValueError("Order total is invalid")
    status = row_value(invoice, "status") or ""
    if not isinstance(status, str):
        raise ValueError("Order status is invalid")
    return {
        "id": source_id, "customer_id": customer_id,
        "customer_name": _text(row_value(invoice, "customer_name")),
        "date": order_date, "total": total, "status": status,
    }


def _flagged(series: list[dict], lookback_days: int, today: date) -> dict[float, object]:
    """Map each flagged value to its anomaly; a z-score depends only on the value."""
    return {
        anomaly.value: anomaly
        for anomaly in detect_order_anomalies(series, lookback_days=lookback_days, today=today)
    }


def _entry(kind: str, anomaly, value: float, **details) -> dict:
    direction = "high" if anomaly.anomaly_type.endswith("_high") else "low"
    low, high = anomaly.expected_range
    return {
        "kind": kind,
        "severity": anomaly.severity,
        "direction": direction,
        "value": value,
        "usual_low": max(0.0, low),
        "usual_high": high,
        "suggested_check": SUGGESTED_CHECKS[(kind, direction)],
        **details,
    }


@router.get("")
async def anomalies(
    lookback_days: int = Query(90, ge=28, le=365),
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
):
    """Orders, weeks and customers that stand out from their own recent pattern."""
    today = date.today()
    window_start = today - timedelta(days=lookback_days)
    invoices = await read_source(
        adapter.get_orders_last_n_days(lookback_days), source_name="Order history",
    )

    rows: list[dict] = []
    rows_skipped = 0
    incomplete: set[str] = set()
    for invoice in invoices:
        try:
            row = _order_row(invoice, today)
        except (ValueError, TypeError):
            rows_skipped += 1
            customer_id = _text(row_value(invoice, "customer_id"))
            if customer_id:
                incomplete.add(customer_id)
            continue
        if row["date"] >= window_start:
            rows.append(row)

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
    billed = filter_billable_invoices(unique)

    found: list[dict] = []

    # Customer behaviour: each order against the same customer's recent orders.
    # Same-named customers get distinct labels so their orders never merge.
    ids_by_name: dict[str, set[str]] = {}
    for row in billed:
        name = row["customer_name"] or f"Customer {row['customer_id']}"
        ids_by_name.setdefault(name, set()).add(row["customer_id"])
    by_label: dict[str, list[dict]] = {}
    for row in billed:
        if row["customer_id"] in incomplete:
            continue
        name = row["customer_name"] or f"Customer {row['customer_id']}"
        label = name if len(ids_by_name[name]) == 1 else f"{name} ({row['customer_id']})"
        by_label.setdefault(label, []).append(row)
    customers_checked = 0
    for label, orders in by_label.items():
        if len(orders) < MIN_POINTS:
            continue
        customers_checked += 1
        series = [
            {"customer_name": label, "total_amount": float(o["total"]), "order_date": o["date"].isoformat()}
            for o in orders
        ]
        flagged = _flagged(series, lookback_days, today)
        for order in orders:
            anomaly = flagged.get(float(order["total"]))
            if anomaly is not None:
                found.append(_entry(
                    "customer_order", anomaly, float(order["total"]),
                    customer_id=order["customer_id"], customer=label,
                    order_id=order["id"], date=order["date"].isoformat(),
                ))

    # Revenue and order volume: each complete week (today excluded) against the
    # other weeks. A skipped row would understate its week, so any skip
    # withholds this check instead of reporting a false drop.
    weeks = lookback_days // 7
    weekly_trends = "withheld" if rows_skipped else "checked"
    if not rows_skipped:
        revenue = [Decimal("0")] * weeks
        volume = [0] * weeks
        for row in billed:
            index = (today - timedelta(days=1) - row["date"]).days // 7
            if 0 <= index < weeks:
                revenue[index] += row["total"]
                volume[index] += 1
        week_ends = [today - timedelta(days=1 + 7 * i) for i in range(weeks)]
        for kind, values in (("weekly_revenue", [float(v) for v in revenue]),
                             ("weekly_orders", [float(v) for v in volume])):
            series = [
                {"customer_name": kind, "total_amount": value, "order_date": end.isoformat()}
                for value, end in zip(values, week_ends)
            ]
            flagged = _flagged(series, lookback_days, today)
            for value, end in zip(values, week_ends):
                anomaly = flagged.get(value)
                if anomaly is not None:
                    found.append(_entry(
                        kind, anomaly, value,
                        period_start=(end - timedelta(days=6)).isoformat(), period_end=end.isoformat(),
                    ))

    # Most serious first; within a severity and kind, most recent first.
    found.sort(key=lambda a: a.get("date") or a.get("period_end") or "", reverse=True)
    found.sort(key=lambda a: (_SEVERITY_ORDER.get(a["severity"], 3), a["kind"]))

    return advisory_response({
        "as_of": today.isoformat(),
        "lookback_days": lookback_days,
        "weeks_compared": weeks,
        "anomalies": found,
        "counts": {
            "high": sum(1 for a in found if a["severity"] == "high"),
            "medium": sum(1 for a in found if a["severity"] == "medium"),
        },
        "orders_analyzed": len(billed),
        "customers_checked": customers_checked,
        "customers_with_too_few_orders": sum(1 for o in by_label.values() if len(o) < MIN_POINTS),
        "weekly_trends": weekly_trends,
        "rows_skipped": rows_skipped,
    }, source="connected_data")
