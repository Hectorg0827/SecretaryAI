"""Account API quality: billed history, pagination, and unknown vs zero."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.api.accounts import get_account, list_accounts
from app.connectors.base import Customer, Invoice


TODAY = date.today()


def _customer(customer_id="cust-1", *, balance=Decimal("0")):
    return Customer(
        id=customer_id, qb_id=customer_id, name=f"Fixture {customer_id}",
        email=None, phone=None, state="NY", balance=balance,
        total_sales=Decimal("0"),
    )


def _invoice(
    invoice_id="invoice-1", *, customer_id="cust-1", days_ago=4,
    total=Decimal("100"), status="open",
):
    return Invoice(
        id=invoice_id, qb_id=invoice_id, customer_id=customer_id,
        customer_name="Fixture", date=TODAY - timedelta(days=days_ago),
        due_date=None, total=total, balance=total, status=status,
        line_items=[],
    )


def _adapter(customers=None, invoices=None):
    adapter = AsyncMock()
    adapter.get_all_customers.return_value = customers if customers is not None else [_customer()]
    adapter.get_orders_last_n_days.return_value = invoices if invoices is not None else []
    return adapter


@pytest.mark.asyncio
async def test_list_total_counts_all_accessible_accounts_not_just_limit():
    customers = [_customer(f"cust-{i}") for i in range(1, 4)]
    result = await list_accounts(limit=1, user={"role": "manager"}, adapter=_adapter(customers))
    assert len(result["accounts"]) == 1
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_truncated_365_day_history_without_billed_orders_is_unknown_not_dormant():
    result = await get_account("cust-1", user={"role": "owner"}, adapter=_adapter())
    assert result["health_status"] == "unknown"
    assert result["health_score"] is None
    assert result["avg_order_value_observed"] is False
    assert result["history_window_days"] == 365
    assert result["last_order_date"] is None


@pytest.mark.asyncio
async def test_void_draft_invoices_do_not_inflate_billed_order_average_or_recency():
    invoices = [
        _invoice("real", days_ago=30, total=Decimal("50.00")),
        _invoice("void", days_ago=1, total=Decimal("10000.00"), status="void"),
        _invoice("draft", days_ago=0, total=Decimal("10000.00"), status="draft"),
    ]
    result = await list_accounts(limit=50, user={"role": "owner"}, adapter=_adapter(invoices=invoices))
    account = result["accounts"][0]
    assert account["avg_order_value"] == 50.0
    assert account["avg_order_value_observed"] is True
    assert account["last_order_date"] == (TODAY - timedelta(days=30)).isoformat()


@pytest.mark.asyncio
async def test_older_billed_invoice_controls_health_when_newer_invoice_is_voided():
    invoices = [
        _invoice("real", days_ago=30),
        _invoice("void", days_ago=1, status="void"),
    ]
    result = await get_account("cust-1", user={"role": "owner"}, adapter=_adapter(invoices=invoices))
    assert result["last_order_date"] == (TODAY - timedelta(days=30)).isoformat()
    assert result["avg_order_value"] == 100.0
    assert result["velocity"]["recent_trend"] == "insufficient_data"
    assert len(result["recent_invoices"]) == 2  # Historical records still visible, with statuses.


@pytest.mark.asyncio
async def test_recent_invoice_history_is_newest_first_even_when_provider_order_is_not():
    invoices = [
        _invoice("old", days_ago=70),
        _invoice("new", days_ago=1),
        _invoice("middle", days_ago=20),
    ]
    result = await get_account("cust-1", user={"role": "owner"}, adapter=_adapter(invoices=invoices))
    assert [row["id"] for row in result["recent_invoices"]] == ["new", "middle", "old"]


@pytest.mark.asyncio
async def test_duplicate_provider_invoices_fail_instead_of_inflating_account_kpis():
    invoices = [_invoice("same"), _invoice("same")]
    with pytest.raises(ValueError, match="duplicate"):
        await list_accounts(limit=50, user={"role": "owner"}, adapter=_adapter(invoices=invoices))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_total", [None, float("nan"), "Infinity", "not-an-amount", -1])
async def test_invalid_invoice_money_cannot_become_zero_or_billings(bad_total):
    with pytest.raises(ValueError, match="total"):
        await list_accounts(
            limit=50, user={"role": "owner"},
            adapter=_adapter(invoices=[_invoice(total=bad_total)]),
        )


@pytest.mark.asyncio
async def test_missing_customer_balance_is_not_zero():
    with pytest.raises(ValueError, match="current_balance"):
        await list_accounts(
            limit=50, user={"role": "owner"},
            adapter=_adapter(customers=[_customer(balance=None)]),
        )


@pytest.mark.asyncio
async def test_signed_customer_credit_balance_is_preserved_but_not_health_scored():
    result = await get_account(
        "cust-1", user={"role": "owner"},
        adapter=_adapter(
            customers=[_customer(balance=Decimal("-42.75"))],
            invoices=[_invoice(total=Decimal("50"))],
        ),
    )
    assert result["current_balance"] == -42.75
    assert result["health_status"] == "unknown"
    assert result["health_score"] is None
    assert result["health_data_quality"] == "credit_balance_unscored"


@pytest.mark.asyncio
async def test_verified_zero_invoice_is_observed_not_unknown():
    result = await get_account(
        "cust-1", user={"role": "owner"},
        adapter=_adapter(invoices=[_invoice(total=Decimal("0"))]),
    )
    assert result["avg_order_value"] == 0.0
    assert result["avg_order_value_observed"] is True
    assert result["last_order_date"] == (TODAY - timedelta(days=4)).isoformat()


@pytest.mark.asyncio
async def test_unobserved_average_and_health_are_marked_as_incomplete():
    result = await list_accounts(
        limit=50, user={"role": "owner"},
        adapter=_adapter(invoices=[_invoice(status="cancelled")]),
    )
    account = result["accounts"][0]
    assert account["health_status"] == "unknown"
    assert account["health_data_quality"] == "partial_365_day_window"
    assert account["avg_order_value_observed"] is False
    assert account["last_order_date"] is None


@pytest.mark.asyncio
async def test_sales_rep_total_excludes_unowned_accounts_even_with_limit():
    from types import SimpleNamespace

    owned = SimpleNamespace(**{**_customer("owned").__dict__, "assigned_rep_id": "rep-1"})
    other = SimpleNamespace(**{**_customer("other").__dict__, "assigned_rep_id": "rep-2"})
    result = await list_accounts(
        limit=1, user={"role": "sales_rep", "sub": "rep-1"},
        adapter=_adapter(customers=[owned, other]),
    )
    assert result["total"] == 1
    assert [row["id"] for row in result["accounts"]] == ["owned"]
