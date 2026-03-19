"""Tests for account health scoring engine."""
from datetime import date
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
    result = score_account(
        account_id="3",
        account_name="Overdue Co",
        last_order_date=date.today() - timedelta(days=90),
        avg_order_cycle_days=30,
        order_history=[],
        current_balance=Decimal("0"),
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
