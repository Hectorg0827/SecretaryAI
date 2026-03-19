"""
Sales analytics engine.
Computes trends, period-over-period comparisons, and per-account velocity.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
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


def compute_period_sales(
    invoices: list[dict],
    date_from: date,
    date_to: date,
) -> SalesTrend:
    """Compute sales metrics for a given date range."""
    period_days = (date_to - date_from).days or 1
    period_label = f"{date_from.isoformat()} to {date_to.isoformat()}"

    in_period = [
        inv for inv in invoices
        if date_from <= _parse_date(inv.get("order_date", "")) <= date_to
    ]

    total_revenue = Decimal("0")
    order_count = len(in_period)
    product_totals: dict[str, dict] = {}
    account_totals: dict[str, dict] = {}

    for inv in in_period:
        amount = Decimal(str(inv.get("total_amount", 0)))
        total_revenue += amount

        acct = inv.get("customer_name", "Unknown")
        account_totals.setdefault(acct, {"name": acct, "revenue": Decimal("0"), "order_count": 0})
        account_totals[acct]["revenue"] += amount
        account_totals[acct]["order_count"] += 1

        for item in inv.get("items", []):
            name = item.get("name", "Unknown")
            qty = float(item.get("quantity", 0))
            rev = Decimal(str(item.get("amount", 0)))
            product_totals.setdefault(name, {"name": name, "qty": 0.0, "revenue": Decimal("0")})
            product_totals[name]["qty"] += qty
            product_totals[name]["revenue"] += rev

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
    today = today or date.today()
    current_end = today
    current_start = today - timedelta(days=days)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days)

    current = compute_period_sales(invoices, current_start, current_end)
    prior = compute_period_sales(invoices, prior_start, prior_end)

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
    if not account_invoices:
        return {"avg_days_between_orders": None, "recent_trend": "no_data"}

    sorted_dates = sorted(
        [_parse_date(inv.get("order_date", "")) for inv in account_invoices]
    )
    if len(sorted_dates) < 2:
        return {"avg_days_between_orders": None, "recent_trend": "insufficient_data"}

    gaps = [
        (sorted_dates[i + 1] - sorted_dates[i]).days
        for i in range(len(sorted_dates) - 1)
    ]
    avg_gap = sum(gaps) / len(gaps)

    # Recent trend: compare last 2 orders vs previous 2
    recent_amounts = [float(inv.get("total_amount", 0)) for inv in account_invoices[-2:]]
    prior_amounts = [float(inv.get("total_amount", 0)) for inv in account_invoices[-4:-2]]

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
        "total_orders": len(account_invoices),
    }


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return date(2000, 1, 1)
