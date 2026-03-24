"""
Simple churn risk predictor based on order frequency and recency.
No ML infra needed — rule-based scoring on order patterns.
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from statistics import mean, stdev

log = logging.getLogger(__name__)


def predict_churn_risk(customer_name: str, order_history: list[dict]) -> dict:
    """
    Returns {"risk": "high"|"medium"|"low", "reason": str, "score": float 0-1}
    order_history: list of {"date": date|str, "total": float}
    """
    if not order_history:
        return {"risk": "high", "reason": "No order history", "score": 0.9}

    dates = []
    for o in order_history:
        d = o.get("date")
        if d is None:
            continue
        if isinstance(d, str):
            try:
                d = date.fromisoformat(str(d)[:10])
            except ValueError:
                continue
        dates.append(d)

    if not dates:
        return {"risk": "medium", "reason": "No parseable order dates", "score": 0.5}

    dates.sort()
    today = date.today()
    days_since_last = (today - dates[-1]).days
    order_count_12m = sum(1 for d in dates if d >= today - timedelta(days=365))
    order_count_3m = sum(1 for d in dates if d >= today - timedelta(days=90))

    # Compute gaps between consecutive orders
    gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    avg_gap = mean(gaps) if gaps else 365
    gap_std = stdev(gaps) if len(gaps) > 1 else avg_gap

    # Score components (0 = healthy, 1 = churned)
    recency_score = min(days_since_last / 120, 1.0)
    frequency_score = max(0, 1 - order_count_12m / 6)
    gap_score = min(days_since_last / (avg_gap + gap_std * 1.5 + 1), 1.0)

    score = round((recency_score * 0.5 + frequency_score * 0.3 + gap_score * 0.2), 2)

    if score >= 0.7:
        risk = "high"
        reason = f"Last order {days_since_last}d ago; avg gap {avg_gap:.0f}d — significantly overdue"
    elif score >= 0.4:
        risk = "medium"
        reason = f"Order frequency slowing — {order_count_3m} orders in last 90 days"
    else:
        risk = "low"
        reason = f"Ordering regularly — {order_count_12m} orders in last 12 months"

    return {"risk": risk, "reason": reason, "score": score}
