"""Read-only QuickBooks projections: parsing, validation and no-write guarantees."""
import inspect
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.connectors import qb_bi_reads
from app.connectors.qb_bi_reads import (
    QBDesktopBIReads, QBOnlineBIReads, SourceNotSupported, parse_profit_and_loss,
)
from app.connectors.qb_online import QBOnlineAdapter


@pytest.fixture
def qbo(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(intuit_environment="sandbox"))
    return QBOnlineAdapter("c", "s", "a", "r", "realm")


def _feed(qbo, monkeypatch, responses: dict):
    calls = []

    async def query_all(entity, where=""):
        calls.append((entity, where))
        return responses[entity]
    monkeypatch.setattr(qbo, "_query_all", query_all)
    return calls


@pytest.mark.asyncio
async def test_open_invoices_query_is_balance_filtered_and_keeps_currency(qbo, monkeypatch):
    calls = _feed(qbo, monkeypatch, {"Invoice": [
        {"Id": "9", "DocNumber": "1009", "TxnDate": "2026-01-05", "DueDate": "2026-02-04",
         "TotalAmt": "120.50", "Balance": "20.50", "CustomerRef": {"value": "C1", "name": "A"},
         "CurrencyRef": {"value": "CAD"}, "Line": []}]})
    rows = await QBOnlineBIReads(qbo).open_invoices()
    assert calls == [("Invoice", "Balance > '0'")]
    assert rows[0]["balance"] == D("20.50") and rows[0]["currency"] == "CAD" and rows[0]["doc_number"] == "1009"


@pytest.mark.asyncio
async def test_open_invoice_query_returning_closed_invoice_fails(qbo, monkeypatch):
    _feed(qbo, monkeypatch, {"Invoice": [
        {"Id": "9", "TxnDate": "2026-01-05", "TotalAmt": "1", "Balance": "0", "CustomerRef": {"value": "C1"}}]})
    with pytest.raises(ValueError):
        await QBOnlineBIReads(qbo).open_invoices()


@pytest.mark.asyncio
async def test_payment_allocations_are_parsed_exactly(qbo, monkeypatch):
    _feed(qbo, monkeypatch, {"Payment": [{
        "Id": "P1", "TxnDate": "2026-03-01", "TotalAmt": "300.00", "UnappliedAmt": "50.00",
        "CustomerRef": {"value": "C1"}, "CurrencyRef": {"value": "USD"},
        "Line": [{"Amount": "200.00", "LinkedTxn": [{"TxnId": "I1", "TxnType": "Invoice"}]},
                 {"Amount": "50.00", "LinkedTxn": [{"TxnId": "CM1", "TxnType": "CreditMemo"}]}]}]})
    [p] = await QBOnlineBIReads(qbo).payments_between(date(2026, 3, 1), date(2026, 3, 31))
    assert p["applied"] == [{"invoice_id": "I1", "amount": D("200.00")}]
    assert p["other_links"][0]["txn_type"] == "CreditMemo"
    assert p["unapplied"] == D("50.00") and p["total"] == D("300.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("payment", [
    {"Id": "P1", "TxnDate": "2026-03-01", "CustomerRef": {"value": "C1"}},                     # no TotalAmt
    {"Id": "P1", "TxnDate": "2026-04-01", "TotalAmt": "1", "CustomerRef": {"value": "C1"}},    # outside window
    {"Id": "P1", "TxnDate": "2026-03-01", "TotalAmt": "1", "CustomerRef": {"value": "C1"},
     "Line": [{"Amount": "1"}]},                                                               # unlinked line
    {"Id": "P1", "TxnDate": "2026-03-01", "TotalAmt": "NaN", "CustomerRef": {"value": "C1"}},
])
async def test_malformed_payments_fail_closed(qbo, monkeypatch, payment):
    _feed(qbo, monkeypatch, {"Payment": [payment]})
    with pytest.raises(ValueError):
        await QBOnlineBIReads(qbo).payments_between(date(2026, 3, 1), date(2026, 3, 31))


@pytest.mark.asyncio
async def test_bills_and_bank_accounts(qbo, monkeypatch):
    _feed(qbo, monkeypatch, {
        "Bill": [{"Id": "B1", "VendorRef": {"value": "V1", "name": "Vend"}, "DocNumber": "X1",
                  "TxnDate": "2026-01-01", "DueDate": "2026-01-31", "TotalAmt": "10.00", "Balance": "4.00"}],
        "Account": [{"Id": "A1", "Name": "Op", "AccountType": "Bank", "CurrentBalance": "-12.34",
                     "CurrencyRef": {"value": "USD"}},
                    {"Id": "A2", "Name": "Closed", "AccountType": "Bank", "CurrentBalance": "5", "Active": False}]})
    reads = QBOnlineBIReads(qbo)
    [bill] = await reads.open_bills()
    assert bill["balance"] == D("4.00") and bill["due_date"] == date(2026, 1, 31)
    banks = await reads.bank_accounts()
    assert [b["id"] for b in banks] == ["A1"] and banks[0]["balance"] == D("-12.34")


