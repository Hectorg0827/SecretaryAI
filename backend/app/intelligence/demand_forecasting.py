"""
Demand forecasting — simple moving-average based projections.
Used to predict stock-out dates and recommend reorder timing.
No ML models required — rule-based + moving averages work well enough
for small importers with predictable ordering patterns.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional


@dataclass
class DemandForecast:
    product_name: str
    current_qty: float
    weekly_sell_rate_avg: float      # 4-week moving average
    weekly_sell_rate_trend: float    # positive = accelerating, negative = slowing
    projected_stockout_date: Optional[date]
    recommended_reorder_date: Optional[date]  # reorder_date = stockout - lead_time_weeks
    recommended_order_qty: float     # enough for 8 weeks at current rate
    confidence: str                  # "high" | "medium" | "low"


DEFAULT_LEAD_TIME_WEEKS = 6   # Time from PO to delivery (importer's typical lead)
DEFAULT_REORDER_WEEKS = 8     # How many weeks of stock to order at a time


def forecast_product(
    product_name: str,
    current_qty: float,
    weekly_sell_history: list[float],  # oldest first, each entry = units sold that week
    lead_time_weeks: int = DEFAULT_LEAD_TIME_WEEKS,
    target_weeks_stock: int = DEFAULT_REORDER_WEEKS,
    today: Optional[date] = None,
) -> DemandForecast:
    """
    Forecast demand and reorder timing for a single product.

    weekly_sell_history: list of weekly quantities sold (most recent last).
    Needs at least 4 weeks for a useful forecast.
    """
    today = today or date.today()

    if not weekly_sell_history:
        return DemandForecast(
            product_name=product_name,
            current_qty=current_qty,
            weekly_sell_rate_avg=0.0,
            weekly_sell_rate_trend=0.0,
            projected_stockout_date=None,
            recommended_reorder_date=None,
            recommended_order_qty=0.0,
            confidence="low",
        )

    # 4-week moving average
    recent = weekly_sell_history[-4:]
    avg_rate = sum(recent) / len(recent)

    # Trend: compare last 2 weeks vs prior 2 weeks
    if len(weekly_sell_history) >= 4:
        last_2 = sum(weekly_sell_history[-2:]) / 2
        prior_2 = sum(weekly_sell_history[-4:-2]) / 2
        trend = last_2 - prior_2
    else:
        trend = 0.0

    confidence = "high" if len(weekly_sell_history) >= 8 else "medium" if len(weekly_sell_history) >= 4 else "low"

    # Projected stock-out
    if avg_rate > 0:
        weeks_until_out = current_qty / avg_rate
        stockout_date = today + timedelta(weeks=weeks_until_out)
        reorder_date = stockout_date - timedelta(weeks=lead_time_weeks)
        order_qty = avg_rate * target_weeks_stock
    else:
        stockout_date = None
        reorder_date = None
        order_qty = 0.0

    return DemandForecast(
        product_name=product_name,
        current_qty=current_qty,
        weekly_sell_rate_avg=round(avg_rate, 2),
        weekly_sell_rate_trend=round(trend, 2),
        projected_stockout_date=stockout_date,
        recommended_reorder_date=reorder_date,
        recommended_order_qty=round(order_qty, 0),
        confidence=confidence,
    )


def build_sell_rate_history(
    product_name_lower: str,
    invoices: list[dict],
    weeks: int = 12,
    today: Optional[date] = None,
) -> list[float]:
    """
    Build a weekly sell-rate history array from invoice line items.
    Returns list of floats, oldest week first.
    """
    today = today or date.today()
    weekly_totals: dict[int, float] = {w: 0.0 for w in range(weeks)}

    for inv in invoices:
        inv_date = _parse_date(inv.get("order_date", ""))
        delta_days = (today - inv_date).days
        if delta_days < 0 or delta_days > weeks * 7:
            continue

        week_idx = weeks - 1 - (delta_days // 7)

        for item in inv.get("items", []):
            name = str(item.get("name", "")).lower()
            if product_name_lower in name or name in product_name_lower:
                qty = float(item.get("quantity", 0))
                weekly_totals[week_idx] = weekly_totals.get(week_idx, 0) + qty

    return [weekly_totals[w] for w in range(weeks)]


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return date(2000, 1, 1)
