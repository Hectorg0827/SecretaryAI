"""Tests for sales analytics engine."""
from datetime import date, timedelta
from app.intelligence.sales_analytics import compute_period_sales, compute_trend_comparison, compute_account_velocity


def _make_invoice(customer: str, days_ago: int, total: float, items=None) -> dict:
    order_date = (date.today() - timedelta(days=days_ago)).isoformat()
    return {
        "customer_name": customer,
        "order_date": order_date,
        "total_amount": total,
        "items": items or [{"name": "Product A", "quantity": 10, "amount": total}],
    }


def test_period_sales_basic():
    invoices = [
        _make_invoice("Account A", 5, 1000),
        _make_invoice("Account B", 10, 2000),
        _make_invoice("Account A", 20, 1500),
    ]
    today = date.today()
    result = compute_period_sales(invoices, today - timedelta(days=30), today)
    assert result.order_count == 3
    assert float(result.total_revenue) == 4500
    assert len(result.top_accounts) > 0


def test_trend_comparison_returns_change_pct():
    invoices = [
        _make_invoice("Account A", 5, 1000),
        _make_invoice("Account A", 35, 800),
    ]
    result = compute_trend_comparison(invoices, days=30)
    assert "change_pct" in result
    assert result["trend"] in ("up", "down", "flat")


def test_account_velocity_avg_gap():
    invoices = [
        {"order_date": (date.today() - timedelta(days=60)).isoformat(), "total_amount": 1000},
        {"order_date": (date.today() - timedelta(days=30)).isoformat(), "total_amount": 1100},
        {"order_date": (date.today() - timedelta(days=0)).isoformat(), "total_amount": 1050},
    ]
    result = compute_account_velocity(invoices)
    assert result["avg_days_between_orders"] == 30.0
    assert result["recent_trend"] in ("growing", "stable", "declining")
