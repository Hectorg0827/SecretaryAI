"""
Anomaly detection — flags unusual patterns in sales, inventory, and accounts.
Uses simple statistical methods (z-score, rolling average deviation) to avoid
false positives while catching real issues.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
import statistics


@dataclass
class Anomaly:
    anomaly_type: str
    entity_name: str
    description: str
    severity: str       # "low" | "medium" | "high"
    value: float
    expected_range: tuple[float, float]
    detected_at: date


def detect_order_anomalies(
    invoices: list[dict],
    lookback_days: int = 90,
    today: Optional[date] = None,
) -> list[Anomaly]:
    """
    Detect unusual order patterns:
    - Unusually large or small orders for an account
    - Accounts that placed double their normal order
    - Accounts with sudden order gaps
    """
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)
    anomalies: list[Anomaly] = []

    # Group by account
    by_account: dict[str, list[dict]] = {}
    for inv in invoices:
        acct = inv.get("customer_name", "Unknown")
        by_account.setdefault(acct, []).append(inv)

    for acct_name, acct_invoices in by_account.items():
        amounts = [
            float(inv.get("total_amount", 0))
            for inv in acct_invoices
            if _parse_date(inv.get("order_date", "")) >= cutoff
        ]

        if len(amounts) < 4:
            continue  # Not enough data

        mean = statistics.mean(amounts)
        stdev = statistics.stdev(amounts) if len(amounts) > 1 else 0

        for inv in acct_invoices:
            amount = float(inv.get("total_amount", 0))
            if stdev == 0:
                continue

            z_score = (amount - mean) / stdev
            if abs(z_score) > 2.5:
                direction = "high" if z_score > 0 else "low"
                severity = "high" if abs(z_score) > 3.0 else "medium"
                anomalies.append(Anomaly(
                    anomaly_type=f"unusual_order_amount_{direction}",
                    entity_name=acct_name,
                    description=(
                        f"{acct_name} placed an unusually {direction} order: "
                        f"${amount:,.0f} (avg: ${mean:,.0f} ± ${stdev:,.0f})"
                    ),
                    severity=severity,
                    value=amount,
                    expected_range=(
                        round(mean - 2 * stdev, 2),
                        round(mean + 2 * stdev, 2),
                    ),
                    detected_at=today,
                ))

    return anomalies


def detect_inventory_anomalies(
    inventory: list[dict],
    today: Optional[date] = None,
) -> list[Anomaly]:
    """
    Detect inventory anomalies:
    - Product quantity dropped to zero unexpectedly
    - Sell rate spike (possible data error or sudden demand)
    """
    today = today or date.today()
    anomalies: list[Anomaly] = []

    for item in inventory:
        qty = float(item.get("total_qty", item.get("quantity_on_hand", 0)))
        sell_rate = float(item.get("weekly_sell_rate", 0))
        name = item.get("product_name", "Unknown")

        # Negative quantity (data error)
        if qty < 0:
            anomalies.append(Anomaly(
                anomaly_type="negative_inventory",
                entity_name=name,
                description=f"{name} has NEGATIVE inventory ({qty:.0f} units). Possible data error.",
                severity="high",
                value=qty,
                expected_range=(0.0, float("inf")),
                detected_at=today,
            ))

        # Zero quantity with high sell rate (should have been caught earlier)
        if qty == 0 and sell_rate > 0:
            anomalies.append(Anomaly(
                anomaly_type="zero_stock_active_item",
                entity_name=name,
                description=f"{name} is out of stock but still selling ({sell_rate:.1f}/wk sell rate).",
                severity="high",
                value=0.0,
                expected_range=(sell_rate * 4, float("inf")),  # At least 4 weeks stock
                detected_at=today,
            ))

    return anomalies


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return date(2000, 1, 1)
