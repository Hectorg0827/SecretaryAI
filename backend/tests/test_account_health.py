"""Tests for account health scoring engine."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.intelligence.account_health import score_account


def test_healthy_account():
    result = score_account(
        account_id="1",
        account_name="Test Account",
        last_order_date=date.today(),
        avg_order_cycle_days=30,
        order_history=[{"date": date.today(), "total": 1000}],
        current_balance=Decimal("0"),
    )
    assert result.status == "healthy"
    assert result.score >= 75


def test_dormant_account_no_orders():
    result = score_account(
        account_id="2",
        account_name="Dormant Co",
        last_order_date=None,
        avg_order_cycle_days=30,
        order_history=[],
        current_balance=Decimal("0"),
    )
    assert result.status in ("at_risk", "dormant")
    assert len(result.flags) > 0


def test_overdue_account():
    from datetime import timedelta
    # 90 days since last order, cycle=30 → severe_threshold=75 → score -= 35 → 65 → "slowing"
    result = score_account(
        account_id="3",
        account_name="Overdue Co",
        last_order_date=date.today() - timedelta(days=90),
        avg_order_cycle_days=30,
        order_history=[],
        current_balance=Decimal("0"),
    )
    assert result.status in ("slowing", "at_risk", "dormant")
    assert len(result.flags) > 0


def test_at_risk_account_multiple_signals():
    from datetime import timedelta
    # at_risk requires multiple bad signals: overdue + declining value + high balance
    today = date.today()
    order_history = [
        {"date": str(today - timedelta(days=180)), "total": 5000},
        {"date": str(today - timedelta(days=150)), "total": 4000},
        {"date": str(today - timedelta(days=120)), "total": 3000},
        {"date": str(today - timedelta(days=90)), "total": 2000},  # >30% drop
    ]
    result = score_account(
        account_id="3b",
        account_name="Multi-Signal Risk Co",
        last_order_date=today - timedelta(days=90),
        avg_order_cycle_days=30,
        order_history=order_history,
        current_balance=Decimal("15000"),  # high balance penalty too
    )
    assert result.status in ("at_risk", "dormant")


def test_high_balance_flag():
    result = score_account(
        account_id="4",
        account_name="Big Balance",
        last_order_date=date.today(),
        avg_order_cycle_days=30,
        order_history=[{"date": date.today(), "total": 5000}],
        current_balance=Decimal("15000"),
    )
    assert any("balance" in f.lower() for f in result.flags)


def _healthy_fixture(**changes):
    today = date(2026, 10, 26)
    args = {
        "account_id": "fictional-1", "account_name": "Fixture Account",
        "last_order_date": today - timedelta(days=7),
        "avg_order_cycle_days": 30,
        "order_history": [
            {"date": (today - timedelta(days=30)).isoformat(), "total": "200.00"},
            {"date": today - timedelta(days=7), "total": "100.00"},
        ],
        "current_balance": Decimal("0"), "today": today,
    }
    args.update(changes)
    return score_account(**args)


def test_mixed_string_and_date_history_with_last_order_string_is_valid():
    result = _healthy_fixture(last_order_date="2026-10-19")
    assert result.status == "healthy"
    assert result.days_since_last_order == 7


def test_last_order_is_derived_from_observed_history_if_not_supplied():
    result = _healthy_fixture(last_order_date=None)
    assert result.days_since_last_order == 7
    assert "No orders on record" not in result.flags


@pytest.mark.parametrize("balance", [None, "NaN", "Infinity", "-1", "unavailable"])
def test_missing_or_invalid_balance_never_scores_as_healthy(balance):
    with pytest.raises(ValueError, match="balance"):
        _healthy_fixture(current_balance=balance)


@pytest.mark.parametrize("row", [
    {"date": "garbage", "total": "100"},
    {"date": "2026-10-27", "total": "100"},
    {"date": "2026-10-15", "total": None},
    {"date": "2026-10-15", "total": "NaN"},
])
def test_bad_order_history_fails_instead_of_silently_ignoring(row):
    with pytest.raises(ValueError, match="[Oo]rder"):
        _healthy_fixture(order_history=[row])


def test_recent_order_cannot_precede_last_order_if_both_observed():
    with pytest.raises(ValueError, match="last_order_date"):
        _healthy_fixture(last_order_date=date(2026, 9, 1))


@pytest.mark.parametrize("cycle", [0, -10, float("nan")])
def test_invalid_observed_cycle_does_not_silently_substitute_30_days(cycle):
    with pytest.raises(ValueError, match="cycle"):
        _healthy_fixture(avg_order_cycle_days=cycle)


def test_unobserved_cycle_is_explicitly_labeled_heuristic():
    result = _healthy_fixture(avg_order_cycle_days=None)
    assert any("unverified" in flag.lower() and "cycle" in flag.lower() for flag in result.flags)
