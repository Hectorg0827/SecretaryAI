"""
Sales analytics engine.
Computes trends, period-over-period comparisons, and per-account velocity.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import math
from typing import Optional


@dataclass
class SalesTrend:
    period_label: str         # e.g., "Last 30 days"
    total_revenue: Decimal
    order_count: int
    avg_order_value: Decimal
    top_products: list[dict]  # [{name, qty, revenue}]
    top_accounts: list[dict]  # [{name, revenue, order_count}]
    vs_prior_period_pct: Optional[float]  # % change vs same period prior


_EXCLUDED_INVOICE_STATUSES = frozenset({"void", "voided", "cancelled", "canceled", "deleted", "draft"})


def _active_invoices(invoices: list[dict]) -> list[dict]:
    """Reject duplicated provider identity and omit records that were never billed."""
    seen_ids: set[str] = set()
    active = []
    for invoice in invoices:
        source_id = invoice.get("qb_id") or invoice.get("id")
        if source_id:
            identity = str(source_id)
            if identity in seen_ids:
                raise ValueError("Sales source has duplicate invoice id")
            seen_ids.add(identity)
        status = str(invoice.get("status") or "").strip().casefold()
        if status not in _EXCLUDED_INVOICE_STATUSES:
            active.append(invoice)
    return active


def _amount(value, *, label: str) -> Decimal:
    try:
        if value is None:
            raise ValueError("missing amount")
        amount = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValueError(f"{label} has missing or invalid amount") from exc
    if not amount.is_finite():
        raise ValueError(f"{label} has non-finite amount")
    return amount


def compute_period_sales(
    invoices: list[dict],
    date_from: date,
    date_to: date,
) -> SalesTrend:
    """Compute sales metrics for a given date range."""
    period_label = f"{date_from.isoformat()} to {date_to.isoformat()}"

    in_period = [
        inv for inv in _active_invoices(invoices)
        if date_from <= _parse_date(inv.get("order_date", "")) <= date_to
    ]

    total_revenue = Decimal("0")
    order_count = len(in_period)
    product_totals: dict[str, dict] = {}
    account_totals: dict[str, dict] = {}

    for inv in in_period:
        if inv.get("total_amount") is None:
            raise ValueError("Sales row has missing total_amount")
        amount = _amount(inv["total_amount"], label="Sales row total_amount")
        total_revenue += amount

        acct = inv.get("customer_name") or "Unknown"
        customer_id = inv.get("customer_id") or inv.get("account_id")
        account_key = f"id:{customer_id}" if customer_id else f"name:{acct}"
        account_totals.setdefault(account_key, {"name": acct, "revenue": Decimal("0"), "order_count": 0})
        account_totals[account_key]["revenue"] += amount
        account_totals[account_key]["order_count"] += 1

        items = inv.get("items", [])
        if not isinstance(items, list):
            raise ValueError("Sales line items must be a list")
        for item in items:
            if not isinstance(item, dict) or item.get("quantity") is None or item.get("amount") is None:
                raise ValueError("Sales line has missing quantity or amount")
            name = item.get("name") or "Unknown"
            try:
                qty = float(item["quantity"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Sales line has invalid quantity") from exc
            if not math.isfinite(qty):
                raise ValueError("Sales line has non-finite quantity")
            rev = _amount(item["amount"], label="Sales line")
            item_id = item.get("item_id") or item.get("sku")
            product_key = f"id:{item_id}" if item_id else f"name:{name}"
            product_totals.setdefault(product_key, {"name": name, "qty": 0.0, "revenue": Decimal("0")})
            product_totals[product_key]["qty"] += qty
            product_totals[product_key]["revenue"] += rev

    avg_order_value = (total_revenue / order_count) if order_count else Decimal("0")

    top_products = sorted(
        [{"name": v["name"], "qty": v["qty"], "revenue": float(v["revenue"])}
         for v in product_totals.values()],
        key=lambda x: x["revenue"], reverse=True
    )[:10]

    top_accounts = sorted(
        [{"name": v["name"], "revenue": float(v["revenue"]), "order_count": v["order_count"]}
         for v in account_totals.values()],
        key=lambda x: x["revenue"], reverse=True
    )[:10]

    return SalesTrend(
        period_label=period_label,
        total_revenue=total_revenue,
        order_count=order_count,
        avg_order_value=avg_order_value,
        top_products=top_products,
        top_accounts=top_accounts,
        vs_prior_period_pct=None,  # Populated separately with compute_trend_comparison
    )


def compute_trend_comparison(
    invoices: list[dict],
    days: int = 30,
    today: Optional[date] = None,
) -> dict:
    """Compare current period vs same prior period."""
    if days <= 0:
        raise ValueError("days must be positive")
    today = today or date.today()
    current_end = today
    current_start = today - timedelta(days=days - 1)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)

    valid_invoices = _active_invoices(invoices)
    current = compute_period_sales(valid_invoices, current_start, current_end)
    prior = compute_period_sales(valid_invoices, prior_start, prior_end)

    if prior.total_revenue > 0:
        pct_change = float(
            (current.total_revenue - prior.total_revenue) / prior.total_revenue * 100
        )
    else:
        pct_change = None

    return {
        "current": {
            "revenue": float(current.total_revenue),
            "orders": current.order_count,
            "avg_order": float(current.avg_order_value),
            "top_products": current.top_products,
            "top_accounts": current.top_accounts,
        },
        "prior": {
            "revenue": float(prior.total_revenue),
            "orders": prior.order_count,
        },
        "change_pct": round(pct_change, 1) if pct_change is not None else None,
        "trend": "up" if (pct_change or 0) > 0 else "down" if (pct_change or 0) < 0 else "flat",
    }


def compute_account_velocity(account_invoices: list[dict]) -> dict:
    """
    Compute order velocity (frequency + recency) for a single account.
    Returns metrics useful for health scoring.
    """
    valid_invoices = _active_invoices(account_invoices)
    if not valid_invoices:
        return {"avg_days_between_orders": None, "recent_trend": "no_data"}

    chronological = sorted(valid_invoices, key=lambda inv: _parse_date(inv.get("order_date", "")))
    sorted_dates = [_parse_date(inv.get("order_date", "")) for inv in chronological]
    if len(sorted_dates) < 2:
        return {"avg_days_between_orders": None, "recent_trend": "insufficient_data"}

    gaps = [
        (sorted_dates[i + 1] - sorted_dates[i]).days
        for i in range(len(sorted_dates) - 1)
    ]
    avg_gap = sum(gaps) / len(gaps)

    # Recent trend: compare last 2 orders vs previous 2
    amounts = [float(_amount(inv.get("total_amount"), label="Sales row total_amount")) for inv in chronological]
    recent_amounts = amounts[-2:]
    prior_amounts = amounts[-4:-2]

    recent_avg = sum(recent_amounts) / len(recent_amounts) if recent_amounts else 0
    prior_avg = sum(prior_amounts) / len(prior_amounts) if prior_amounts else recent_avg

    if prior_avg and recent_avg < prior_avg * 0.85:
        trend = "declining"
    elif prior_avg and recent_avg > prior_avg * 1.15:
        trend = "growing"
    else:
        trend = "stable"

    return {
        "avg_days_between_orders": round(avg_gap, 1),
        "recent_trend": trend,
        "last_order_date": sorted_dates[-1].isoformat(),
        "total_orders": len(valid_invoices),
    }


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError("Sales row has missing or invalid order_date") from exc
