"""Gate A side-effect and business-data truthfulness regression tests.

No provider calls, email delivery, database migrations or external HTTP requests.
"""
import inspect
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.dashboard import (
    SendReplyRequest, get_sales_data, mark_email_read, send_email_reply,
)
from app.connectors.base import Invoice


@pytest.mark.asyncio
async def test_legacy_direct_email_send_is_disabled_even_for_authorized_owner():
    request = SendReplyRequest(
        email_id="synthetic-mail-1", to="fictional@example.invalid",
        subject="Fixture only", body="Test only",
    )
    assert "adapter" not in inspect.signature(send_email_reply).parameters
    assert "db" not in inspect.signature(send_email_reply).parameters
    with pytest.raises(HTTPException) as caught:
        await send_email_reply(
            body=request, user={"company_id": "tenant-a", "role": "owner", "sub": "user-a"}
        )
    assert caught.value.status_code == 409
    assert "disabled" in caught.value.detail.lower()


@pytest.mark.asyncio
async def test_email_mark_read_failure_is_not_reported_as_success():
    adapter = AsyncMock()
    adapter.mark_email_read.side_effect = ConnectionError("mail provider offline")
    with pytest.raises(HTTPException) as caught:
        await mark_email_read(
            "synthetic-mail-1",
            user={"company_id": "tenant-a", "sub": "user-a"},
            adapter=adapter,
        )
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_email_mark_read_provider_success_is_acknowledged():
    adapter = AsyncMock()
    result = await mark_email_read(
        "synthetic-mail-1", user={"company_id": "tenant-a", "sub": "user-a"},
        adapter=adapter,
    )
    assert result == {"status": "ok"}
    adapter.mark_email_read.assert_awaited_once_with("synthetic-mail-1")


@pytest.mark.asyncio
async def test_sales_provider_failure_never_becomes_zero_revenue():
    adapter = AsyncMock()
    adapter.get_orders_last_n_days.side_effect = ConnectionError("accounting offline")
    with pytest.raises(HTTPException) as caught:
        await get_sales_data(
            days=30, user={"company_id": "tenant-a", "sub": "user-a"},
            adapter=adapter,
        )
    assert caught.value.status_code == 503
    assert "unavailable" in caught.value.detail.lower()


@pytest.mark.asyncio
async def test_zero_value_invoices_remain_valid_sales_results():
    invoice = Invoice(
        id="synthetic-id", qb_id="synthetic-qb-id",
        customer_id="customer-a", customer_name="Synthetic",
        date=date.today(), due_date=None,
        total=Decimal("0"), balance=Decimal("0"),
        status="paid", line_items=[],
    )
    adapter = AsyncMock()
    adapter.get_orders_last_n_days.return_value = [invoice]
    result = await get_sales_data(
        days=30, user={"company_id": "tenant-a", "sub": "user-a"},
        adapter=adapter,
    )
    assert result["total_revenue"] == 0
    assert result["order_count"] == 1
