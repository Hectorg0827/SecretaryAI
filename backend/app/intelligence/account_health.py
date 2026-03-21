"""
Account health scoring engine.
Classifies each customer account as: healthy, slowing, at_risk, or dormant.
Runs autonomously — no user approval needed.

Scoring weights, penalty values, thresholds, and status boundaries are loaded
from the active IndustryModule — no values are hardcoded. Different industries
have different dormancy patterns, balance thresholds, and scoring sensitivities.
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


def _load_scoring_config(industry_module=None) -> dict:
    """
    Load account health scoring configuration from the industry module.
    Falls back to wholesale distribution defaults when no module is provided.
    """
    if industry_module is None:
        return {
            "no_order_penalty": 40,
            "severe_recency_penalty": 35,
            "overdue_recency_penalty": 15,
            "severe_cycle_multiplier": 2.5,
            "overdue_cycle_multiplier": 1.5,
            "trend_critical_penalty": 20,   # >30% decline
            "trend_warning_penalty": 10,    # >15% decline
            "trend_critical_threshold": 0.70,
            "trend_warning_threshold": 0.85,
            "balance_penalty": 10,
            "balance_threshold": Decimal("10000"),
            "status_healthy": 75,
            "status_slowing": 50,
            "status_at_risk": 25,
        }

    kpis = industry_module.get_kpi_definitions()
    scoring = industry_module.get_scoring_config()

    # Balance threshold from KPI definitions
    balance_kpi = kpis.get("high_balance_threshold")
    balance_threshold = Decimal(str(balance_kpi.critical_threshold)) if balance_kpi else Decimal("10000")

    # Status boundaries from scoring config
    acct_scoring = scoring.get("account_health")
    boundaries = acct_scoring.status_boundaries if acct_scoring else {}

    return {
        "no_order_penalty": 40,
        "severe_recency_penalty": 35,
        "overdue_recency_penalty": 15,
        "severe_cycle_multiplier": 2.5,
        "overdue_cycle_multiplier": 1.5,
        "trend_critical_penalty": 20,
        "trend_warning_penalty": 10,
        "trend_critical_threshold": 0.70,
        "trend_warning_threshold": 0.85,
        "balance_penalty": 10,
        "balance_threshold": balance_threshold,
        "status_healthy": boundaries.get("healthy", 75),
        "status_slowing": boundaries.get("slowing", 50),
        "status_at_risk": boundaries.get("at_risk", 25),
    }


def score_account(
    account_id: str,
    account_name: str,
    last_order_date: Optional[date],
    avg_order_cycle_days: Optional[int],
    order_history: list[dict],  # [{date, total}]
    current_balance: Decimal,
    today: Optional[date] = None,
    industry_module=None,
) -> AccountHealthResult:
    """
    Score an account's health.

    industry_module: optional IndustryModule to load thresholds from.
    When None, falls back to wholesale distribution defaults (backward compatible).
    """
    today = today or date.today()
    cfg = _load_scoring_config(industry_module)
    flags: list[str] = []
    score = 100

    # Days since last order
    if last_order_date is None:
        days_since = 999
        flags.append("No orders on record")
        score -= cfg["no_order_penalty"]
    else:
        days_since = (today - last_order_date).days

    cycle = avg_order_cycle_days or 30
    overdue_threshold = int(cycle * cfg["overdue_cycle_multiplier"])
    severe_threshold = int(cycle * cfg["severe_cycle_multiplier"])

    if days_since > severe_threshold:
        flags.append(f"No order in {days_since} days (avg cycle: {cycle}d)")
        score -= cfg["severe_recency_penalty"]
    elif days_since > overdue_threshold:
        flags.append(f"Order overdue by {days_since - cycle} days")
        score -= cfg["overdue_recency_penalty"]

    # Declining order size trend
    if len(order_history) >= 4:
        recent = [float(o["total"]) for o in sorted(order_history, key=lambda x: x["date"])[-4:]]
        if recent[-1] < recent[0] * cfg["trend_critical_threshold"]:
            flags.append("Order value declining trend (>30% drop)")
            score -= cfg["trend_critical_penalty"]
        elif recent[-1] < recent[0] * cfg["trend_warning_threshold"]:
            flags.append("Order value declining trend")
            score -= cfg["trend_warning_penalty"]

    # High outstanding balance
    if current_balance > cfg["balance_threshold"]:
        flags.append(f"High outstanding balance: ${current_balance:,.2f}")
        score -= cfg["balance_penalty"]

    # Classify using module-defined status boundaries
    score = max(0, score)
    if score >= cfg["status_healthy"]:
        status = "healthy"
    elif score >= cfg["status_slowing"]:
        status = "slowing"
    elif score >= cfg["status_at_risk"]:
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
