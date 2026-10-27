"""Read-only source integrity tests; never contact QuickBooks or Conductor."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.connectors.qb_desktop import QBDesktopAdapter
from app.connectors.qb_online import QBOnlineAdapter


def _qbo():
    return QBOnlineAdapter("client", "secret", "test-access", "test-refresh", "test-realm")


def _desktop():
    return QBDesktopAdapter("test-api-key", "test-user")


@pytest.mark.asyncio
async def test_qbo_provider_fault_is_not_reported_as_zero_customers():
    qb = _qbo()
    qb._query = AsyncMock(return_value={"Fault": {"Error": [{"Message": "Synthetic outage"}]}})
    with pytest.raises(ValueError, match="QueryResponse"):
        await qb.get_customers()


@pytest.mark.asyncio
async def test_qbo_explicit_zero_results_are_valid():
    qb = _qbo()
    qb._query = AsyncMock(return_value={"QueryResponse": {"totalCount": 0}})
    assert await qb.get_customers() == []


@pytest.mark.asyncio
async def test_qbo_repeated_page_identity_fails_closed():
    qb = _qbo()
    page = [{"Id": f"item-{i}"} for i in range(1000)]
    qb._query = AsyncMock(side_effect=[
        {"QueryResponse": {"Customer": page}},
        {"QueryResponse": {"Customer": page}},
    ])
    with pytest.raises(ValueError, match="duplicate"):
        await qb._query_all("Customer")
    assert qb._query.await_count == 2


@pytest.mark.asyncio
async def test_qbo_full_then_partial_page_is_complete_without_duplication():
    qb = _qbo()
    first = [{"Id": f"item-{i}"} for i in range(1000)]
    second = [{"Id": "item-1000"}, {"Id": "item-1001"}]
    qb._query = AsyncMock(side_effect=[
        {"QueryResponse": {"Customer": first}},
        {"QueryResponse": {"Customer": second}},
    ])
    result = await qb._query_all("Customer")
    assert len(result) == 1002
    assert "STARTPOSITION 1001" in qb._query.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_qbo_invalid_entity_payload_does_not_masquerade_as_empty_page():
    qb = _qbo()
    qb._query = AsyncMock(return_value={"QueryResponse": {"Customer": {"Id": "wrong-shape"}}})
    with pytest.raises(ValueError, match="list"):
        await qb._query_all("Customer")


@pytest.mark.asyncio
async def test_desktop_provider_fault_is_not_reported_as_no_invoices():
    qb = _desktop()
    qb._get = AsyncMock(return_value={"error": "Synthetic connection error"})
    with pytest.raises(ValueError, match="data"):
        await qb._paginate("/quickbooks-desktop/invoices")


@pytest.mark.asyncio
async def test_desktop_explicit_zero_results_are_valid():
    qb = _desktop()
    qb._get = AsyncMock(return_value={"data": [], "hasMore": False})
    assert await qb._paginate("/quickbooks-desktop/invoices") == []


@pytest.mark.asyncio
async def test_desktop_missing_continuation_cursor_fails_without_loop():
    qb = _desktop()
    qb._get = AsyncMock(return_value={"data": [{"id": "i1"}], "hasMore": True})
    with pytest.raises(ValueError, match="cursor"):
        await qb._paginate("/quickbooks-desktop/invoices")
    qb._get.assert_awaited_once()


@pytest.mark.asyncio
async def test_desktop_repeated_continuation_cursor_fails_without_loop():
    qb = _desktop()
    qb._get = AsyncMock(side_effect=[
        {"data": [{"id": "i1"}], "hasMore": True, "nextCursor": "cursor-a"},
        {"data": [{"id": "i2"}], "hasMore": True, "nextCursor": "cursor-a"},
    ])
    with pytest.raises(ValueError, match="cursor"):
        await qb._paginate("/quickbooks-desktop/invoices")
    assert qb._get.await_count == 2


@pytest.mark.asyncio
async def test_desktop_duplicate_invoice_across_pages_fails_closed():
    qb = _desktop()
    qb._get = AsyncMock(side_effect=[
        {"data": [{"id": "i1"}], "hasMore": True, "nextCursor": "cursor-a"},
        {"data": [{"id": "i1"}], "hasMore": False},
    ])
    with pytest.raises(ValueError, match="duplicate"):
        await qb._paginate("/quickbooks-desktop/invoices")


@pytest.mark.asyncio
async def test_desktop_empty_page_with_has_more_does_not_spin_forever():
    qb = _desktop()
    qb._get = AsyncMock(return_value={"data": [], "hasMore": True, "nextCursor": "advance"})
    with pytest.raises(ValueError, match="empty page"):
        await qb._paginate("/quickbooks-desktop/invoices")
    qb._get.assert_awaited_once()


@pytest.mark.parametrize("missing", ["transactionDate", "totalAmount", "balanceRemaining"])
def test_desktop_missing_required_invoice_value_cannot_be_imputed(missing):
    qb = _desktop()
    raw = {"id": "invoice-1", "transactionDate": "2026-10-01",
           "totalAmount": "100.00", "balanceRemaining": "100.00"}
    del raw[missing]
    with pytest.raises(ValueError, match=missing):
        qb._normalize_invoice(raw)


def test_desktop_genuine_zero_invoice_balance_is_preserved():
    qb = _desktop()
    invoice = qb._normalize_invoice({
        "id": "invoice-1", "transactionDate": "2026-10-01",
        "totalAmount": "100.00", "balanceRemaining": "0.00",
        "lineItems": [{"id": "line-1", "itemId": "item-1", "quantity": "2",
                       "amount": "100.00", "unitPrice": "50.00"}],
    })
    assert invoice.date == date(2026, 10, 1)
    assert invoice.balance == Decimal("0.00")
    assert invoice.status == "paid"


@pytest.mark.parametrize("malformed", ["NaN", "Infinity", "-1"])
def test_desktop_invalid_invoice_balance_is_rejected(malformed):
    qb = _desktop()
    with pytest.raises(ValueError, match="balanceRemaining"):
        qb._normalize_invoice({
            "id": "invoice-1", "transactionDate": "2026-10-01",
            "totalAmount": "100.00", "balanceRemaining": malformed,
        })


@pytest.mark.parametrize("connector, raw", [
    (_qbo, {"Id": "item-1", "Name": "Fixture", "UnitPrice": 10}),
    (_desktop, {"id": "item-1", "name": "Fixture", "salesPrice": 10}),
])
def test_absent_inventory_quantity_is_not_verified_zero(connector, raw):
    with pytest.raises(ValueError, match="quantity"):
        connector()._normalize_inventory_item(raw)


@pytest.mark.parametrize("connector, raw", [
    (_qbo, {"Id": "item-1", "Name": "Fixture", "QtyOnHand": "0"}),
    (_desktop, {"id": "item-1", "name": "Fixture", "quantityOnHand": "0"}),
])
def test_real_provider_zero_inventory_quantity_is_preserved(connector, raw):
    assert connector()._normalize_inventory_item(raw).quantity_on_hand == Decimal(0)


@pytest.mark.asyncio
async def test_qbo_provider_empty_query_response_without_total_count_is_valid():
    qb = _qbo()
    qb._query = AsyncMock(return_value={
        "QueryResponse": {}, "time": "2026-10-27T09:00:00-04:00",
    })
    assert await qb._query_all("Invoice") == []


@pytest.mark.asyncio
async def test_qbo_exact_full_page_then_empty_provider_response_completes():
    qb = _qbo()
    first = [{"Id": f"item-{i}"} for i in range(1000)]
    qb._query = AsyncMock(side_effect=[
        {"QueryResponse": {"Invoice": first}},
        {"QueryResponse": {}, "time": "2026-10-27T09:00:00-04:00"},
    ])
    assert len(await qb._query_all("Invoice")) == 1000
    assert qb._query.await_count == 2


@pytest.mark.asyncio
async def test_qbo_empty_shape_without_provider_evidence_is_not_assumed_valid():
    qb = _qbo()
    qb._query = AsyncMock(return_value={"QueryResponse": {}})
    with pytest.raises(ValueError, match="missing Invoice"):
        await qb._query_all("Invoice")


@pytest.mark.asyncio
async def test_qbo_fault_overrides_apparent_empty_query_response():
    qb = _qbo()
    qb._query = AsyncMock(return_value={
        "Fault": {"Error": [{"Message": "fixture fault"}]},
        "QueryResponse": {}, "time": "2026-10-27T09:00:00-04:00",
    })
    with pytest.raises(ValueError, match="Fault"):
        await qb._query_all("Invoice")
