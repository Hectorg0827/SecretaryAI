"""Synthetic offline AR payment comparison tests.

Do not call any QBO API, data store or payment executor. Assertions prove
fail-closed behavior only; they do not certify production financial records.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.scheduler.ar_payment_evidence import (
    ARPaymentEvidenceInvalid, compare_ar_payment_allocations,
)

DAY = date(2026, 11, 15)
COMPANY = "tenant-fixture"


def invoice(identifier="inv-a", *, balance="40.00", total="100.00",
            company=COMPANY, customer="customer-1", currency="USD"):
    return {
        "id": identifier, "company_id": company,
        "customer_id": customer, "currency": currency,
        "total": total, "balance": balance, "txn_date": DAY,
    }


def payment(identifier="payment-a", *, amount="60.00", unapplied="0.00",
            links=None, company=COMPANY, customer="customer-1", currency="USD"):
    if links is None:
        links = [{"invoice_id": "inv-a", "amount": "60.00"}]
    return {
        "id": identifier, "company_id": company, "customer_id": customer,
        "currency": currency, "total": amount, "unapplied": unapplied,
        "txn_date": DAY, "allocations": links,
    }


def compare(invoices=None, payments=None, **overrides):
    options = {
        "company_id": COMPANY, "as_of": DAY,
        "invoices_complete": True, "payments_complete": True,
    }
    options.update(overrides)
    return compare_ar_payment_allocations(
        [invoice()] if invoices is None else invoices,
        [payment()] if payments is None else payments,
        **options,
    )


def test_partial_payment_matches_observed_balance_without_claiming_reconciliation():
    result = compare()
    assert result == {
        "status": "aligned_not_certified", "reason": None,
        "invoice_count": 1, "linked_invoice_count": 1,
        "variance_invoice_count": 0, "partially_paid_invoice_count": 1,
        "unapplied_payment_count": 0,
        "accounting_reconciled": False, "financial_write_authorized": False,
    }
    assert "tenant-fixture" not in str(result)
    assert "customer-1" not in str(result)
    assert "100.00" not in str(result)


def test_split_application_across_two_invoices_and_unapplied_cash():
    docs = [invoice("inv-a"), invoice("inv-b", balance="20.00", total="50.00")]
    cash = [payment(
        amount="100.00", unapplied="10.00",
        links=[{"invoice_id": "inv-a", "amount": "60.00"},
               {"invoice_id": "inv-b", "amount": "30.00"}],
    )]
    result = compare(docs, cash)
    assert result["status"] == "aligned_not_certified"
    assert result["linked_invoice_count"] == 2
    assert result["partially_paid_invoice_count"] == 2
    assert result["unapplied_payment_count"] == 1


def test_unapplied_cash_is_not_assumed_to_have_reduced_invoice_balance():
    result = compare(
        [invoice(balance="100.00")],
        [payment(amount="60.00", unapplied="60.00", links=[])],
    )
    assert result["status"] == "aligned_not_certified"
    assert result["linked_invoice_count"] == 0
    assert result["unapplied_payment_count"] == 1


def test_missing_provider_coverage_cannot_be_presented_as_zero_receipts():
    for overrides in [
        {"payments_complete": False},
        {"invoices_complete": False},
        {"payments_complete": None},
        {"payments_complete": 1},
    ]:
        result = compare(invoices=[], payments=[], **overrides)
        assert result["status"] == "not_assessed"
        assert result["invoice_count"] is None
        assert result["variance_invoice_count"] is None
        assert result["accounting_reconciled"] is False


def test_writeoff_or_credit_without_payment_link_is_a_variance_not_a_fake_payment():
    result = compare([invoice(balance="30.00")], [payment()])
    assert result["status"] == "variance_detected"
    assert result["variance_invoice_count"] == 1
    assert result["accounting_reconciled"] is False


@pytest.mark.parametrize("bad_documents", [
    [invoice(), invoice()],  # duplicate invoice ID
    [invoice(company="tenant-other")],
    [invoice(total="99.00", balance="100.00")],
    [invoice(balance="NaN")],
    [invoice(total=100.0)],
    [invoice(currency="usd")],
    [invoice(balance="40.001")],
    [invoice(customer="")],
    [dict(invoice(), txn_date=DAY.replace(day=16))],
    [dict(invoice(), txn_date=datetime(2026, 11, 15, tzinfo=timezone.utc))],
])
def test_bad_invoice_sources_fail_closed(bad_documents):
    with pytest.raises(ARPaymentEvidenceInvalid):
        compare(bad_documents, [payment()])


@pytest.mark.parametrize("bad_payments", [
    [payment(), payment()],  # repeated payment replay cannot be counted twice
    [payment(company="tenant-other")],
    [payment(currency="EUR")],
    [payment(customer="customer-2")],
    [payment(unapplied="1.00")],  # doesn't balance
    [payment(amount="40.00")],  # allocation exceeds payment
    [payment(amount="60.001")],
    [payment(amount=float("nan"))],
    [payment(amount=Decimal("Infinity"))],
    [payment(links=None), dict(payment(), id="payment-b")],  # same invoice allowed across distinct payments -> overapplied
    [payment(links=[{"invoice_id": "inv-other", "amount": "60.00"}])],
    [payment(links=[{"invoice_id": "inv-a", "amount": "30.00"},
                    {"invoice_id": "inv-a", "amount": "30.00"}])],
    [dict(payment(), allocations=None)],
    [dict(payment(), txn_date=DAY.replace(day=16))],
])
def test_bad_payment_sources_fail_closed(bad_payments):
    with pytest.raises(ARPaymentEvidenceInvalid):
        compare([invoice()], bad_payments)


def test_multiple_distinct_payments_to_same_invoice_are_counted_once():
    result = compare(
        [invoice(balance="40.00")],
        [payment("payment-a", amount="40.00",
                 links=[{"invoice_id": "inv-a", "amount": "40.00"}]),
         payment("payment-b", amount="20.00",
                 links=[{"invoice_id": "inv-a", "amount": "20.00"}])],
    )
    assert result["status"] == "aligned_not_certified"
    assert result["linked_invoice_count"] == 1


@pytest.mark.parametrize("docs,cash", [
    (None, []),
    ([], None),
    ([invoice()] * 5001, []),
    ([], [payment()] * 5001),
])
def test_unbounded_or_missing_source_fails_closed(docs, cash):
    with pytest.raises(ARPaymentEvidenceInvalid):
        compare_ar_payment_allocations(
            docs, cash, company_id=COMPANY, as_of=DAY,
            invoices_complete=True, payments_complete=True,
        )


def test_unbounded_nested_payment_links_fail_closed_before_allocating():
    oversized = [{"invoice_id": "inv-a", "amount": "0.01"}] * 5001
    with pytest.raises(ARPaymentEvidenceInvalid, match="unbounded"):
        compare([invoice()], [payment(links=oversized)])


def test_unverified_sources_do_not_resolve_a_partial_ledger_without_evidence():
    # A provider outage is neither zero cash nor an accurately matched balance.
    result = compare([invoice()], [], payments_complete=False)
    assert result["status"] == "not_assessed"
    assert result["unapplied_payment_count"] is None
