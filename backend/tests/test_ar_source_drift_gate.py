"""Staging-only, read-only QBO source drift acceptance gates.

Two independent complete reads may detect mid-comparison changes, but cannot
provide a provider-guaranteed consistent snapshot. These synthetic fixtures
never call a real provider, database, or financial mutation API.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.unified_adapter import UnifiedDataAdapter
from app.scheduler.ar_payment_evidence import (
    ARPaymentEvidenceInvalid, assert_stable_ar_source_view,
)

DAY = date(2026, 11, 15)
CURRENCY = {"multicurrency_enabled": False, "home_currency": "USD"}


def _invoice(identifier="invoice-1", *, total="100.00", balance="40.00"):
    return {
        "id": identifier,
        "customer_id": "customer-secret",
        "currency": "USD",
        "txn_date": DAY,
        "total": total,
        "balance": balance,
    }


def _payment(identifier="payment-1", *, allocated="60.00"):
    return {
        "id": identifier,
        "customer_id": "customer-secret",
        "currency": "USD",
        "txn_date": DAY,
        "total": allocated,
        "unapplied": "0.00",
        "allocations": [{"invoice_id": "invoice-1", "amount": allocated}],
    }


def _adapter(*, invoices=None, payments=None, prefs=None):
    qb = QBOnlineAdapter.__new__(QBOnlineAdapter)
    qb.get_ar_currency_context_read_only = AsyncMock(side_effect=(
        [CURRENCY, CURRENCY] if prefs is None else prefs
    ))
    qb.get_ar_invoice_evidence_read_only = AsyncMock(side_effect=(
        [[_invoice()], [_invoice()]] if invoices is None else invoices
    ))
    qb.get_ar_payment_evidence_read_only = AsyncMock(side_effect=(
        [[_payment()], [_payment()]] if payments is None else payments
    ))
    qb.create_payment = AsyncMock()
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = qb
    adapter._config = {"id": "tenant-fixture", "company_id": "tenant-fixture"}
    return adapter, qb


async def _preview(adapter):
    with patch("app.config.get_settings", return_value=SimpleNamespace(
        secretaryai_deployment_env="staging", intuit_environment="sandbox",
    )):
        return await adapter.get_qbo_ar_payment_match_preview_read_only(
            company_id="tenant-fixture", as_of=DAY,
        )


def test_same_records_in_different_page_order_do_not_raise_false_drift():
    first = [_invoice("a"), _invoice("b", total="50.00", balance="50.00")]
    assert_stable_ar_source_view(first, list(reversed(first)), source="invoice")


def test_stability_guard_detects_modified_balance_and_added_or_missing_records():
    original = [_invoice()]
    variations = [
        [_invoice(balance="39.00")],
        [],
        [_invoice(), _invoice("extra")],
        [_invoice("another")],
    ]
    for changed in variations:
        with pytest.raises(ARPaymentEvidenceInvalid, match="changed"):
            assert_stable_ar_source_view(original, changed, source="invoice")


def test_stability_guard_rejects_duplicate_ids_and_unbounded_or_malformed_views():
    for changed in (
        [_payment(), _payment()],
        [None],
        None,
        [_payment()] * 5001,
    ):
        with pytest.raises(ARPaymentEvidenceInvalid):
            assert_stable_ar_source_view([_payment()], changed, source="payment")


@pytest.mark.asyncio
async def test_two_stable_source_reads_allow_only_noncertified_result():
    adapter, qb = _adapter()
    result = await _preview(adapter)
    assert result["status"] == "aligned_not_certified"
    assert result["accounting_reconciled"] is False
    assert result["financial_write_authorized"] is False
    assert qb.get_ar_currency_context_read_only.await_count == 2
    assert qb.get_ar_invoice_evidence_read_only.await_count == 2
    assert qb.get_ar_payment_evidence_read_only.await_count == 2
    qb.create_payment.assert_not_awaited()
    assert "customer-secret" not in str(result)
    assert "invoice-1" not in str(result)


@pytest.mark.asyncio
async def test_invoice_balance_change_during_payment_read_fails_closed():
    adapter, qb = _adapter(invoices=[
        [_invoice(balance="40.00")], [_invoice(balance="39.00")],
    ])
    with pytest.raises(ARPaymentEvidenceInvalid, match="changed") as err:
        await _preview(adapter)
    assert "customer-secret" not in str(err.value)
    assert "invoice-1" not in str(err.value)
    qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_allocation_change_between_read_passes_fails_closed():
    adapter, qb = _adapter(payments=[
        [_payment(allocated="60.00")], [_payment(allocated="59.00")],
    ])
    with pytest.raises(ARPaymentEvidenceInvalid, match="changed"):
        await _preview(adapter)
    qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_removed_payment_during_refresh_cannot_be_counted_as_zero():
    adapter, qb = _adapter(payments=[
        [_payment()], [],
    ])
    with pytest.raises(ARPaymentEvidenceInvalid, match="changed"):
        await _preview(adapter)
    qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_currency_preferences_change_during_reads_blocks_preview():
    adapter, qb = _adapter(prefs=[
        CURRENCY,
        {"multicurrency_enabled": False, "home_currency": "DOP"},
    ])
    with pytest.raises(ARPaymentEvidenceInvalid, match="preferences changed"):
        await _preview(adapter)
    qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_source_read_unavailable_never_returns_partial_counts():
    adapter, qb = _adapter(payments=[
        [_payment()], OSError("SENSITIVE_PROVIDER_CANARY"),
    ])
    with pytest.raises(OSError):
        await _preview(adapter)
    qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_page_order_changes_do_not_invalidate_stable_ar_source():
    unlinked = _invoice("invoice-2", total="20.00", balance="20.00")
    initial = [_invoice(), unlinked]
    adapter, qb = _adapter(invoices=[initial, list(reversed(initial))])
    result = await _preview(adapter)
    assert result["status"] == "aligned_not_certified"
    assert result["invoice_count"] == 2
    assert result["accounting_reconciled"] is False
    qb.create_payment.assert_not_awaited()