@pytest.mark.asyncio
async def test_bill_balance_above_total_and_missing_bank_balance_fail(qbo, monkeypatch):
    _feed(qbo, monkeypatch, {"Bill": [{"Id": "B1", "VendorRef": {"value": "V1"}, "TxnDate": "2026-01-01",
                                       "TotalAmt": "1", "Balance": "2"}],
                             "Account": [{"Id": "A1", "AccountType": "Bank"}]})
    with pytest.raises(ValueError):
        await QBOnlineBIReads(qbo).open_bills()
    with pytest.raises(ValueError):
        await QBOnlineBIReads(qbo).bank_accounts()


@pytest.mark.asyncio
async def test_home_currency_from_preferences(qbo, monkeypatch):
    async def query(_sql):
        return {"QueryResponse": {"Preferences": [{"CurrencyPrefs": {
            "MultiCurrencyEnabled": False, "HomeCurrency": {"value": "USD"}}}]}}
    monkeypatch.setattr(qbo, "_query", query)
    assert await QBOnlineBIReads(qbo).company_currency() == {
        "home_currency": "USD", "multicurrency_enabled": False, "source": "qbo_preferences"}


def _report(start="2026-01-01", end="2026-01-31", rows=None):
    def row(group, value):
        return {"group": group, "Summary": {"ColData": [{"value": group}, {"value": value}]}}
    return {"Header": {"StartPeriod": start, "EndPeriod": end, "Currency": "USD", "ReportBasis": "Accrual"},
            "Rows": {"Row": rows if rows is not None else [
                row("Income", "1000.00"), row("COGS", "600.00"), row("GrossProfit", "400.00"),
                row("Expenses", "250.00"), row("NetOperatingIncome", "150.00"), row("NetIncome", "150.00")]}}


def test_profit_and_loss_parses_section_totals():
    pnl = parse_profit_and_loss(_report(), date(2026, 1, 1), date(2026, 1, 31))
    assert pnl["income"] == D("1000.00") and pnl["net_income"] == D("150.00")
    assert pnl["other_income"] is None  # not reported ≠ zero


@pytest.mark.parametrize("report", [
    _report(start="2025-12-01"),                    # wrong period
    _report(rows=[]),                               # no net income
    {"Header": {}},                                 # malformed
    _report(rows=[{"group": "NetIncome", "Summary": {"ColData": [{"value": "x"}]}}]),   # wrong column count
    _report(rows=[{"group": "NetIncome", "Summary": {"ColData": [{"value": "x"}, {"value": "abc"}]}}]),
])
def test_profit_and_loss_rejects_mismatched_or_malformed_reports(report):
    with pytest.raises(ValueError):
        parse_profit_and_loss(report, date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.asyncio
async def test_products_derive_brand_from_item_hierarchy_and_keep_missing_cost_unknown(qbo, monkeypatch):
    _feed(qbo, monkeypatch, {"Item": [
        {"Id": "1", "Name": "Rum", "FullyQualifiedName": "Lion:Rum", "QtyOnHand": "5", "PurchaseCost": "3.10",
         "UnitPrice": "6"},
        {"Id": "2", "Name": "Gin", "FullyQualifiedName": "Gin", "QtyOnHand": "0"}]})
    rum, gin = await QBOnlineBIReads(qbo).products()
    assert rum["brand"] == "Lion" and rum["cost"] == D("3.10")
    assert gin["brand"] is None and gin["cost"] is None and gin["list_price"] is None


@pytest.mark.asyncio
async def test_desktop_reports_unsupported_pnl_and_unverified_mapping():
    reads = QBDesktopBIReads(object(), {"home_currency": "USD", "qbd_multicurrency_enabled": False})
    assert reads.mapping_quality == "unverified_mapping"
    with pytest.raises(SourceNotSupported):
        await reads.profit_and_loss(date(2026, 1, 1), date(2026, 1, 31))
    assert (await reads.company_currency())["source"] == "tenant_settings"


def test_bi_reads_module_contains_no_write_calls():
    source = inspect.getsource(qb_bi_reads)
    for forbidden in ("_post(", "_patch(", "create_", "update_invoice", ".post(", ".put(", ".delete("):
        assert forbidden not in source, forbidden
