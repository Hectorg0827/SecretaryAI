"""Offline QBO sandbox payment source tests: mock API reads, no financial writes."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.qbo_ar_payment_evidence import (
    QBOARSourceInvalid, normalize_currency_preferences,
    normalize_invoice_for_ar, normalize_payment_for_ar,
)
from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.unified_adapter import UnifiedDataAdapter

DAY = date(2026, 11, 15)


def _settings(environment="staging", intuit="sandbox"):
    return SimpleNamespace(
        secretaryai_deployment_env=environment, intuit_environment=intuit,
    )


def _qbo():
    with patch("app.config.get_settings", return_value=_settings()):
        return QBOnlineAdapter(
            "fixture-client", "fixture-secret", "fixture-access",
            "fixture-refresh", "fixture-realm",
        )


def _invoice(**updates):
    value = {
        "Id": "inv-1", "CustomerRef": {"value": "customer-1"},
        "CurrencyRef": {"value": "USD"}, "TxnDate": DAY.isoformat(),
        "TotalAmt": 100.0, "Balance": 40.0,
    }
    value.update(updates)
    return value


def _payment(**updates):
    value = {
        "Id": "pay-1", "CustomerRef": {"value": "customer-1"},
        "CurrencyRef": {"value": "USD"}, "TxnDate": DAY.isoformat(),
        "TotalAmt": 60.0, "UnappliedAmt": 0.0,
        "Line": [{
            "Amount": 60.0,
            "LinkedTxn": [{"TxnId": "inv-1", "TxnType": "Invoice"}],
        }],
    }
    value.update(updates)
    return value


def test_qbo_invoice_and_payment_normalizers_preserve_only_necessary_evidence():
    raw_invoice = _invoice(PrivateNote="SENSITIVE_INVOICE_NOTE")
    raw_payment = _payment(PrivateNote="SENSITIVE_PAYMENT_NOTE")
    inv = normalize_invoice_for_ar(raw_invoice)
    pay = normalize_payment_for_ar(raw_payment)
    assert inv == {
        "id": "inv-1", "customer_id": "customer-1", "currency": "USD",
        "txn_date": DAY, "total": "100.00", "balance": "40.00",
    }
    assert pay == {
        "id": "pay-1", "customer_id": "customer-1", "currency": "USD",
        "txn_date": DAY, "total": "60.00", "unapplied": "0.00",
        "allocations": [{"invoice_id": "inv-1", "amount": "60.00"}],
    }
    assert "SENSITIVE" not in str(inv) + str(pay)


def test_unapplied_payment_can_have_no_invoice_links_but_not_invented_zero():
    assert normalize_payment_for_ar(_payment(
        TotalAmt=25.0, UnappliedAmt=25.0, Line=None,
    ))["allocations"] == []
    with pytest.raises(QBOARSourceInvalid):
        normalize_payment_for_ar(_payment(UnappliedAmt=None, Line=[]))


@pytest.mark.parametrize("changes", [
    {"CurrencyRef": None},
    {"CurrencyRef": {"value": "usd"}},
    {"CustomerRef": {"value": None}},
    {"TxnDate": None},
    {"TxnDate": "2026-13-15"},
    {"TotalAmt": None},
    {"TotalAmt": "NaN"},
    {"TotalAmt": "0.001"},
    {"TotalAmt": -1},
    {"Balance": "150.00"},
    {"Balance": "Infinity"},
])
def test_corrupt_invoice_source_fails_closed(changes):
    with pytest.raises(QBOARSourceInvalid):
        normalize_invoice_for_ar(_invoice(**changes))


@pytest.mark.parametrize("changes", [
    {"CurrencyRef": None},
    {"UnappliedAmt": None},
    {"TotalAmt": "NaN"},
    {"TotalAmt": "0.001"},
    {"TxnDate": "yesterday"},
    {"Line": []},  # total 60, unapplied 0 cannot be reconstructed
    {"Line": [{"Amount": 60, "LinkedTxn": [{"TxnId": "credit-1", "TxnType": "CreditMemo"}]}]},
    {"Line": [{"Amount": 60, "LinkedTxn": [
        {"TxnId": "inv-1", "TxnType": "Invoice"},
        {"TxnId": "credit-1", "TxnType": "CreditMemo"},
    ]}]},
    {"Line": [{"LinkedTxn": [{"TxnId": "inv-1", "TxnType": "Invoice"}]}]},
    {"Line": [{"Amount": 60, "LinkedTxn": None}]},
    {"Line": [{"Amount": 59.99, "LinkedTxn": [{"TxnId": "inv-1", "TxnType": "Invoice"}]}]},
    {"Line": [{"Amount": 60, "LinkedTxn": [{"TxnId": "", "TxnType": "Invoice"}]}]},
])
def test_ambiguous_payment_or_credit_memo_links_fail_closed(changes):
    with pytest.raises(QBOARSourceInvalid):
        normalize_payment_for_ar(_payment(**changes))


@pytest.mark.asyncio
async def test_payment_query_is_bounded_get_only_and_preserves_links():
    qb = _qbo()
    qb._query = AsyncMock(return_value={
        "QueryResponse": {"Payment": [_payment()]},
        "time": "2026-11-15T13:00:00Z",
    })
    qb._post = AsyncMock()
    with patch("app.config.get_settings", return_value=_settings()):
        rows = await qb.get_ar_payment_evidence_read_only()
    assert rows[0]["allocations"] == [{"invoice_id": "inv-1", "amount": "60.00"}]
    qb._query.assert_awaited_once()
    sql = qb._query.await_args.args[0]
    assert sql == "SELECT * FROM Payment STARTPOSITION 1 MAXRESULTS 1000"
    qb._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_provider_query_response_does_not_become_zero_payments():
    qb = _qbo()
    qb._query = AsyncMock(return_value={"QueryResponse": {"Invoice": []}})
    with patch("app.config.get_settings", return_value=_settings()):
        with pytest.raises(ValueError, match="missing Payment"):
            await qb.get_ar_payment_evidence_read_only()


@pytest.mark.asyncio
async def test_direct_qbo_reader_disallows_production_even_without_unified_adapter():
    qb = _qbo()
    qb._query = AsyncMock()
    with patch("app.config.get_settings", return_value=_settings(intuit="production")):
        with pytest.raises(RuntimeError, match="sandbox"):
            await qb.get_ar_invoice_evidence_read_only()
        with pytest.raises(RuntimeError, match="sandbox"):
            await qb.get_ar_payment_evidence_read_only()
    qb._query.assert_not_awaited()


def _adapter(*, company="tenant-a"):
    u = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    u._config = {"id": company, "company_id": company}
    u._qb = _qbo()
    u._qb.get_ar_currency_context_read_only = AsyncMock(return_value={
        "multicurrency_enabled": False, "home_currency": "USD",
    })
    u._qb.get_ar_invoice_evidence_read_only = AsyncMock(return_value=[
        normalize_invoice_for_ar(_invoice()),
    ])
    u._qb.get_ar_payment_evidence_read_only = AsyncMock(return_value=[
        normalize_payment_for_ar(_payment()),
    ])
    return u


@pytest.mark.asyncio
async def test_staging_sandbox_preview_reads_both_complete_feeds_without_write():
    u = _adapter()
    u._qb.create_payment = AsyncMock()
    with patch("app.config.get_settings", return_value=_settings()):
        result = await u.get_qbo_ar_payment_match_preview_read_only(
            company_id="tenant-a", as_of=DAY,
        )
    assert result["status"] == "aligned_not_certified"
    assert result["variance_invoice_count"] == 0
    assert result["accounting_reconciled"] is False
    assert result["financial_write_authorized"] is False
    assert "tenant-a" not in str(result)
    assert "inv-1" not in str(result)
    u._qb.get_ar_currency_context_read_only.assert_awaited_once()
    u._qb.get_ar_invoice_evidence_read_only.assert_awaited_once_with(
        currency_context={"multicurrency_enabled": False, "home_currency": "USD"},
    )
    u._qb.get_ar_payment_evidence_read_only.assert_awaited_once_with(
        currency_context={"multicurrency_enabled": False, "home_currency": "USD"},
    )
    u._qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_blocks_production_and_cross_tenant_before_provider_reads():
    for env, intuit, cid in [
        ("production", "sandbox", "tenant-a"),
        ("staging", "production", "tenant-a"),
        ("staging", "sandbox", "tenant-other"),
    ]:
        u = _adapter()
        with patch("app.config.get_settings", return_value=_settings(env, intuit)):
            with pytest.raises((RuntimeError, PermissionError)):
                await u.get_qbo_ar_payment_match_preview_read_only(
                    company_id=cid, as_of=DAY,
                )
        u._qb.get_ar_currency_context_read_only.assert_not_awaited()
        u._qb.get_ar_invoice_evidence_read_only.assert_not_awaited()
        u._qb.get_ar_payment_evidence_read_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_denies_mismatched_tenant_metadata():
    u = _adapter()
    u._config["company_id"] = "tenant-other"
    with patch("app.config.get_settings", return_value=_settings()):
        with pytest.raises(PermissionError):
            await u.get_qbo_ar_payment_match_preview_read_only(
                company_id="tenant-a", as_of=DAY,
            )
    u._qb.get_ar_currency_context_read_only.assert_not_awaited()
    u._qb.get_ar_invoice_evidence_read_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_remains_unavailable_not_zero_receipts():
    u = _adapter()
    u._qb.get_ar_payment_evidence_read_only.side_effect = OSError(
        "SENSITIVE_PROVIDER_RESPONSE_CANARY"
    )
    with patch("app.config.get_settings", return_value=_settings()):
        with pytest.raises(OSError):
            await u.get_qbo_ar_payment_match_preview_read_only(
                company_id="tenant-a", as_of=DAY,
            )
    u._qb.create_payment = AsyncMock()
    u._qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_currency_invoice_and_payment_never_match():
    u = _adapter()
    u._qb.get_ar_payment_evidence_read_only.return_value = [
        normalize_payment_for_ar(_payment(CurrencyRef={"value": "EUR"})),
    ]
    with patch("app.config.get_settings", return_value=_settings()):
        with pytest.raises(ValueError, match="currency mismatch"):
            await u.get_qbo_ar_payment_match_preview_read_only(
                company_id="tenant-a", as_of=DAY,
            )


# Intuit CurrencyRef is conditionally required. A verified company-wide
# Preferences read may supply HomeCurrency ONLY when multicurrency is off.
@pytest.mark.parametrize("multi,home", [
    (False, "EUR"), ("false", "DOP"), (True, "USD"), ("true", "GBP"),
])
def test_verified_preferences_currency_normalization(multi, home):
    result = normalize_currency_preferences({
        "CurrencyPrefs": {
            "MultiCurrencyEnabled": multi, "HomeCurrency": {"value": home},
        },
    })
    assert result == {
        "multicurrency_enabled": multi in (True, "true"),
        "home_currency": home,
    }


@pytest.mark.parametrize("bad", [
    {},
    {"CurrencyPrefs": {}},
    {"CurrencyPrefs": {"MultiCurrencyEnabled": None, "HomeCurrency": {"value": "USD"}}},
    {"CurrencyPrefs": {"MultiCurrencyEnabled": 0, "HomeCurrency": {"value": "USD"}}},
    {"CurrencyPrefs": {"MultiCurrencyEnabled": False, "HomeCurrency": None}},
    {"CurrencyPrefs": {"MultiCurrencyEnabled": True, "HomeCurrency": {"value": "usd"}}},
])
def test_unknown_or_malformed_preferences_cannot_guess_a_currency(bad):
    with pytest.raises(QBOARSourceInvalid):
        normalize_currency_preferences(bad)


def test_single_currency_preference_allows_missing_currencyref_without_usd_default():
    context = {"multicurrency_enabled": False, "home_currency": "DOP"}
    inv = normalize_invoice_for_ar(_invoice(CurrencyRef=None), currency_context=context)
    pay = normalize_payment_for_ar(_payment(CurrencyRef=None), currency_context=context)
    assert inv["currency"] == "DOP"
    assert pay["currency"] == "DOP"
    assert "USD" not in str(inv) + str(pay)


def test_multicurrency_or_unsupported_currency_missing_ref_still_fails_closed():
    for context in (None, {"multicurrency_enabled": True, "home_currency": "USD"}):
        with pytest.raises(QBOARSourceInvalid):
            normalize_invoice_for_ar(_invoice(CurrencyRef=None), currency_context=context)
        with pytest.raises(QBOARSourceInvalid):
            normalize_payment_for_ar(_payment(CurrencyRef=None), currency_context=context)
    single = {"multicurrency_enabled": False, "home_currency": "EUR"}
    with pytest.raises(QBOARSourceInvalid, match="mismatch"):
        normalize_payment_for_ar(_payment(CurrencyRef={"value": "USD"}),
                                 currency_context=single)


@pytest.mark.asyncio
async def test_preferences_query_is_get_only_singleton_and_sandbox_scoped():
    qb = _qbo()
    qb._query = AsyncMock(return_value={
        "QueryResponse": {"Preferences": [{
            "CurrencyPrefs": {
                "MultiCurrencyEnabled": False,
                "HomeCurrency": {"value": "DOP"},
            },
        }]},
        "time": "2026-11-15T13:00:00Z",
    })
    qb._post = AsyncMock()
    with patch("app.config.get_settings", return_value=_settings()):
        context = await qb.get_ar_currency_context_read_only()
    assert context == {"multicurrency_enabled": False, "home_currency": "DOP"}
    qb._query.assert_awaited_once_with("select * from Preferences")
    qb._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_preferences_missing_multiple_or_faulted_never_calls_invoice_read():
    qb = _qbo()
    for envelope in (
        {"QueryResponse": {}},
        {"QueryResponse": {"Preferences": []}},
        {"QueryResponse": {"Preferences": [{}, {}]}},
        {"Fault": {"Error": [{"Message": "SENSITIVE"}]}},
    ):
        qb._query = AsyncMock(return_value=envelope)
        with patch("app.config.get_settings", return_value=_settings()):
            with pytest.raises(QBOARSourceInvalid):
                await qb.get_ar_currency_context_read_only()


@pytest.mark.asyncio
async def test_preferences_reader_rejected_in_production_before_any_query():
    qb = _qbo()
    qb._query = AsyncMock()
    with patch("app.config.get_settings",
               return_value=_settings(intuit="production")):
        with pytest.raises(RuntimeError, match="sandbox"):
            await qb.get_ar_currency_context_read_only()
    qb._query.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_currency_is_resolved_by_shared_verified_context_in_preview():
    u = _adapter()
    context = {"multicurrency_enabled": False, "home_currency": "DOP"}
    u._qb.get_ar_currency_context_read_only.return_value = context
    u._qb.get_ar_invoice_evidence_read_only.return_value = [
        normalize_invoice_for_ar(_invoice(CurrencyRef=None), currency_context=context),
    ]
    u._qb.get_ar_payment_evidence_read_only.return_value = [
        normalize_payment_for_ar(_payment(CurrencyRef=None), currency_context=context),
    ]
    with patch("app.config.get_settings", return_value=_settings()):
        result = await u.get_qbo_ar_payment_match_preview_read_only(
            company_id="tenant-a", as_of=DAY,
        )
    assert result["status"] == "aligned_not_certified"
    assert result["accounting_reconciled"] is False
    u._qb.get_ar_invoice_evidence_read_only.assert_awaited_once_with(
        currency_context=context,
    )
    u._qb.get_ar_payment_evidence_read_only.assert_awaited_once_with(
        currency_context=context,
    )


@pytest.mark.asyncio
async def test_preference_read_failure_blocks_both_transaction_feeds():
    u = _adapter()
    u._qb.get_ar_currency_context_read_only.side_effect = QBOARSourceInvalid(
        "SENSITIVE PREFERENCE FAILURE"
    )
    with patch("app.config.get_settings", return_value=_settings()):
        with pytest.raises(QBOARSourceInvalid):
            await u.get_qbo_ar_payment_match_preview_read_only(
                company_id="tenant-a", as_of=DAY,
            )
    u._qb.get_ar_invoice_evidence_read_only.assert_not_awaited()
    u._qb.get_ar_payment_evidence_read_only.assert_not_awaited()
