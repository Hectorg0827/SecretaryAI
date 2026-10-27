"""Evidence-gated *heuristic* churn screening; not a calibrated prediction.

No active consumer currently calls this function. Do not surface a numeric risk
to a customer-facing workflow until the canonical tenant, order, freshness and
role-scope contracts have been accepted and validated on consented sources.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean, stdev


_SOURCE_STATUSES = frozenset({"fresh", "stale", "partial", "unavailable"})


def _unknown(reason: str, evidence_status: str, risk: str = "unknown") -> dict:
    return {
        "risk": risk,
        "reason": reason,
        "score": None,
        "evidence_status": evidence_status,
        "method": "no_estimate",
        "confidence": "not_calibrated",
    }


def _parse_order_date(value: object, today: date) -> date:
    if isinstance(value, datetime):
        raise ValueError("Order date must be a calendar date, not a timestamp")
    if isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Order date is invalid") from exc
    else:
        raise ValueError("Order date is missing or invalid")
    if parsed > today:
        raise ValueError("Order date is in the future")
    return parsed


def predict_churn_risk(
    customer_name: str,
    order_history: list[dict],
    *,
    today: date | None = None,
    history_complete: bool = False,
    source_status: str = "unavailable",
) -> dict:
    """Screen frequency/recency only when the entire source history is known.

    Callers must attest that the order history is complete and the source is
    fresh; a 30/365-day invoice query alone cannot establish completeness.
    Every order needs a stable source ID and a valid date. There is no tenant
    authorization, matching or source retrieval here; the Gate A caller owns
    those controls. Scores are uncalibrated ranking hypotheses, not causal
    findings or probabilities of churn.
    """
    if source_status not in _SOURCE_STATUSES:
        raise ValueError("Unknown source_status for churn assessment")
    today = today or date.today()
    if isinstance(today, datetime) or not isinstance(today, date):
        raise ValueError("today must be a calendar date")

    dates: list[date] = []
    seen_ids: set[str] = set()
    for order in order_history:
        if not isinstance(order, dict):
            raise ValueError("Order history row must be a mapping")
        order_id = order.get("id") or order.get("source_order_id")
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("Order history row has missing source id")
        if order_id in seen_ids:
            raise ValueError("Order history contains duplicate source id")
        seen_ids.add(order_id)
        dates.append(_parse_order_date(order.get("date"), today))

    if source_status != "fresh":
        return _unknown("Source orders are stale, partial or unavailable", source_status)
    if not history_complete:
        return _unknown("Complete historical order coverage has not been proven", "incomplete_history")
    if not dates:
        return _unknown("No observed orders in complete source history; churn does not apply", "complete_observed", "not_applicable")
    if len(set(dates)) < 2:
        return _unknown("Insufficient distinct order dates to infer an order cycle", "insufficient_history")

    dates.sort()
    days_since_last = (today - dates[-1]).days
    order_count_12m = sum(1 for d in dates if d >= today - timedelta(days=365))
    order_count_3m = sum(1 for d in dates if d >= today - timedelta(days=90))

    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    avg_gap = mean(gaps)
    gap_std = stdev(gaps) if len(gaps) > 1 else avg_gap

    # Heuristic weights are not probabilities or empirically fitted rates.
    recency_score = min(days_since_last / 120, 1.0)
    frequency_score = max(0, 1 - order_count_12m / 6)
    gap_score = min(days_since_last / (avg_gap + gap_std * 1.5 + 1), 1.0)
    score = round((recency_score * 0.5 + frequency_score * 0.3 + gap_score * 0.2), 2)

    if score >= 0.7:
        risk = "high"
        reason = f"Last order {days_since_last}d ago; historical gap mean {avg_gap:.0f}d"
    elif score >= 0.4:
        risk = "medium"
        reason = f"Recent frequency signal: {order_count_3m} orders in last 90 days"
    else:
        risk = "low"
        reason = f"Recent frequency signal: {order_count_12m} orders in last 12 months"

    return {
        "risk": risk,
        "reason": reason,
        "score": score,
        "evidence_status": "complete_observed",
        "method": "rule_based_hypothesis",
        "confidence": "not_calibrated",
    }
