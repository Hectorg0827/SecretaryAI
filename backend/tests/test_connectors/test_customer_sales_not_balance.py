"""A customer's open balance must never be reported as their sales.

QuickBooks Online ``BalanceWithJobs`` and Conductor (QuickBooks Desktop)
``totalBalance`` are money still owed, including sub-customers/jobs. Neither
provider's customer record carries a sales total, so ``total_sales`` is
unknown (None) rather than a balance or a zero.

All fixtures are invented; provider HTTP is patched, nothing leaves the test.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.base import Customer
from app.connectors.qb_desktop import QBDesktopAdapter
from app.connectors.qb_online import QBOnlineAdapter
from app.router.access_router import AccessRouter


QBO_CUSTOMER = {
    "Id": "58",
    "DisplayName": "Fixture Grocers",
    "Balance": "250.00",
    "BalanceWithJobs": "900.00",
    "BillAddr": {"CountrySubDivisionCode": "NY"},
}

QBD_CUSTOMER = {
    "id": "80000001-1234567890",
    "name": "Fixture Grocers",
    "balance": "250.00",
    "totalBalance": "900.00",
    "billingAddress": {"state": "NJ"},
}


def _qbo() -> QBOnlineAdapter:
    return QBOnlineAdapter("client", "secret", "access", "refresh", "realm")


def _qbd() -> QBDesktopAdapter:
    return QBDesktopAdapter(api_key="fixture-key", end_user_id="fixture-user")


def test_qbo_balance_with_jobs_is_not_reported_as_sales():
    customer = _qbo()._normalize_customer(QBO_CUSTOMER)
    assert customer.balance == Decimal("250.00")
    assert customer.total_sales is None
    assert Decimal("900.00") not in (customer.balance, customer.total_sales)


def test_qbd_total_balance_is_not_reported_as_sales():
    customer = _qbd()._normalize_customer(QBD_CUSTOMER)
    assert customer.balance == Decimal("250.00")
    assert customer.total_sales is None
    assert Decimal("900.00") not in (customer.balance, customer.total_sales)


def test_customer_sales_default_to_unknown_not_zero():
    customer = Customer(
        id="c1", qb_id="c1", name="Fixture", email=None, phone=None,
        state=None, balance=Decimal("0"),
    )
    assert customer.total_sales is None


@pytest.mark.asyncio
async def test_qbo_get_customers_returns_unknown_sales_for_every_record():
    adapter = _qbo()
    with patch.object(adapter, "_query_all", AsyncMock(return_value=[QBO_CUSTOMER])):
        customers = await adapter.get_customers()
    assert [c.total_sales for c in customers] == [None]


@pytest.mark.asyncio
async def test_qbd_get_customers_returns_unknown_sales_for_every_record():
    adapter = _qbd()
    with patch.object(adapter, "_paginate", AsyncMock(return_value=[QBD_CUSTOMER])):
        customers = await adapter.get_customers()
    assert [c.total_sales for c in customers] == [None]


@pytest.mark.asyncio
async def test_access_router_customer_rows_carry_unknown_sales_not_balance():
    """The router's API path serializes customers for automation consumers."""
    config = {
        "id": "tenant-a", "qb_type": "online",
        "qbo_client_id": "client", "qbo_client_secret": "secret",
        "qbo_access_token": "access", "qbo_refresh_token": "refresh",
        "qbo_realm_id": "realm",
    }
    router = AccessRouter(config, db=None)
    with patch.object(QBOnlineAdapter, "_query_all", AsyncMock(return_value=[QBO_CUSTOMER])):
        payload = await router._try_api("customers", {})
    [row] = payload["customers"]
    assert row["balance"] == Decimal("250.00")
    assert row["total_sales"] is None
