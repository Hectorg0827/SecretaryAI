"""Read-only connector and account authorization regression contracts.

All fixtures are invented; tests never call an external service.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.gmail_connector import GmailConnector
from app.connectors.outlook import OutlookConnector
from app.connectors.unified_adapter import UnifiedDataAdapter
from app.api.accounts import get_account, list_accounts


def _customer(qb_id="cust-1", *, assigned_rep_id=None):
    return SimpleNamespace(
        id=qb_id, qb_id=qb_id, name="Fixture Account", email=None,
        phone=None, state="NY", balance=Decimal("0"),
        total_sales=Decimal("0"), assigned_rep_id=assigned_rep_id,
    )


def _qbo():
    return QBOnlineAdapter("client", "secret", "access", "refresh", "realm")


def test_qbo_sales_lines_preserve_item_identity_for_stock_velocity():
    invoice = _qbo()._normalize_invoice({
        "Id": "inv-1", "TxnDate": "2026-10-01", "CustomerRef": {"value": "cust-1"},
        "TotalAmt": "25.00", "Balance": "25.00", "Line": [{
            "DetailType": "SalesItemLineDetail", "Amount": "25.00",
            "SalesItemLineDetail": {"ItemRef": {"value": "sku-1", "name": "Item One"},
                                    "Qty": 5, "UnitPrice": "5.00"},
        }],
    })
    assert invoice.line_items[0]["item_id"] == "sku-1"
    assert invoice.line_items[0]["quantity"] == 5


def test_qbo_missing_transaction_date_is_not_fabricated_as_today():
    with pytest.raises(ValueError, match="TxnDate"):
        _qbo()._normalize_invoice({"Id": "inv-no-date", "TotalAmt": "5", "Balance": "5"})


def test_qbo_missing_balance_is_not_fabricated_as_paid():
    with pytest.raises(ValueError, match="Balance"):
        _qbo()._normalize_invoice({"Id": "inv-no-balance", "TxnDate": "2026-10-01", "TotalAmt": "5"})


@pytest.mark.asyncio
async def test_unified_adapter_qb_failure_is_not_an_empty_success():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = AsyncMock()
    adapter._qb.get_customers.side_effect = httpx.ConnectError("fixture disconnect")
    with pytest.raises(httpx.ConnectError):
        await adapter.get_all_customers()


@pytest.mark.asyncio
async def test_qbo_rotated_tokens_require_company_scope():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._config = {}
    with pytest.raises(RuntimeError, match="company identifier"):
        await adapter._persist_qbo_tokens("test-access", "test-refresh")


@pytest.mark.asyncio
async def test_qbo_token_persistence_failure_is_not_silently_accepted(caplog):
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._config = {"id": "tenant-fixture"}
    db = MagicMock()
    db.table().update().eq().execute.side_effect = RuntimeError("fixture storage unavailable")
    settings = SimpleNamespace(supabase_url="https://test.invalid", supabase_service_role_key="fixture-key",
                               secret_key="fixture-encryption-key")
    with patch("supabase.create_client", return_value=db), \
         patch("app.config.get_settings", return_value=settings), \
         patch("app.utils.encryption.encrypt", side_effect=lambda value, _: "encrypted"):
        with pytest.raises(RuntimeError, match="could not be persisted"):
            await adapter._persist_qbo_tokens("fixture-secret-access", "fixture-secret-refresh")
    assert "fixture-secret-access" not in caplog.text
    assert "fixture-secret-refresh" not in caplog.text


@pytest.mark.asyncio
async def test_outlook_read_failure_is_not_reported_as_empty_mailbox():
    outlook = OutlookConnector("tenant", "client", "secret", "token", "refresh")
    outlook._get_headers = AsyncMock(side_effect=httpx.ConnectError("fixture disconnect"))
    with pytest.raises(httpx.ConnectError):
        await outlook.get_emails()


@pytest.mark.asyncio
async def test_connectivity_does_not_claim_unverified_outlook_is_healthy():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = None
    adapter._gmail = None
    adapter._outlook = object()
    adapter._sheets = None
    adapter._shopify = None
    adapter._shiptrack = None
    assert (await adapter.test_all_connections())["outlook"] is False


@pytest.mark.asyncio
async def test_gmail_connectivity_uses_token_stored_by_real_connector():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = None
    adapter._gmail = GmailConnector({"token": "fixture-access-token"})
    adapter._outlook = adapter._sheets = adapter._shopify = adapter._shiptrack = None
    fake_response = MagicMock(status_code=200)
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.get.return_value = fake_response
    with patch("httpx.AsyncClient", return_value=fake_client):
        assert (await adapter.test_all_connections())["gmail"] is True
    assert fake_client.get.await_args.kwargs["headers"] == {
        "Authorization": "Bearer fixture-access-token",
    }


def _gmail_service(*, broken_message=False):
    service = MagicMock()
    service.users().messages().list().execute.return_value = {"messages": [{"id": "m-1"}]}
    if broken_message:
        service.users().messages().get().execute.side_effect = RuntimeError("fixture missing page")
    else:
        service.users().messages().get().execute.return_value = {
            "id": "m-1", "threadId": "t-1", "snippet": "fixture preview",
            "payload": {"headers": [{"name": "Message-ID", "value": "<fixture-1@example.invalid>"}]},
        }
    return service


def test_gmail_provides_stable_thread_and_message_identifiers():
    gmail = GmailConnector({"token": "fixture"})
    gmail._get_service = lambda: _gmail_service()
    email = gmail._fetch_emails("", 1)[0]
    assert email["thread_id"] == "t-1"
    assert email["message_id"] == "<fixture-1@example.invalid>"


def test_gmail_partial_message_fetch_fails_closed():
    gmail = GmailConnector({"token": "fixture"})
    gmail._get_service = lambda: _gmail_service(broken_message=True)
    with pytest.raises(RuntimeError, match="fixture missing page"):
        gmail._fetch_emails("", 1)


@pytest.mark.asyncio
async def test_missing_mail_source_is_not_an_empty_mailbox():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._gmail = adapter._outlook = None
    with pytest.raises(RuntimeError, match="No email connector"):
        await adapter.get_emails()


@pytest.mark.asyncio
async def test_sales_rep_without_verified_owner_sees_no_accounts():
    adapter = AsyncMock()
    adapter.get_all_customers.return_value = [_customer()]
    adapter.get_orders_last_n_days.return_value = []
    result = await list_accounts(
        limit=50, user={"role": "sales_rep", "sub": "rep-1"}, adapter=adapter,
    )
    assert result["accounts"] == []


@pytest.mark.asyncio
async def test_sales_rep_cannot_fetch_another_reps_account_detail():
    from fastapi import HTTPException

    adapter = AsyncMock()
    adapter.get_all_customers.return_value = [_customer(assigned_rep_id="rep-2")]
    adapter.get_orders_last_n_days.return_value = []
    with pytest.raises(HTTPException) as exc:
        await get_account("cust-1", user={"role": "sales_rep", "sub": "rep-1"}, adapter=adapter)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_sales_rep_cannot_fetch_account_without_ownership_field():
    from fastapi import HTTPException

    adapter = AsyncMock()
    adapter.get_all_customers.return_value = [_customer()]
    with pytest.raises(HTTPException) as exc:
        await get_account("cust-1", user={"role": "sales_rep", "sub": "rep-1"}, adapter=adapter)
    assert exc.value.status_code == 403
    adapter.get_orders_last_n_days.assert_not_awaited()


@pytest.mark.asyncio
async def test_sales_rep_can_read_only_a_verified_owned_account():
    adapter = AsyncMock()
    adapter.get_all_customers.return_value = [
        _customer("one", assigned_rep_id="rep-1"),
        _customer("two", assigned_rep_id="rep-2"),
    ]
    adapter.get_orders_last_n_days.return_value = []
    result = await list_accounts(
        limit=50, user={"role": "sales_rep", "sub": "rep-1"}, adapter=adapter,
    )
    assert [c["id"] for c in result["accounts"]] == ["one"]
