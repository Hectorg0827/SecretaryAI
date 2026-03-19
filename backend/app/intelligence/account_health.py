"""
Account health scoring engine.
Classifies each customer account as: healthy, slowing, at_risk, or dormant.
Runs autonomously — no user approval needed.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional


@dataclass
class AccountHealthResult:
    account_id: str
    account_name: str
    score: int                 # 0-100
    status: str                # healthy | slowing | at_risk | dormant
    days_since_last_order: int
    avg_order_cycle_days: Optional[int]
    current_balance: Decimal
    flags: list[str]           # specific issues found


def score_account(
    account_id: str,
    account_name: str,
    last_order_date: Optional[date],
    avg_order_cycle_days: Optional[int],
    order_history: list[dict],  # [{date, total}]
    current_balance: Decimal,
    today: Optional[date] = None,
) -> AccountHealthResult:
    today = today or date.today()
    flags: list[str] = []
    score = 100

    # Days since last order
    if last_order_date is None:
        days_since = 999
        flags.append("No orders on record")
        score -= 40
    else:
        days_since = (today - last_order_date).days

    cycle = avg_order_cycle_days or 30

    # Overdue threshold: 1.5× the average cycle
    overdue_threshold = int(cycle * 1.5)
    severe_threshold = int(cycle * 2.5)

    if days_since > severe_threshold:
        flags.append(f"No order in {days_since} days (avg cycle: {cycle}d)")
        score -= 35
    elif days_since > overdue_threshold:
        flags.append(f"Order overdue by {days_since - cycle} days")
        score -= 15

    # Declining order size trend
    if len(order_history) >= 4:
        recent = [float(o["total"]) for o in sorted(order_history, key=lambda x: x["date"])[-4:]]
        if recent[-1] < recent[0] * 0.7:
            flags.append("Order value declining trend (>30% drop)")
            score -= 20
        elif recent[-1] < recent[0] * 0.85:
            flags.append("Order value declining trend")
            score -= 10

    # High outstanding balance
    if current_balance > Decimal("10000"):
        flags.append(f"High outstanding balance: ${current_balance:,.2f}")
        score -= 10

    # Classify
    score = max(0, score)
    if score >= 75:
        status = "healthy"
    elif score >= 50:
        status = "slowing"
    elif score >= 25:
        status = "at_risk"
    else:
        status = "dormant"

    return AccountHealthResult(
        account_id=account_id,
        account_name=account_name,
        score=score,
        status=status,
        days_since_last_order=days_since,
        avg_order_cycle_days=avg_order_cycle_days,
        current_balance=current_balance,
        flags=flags,
    )
