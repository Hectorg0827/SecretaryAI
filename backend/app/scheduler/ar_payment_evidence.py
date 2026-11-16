"""Pure, read-only AR cash-application comparison for synthetic staging acceptance.

This module has no provider, database, authorization or execution dependencies.
A future QBO-sandbox caller must independently verify complete, company-scoped
invoice/payment feeds and normalize provider links before calling it. The result
is a comparison only: credits, refunds, fees and journal adjustments may explain
a detected difference. It never posts or authorizes a payment.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_DOCUMENTS = 5000
MAX_MONEY = Decimal("999999999999.99")
CENT = Decimal("0.01")


class ARPaymentEvidenceInvalid(ValueError):
    """Source records cannot support a trustworthy AR comparison."""


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ARPaymentEvidenceInvalid(f"Invalid {name}")
    return value


def _money(value: Any, name: str) -> Decimal:
    # Binary floats may have been rounded before we received them.
    if value is None or isinstance(value, (bool, float)):
        raise ARPaymentEvidenceInvalid(f"Unverified {name}")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ARPaymentEvidenceInvalid(f"Invalid {name}") from exc
    if (not amount.is_finite() or amount < 0 or amount > MAX_MONEY
            or amount != amount.quantize(CENT)):
        raise ARPaymentEvidenceInvalid(f"Invalid {name}")
    return amount


def _currency(value: Any) -> str:
    if (not isinstance(value, str) or len(value) != 3
            or not value.isascii() or not value.isalpha() or not value.isupper()):
        raise ARPaymentEvidenceInvalid("Unverified source currency")
    return value


def _transaction_date(value: Any, as_of: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime) or value > as_of:
        raise ARPaymentEvidenceInvalid("Invalid or future-dated transaction")
    return value


def _company(row: dict, expected: str) -> None:
    if row.get("company_id") != expected:
        raise ARPaymentEvidenceInvalid("AR evidence tenant mismatch")


def _unassessed() -> dict:
    return {
        "status": "not_assessed",
        "reason": "complete_source_coverage_unverified",
        "invoice_count": None,
        "linked_invoice_count": None,
        "variance_invoice_count": None,
        "partially_paid_invoice_count": None,
        "unapplied_payment_count": None,
        "accounting_reconciled": False,
        "financial_write_authorized": False,
    }



def assert_stable_ar_source_view(
    earlier: list[dict], later: list[dict], *, source: str,
) -> None:
    """Fail closed when two complete bounded provider reads disagree.

    This is best-effort source-drift detection, NOT an atomic QBO snapshot.
    Compare normalized records by unique ID, ignoring harmless page order.
    Never print provider identifiers, customer data or financial amounts.
    """
    if source not in {"invoice", "payment"}:
        raise ARPaymentEvidenceInvalid("Unsupported AR source comparison")

    def index(rows: list[dict]) -> dict[str, dict]:
        if not isinstance(rows, list) or len(rows) > MAX_DOCUMENTS:
            raise ARPaymentEvidenceInvalid("AR source comparison unbounded")
        keyed: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ARPaymentEvidenceInvalid("AR source comparison malformed")
            record_id = _identifier(row.get("id"), "AR source ID")
            if record_id in keyed:
                raise ARPaymentEvidenceInvalid("AR source comparison duplicate ID")
            if source == "payment":
                links = row.get("allocations")
                if not isinstance(links, list) or len(links) > MAX_DOCUMENTS:
                    raise ARPaymentEvidenceInvalid("AR source allocations invalid")
                canonical = []
                for link in links:
                    if (not isinstance(link, dict)
                            or set(link) != {"invoice_id", "amount"}):
                        raise ARPaymentEvidenceInvalid("AR source allocation malformed")
                    canonical.append((
                        _identifier(link["invoice_id"], "linked invoice ID"),
                        _money(link["amount"], "linked allocation"),
                    ))
                # QBO does not guarantee Line order across independent reads;
                # changed amounts/IDs must still be detected, but permutation
                # of the same linked payments is not source drift.
                keyed[record_id] = {
                    **row, "allocations": tuple(sorted(canonical)),
                }
            else:
                keyed[record_id] = row
        return keyed

    first = index(earlier)
    second = index(later)
    if first != second:
        raise ARPaymentEvidenceInvalid("AR source changed during comparison")


def compare_ar_payment_allocations(
    invoices: list[dict],
    payments: list[dict],
    *,
    company_id: str,
    as_of: date,
    invoices_complete: bool,
    payments_complete: bool,
) -> dict:
    """Compare exact cent allocations with observed invoice balance deltas.

    Required normalized invoice fields:
      id, company_id, customer_id, currency, total, balance, txn_date.
    Required normalized payment fields:
      id, company_id, customer_id, currency, total, unapplied, txn_date,
      allocations: [{invoice_id, amount}].

    Caller must prove *both* pagination streams complete and scoped to this
    company before passing True for coverage. In particular, a missing payment
    feed is not evidence that an unpaid invoice has received zero payments.
    No identifiers, financial amounts or customer names leave this function.
    """
    _identifier(company_id, "company identifier")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ARPaymentEvidenceInvalid("Invalid comparison date")
    if invoices_complete is not True or payments_complete is not True:
        return _unassessed()
    if (not isinstance(invoices, list) or not isinstance(payments, list)
            or len(invoices) > MAX_DOCUMENTS or len(payments) > MAX_DOCUMENTS):
        raise ARPaymentEvidenceInvalid("AR source is missing or unbounded")

    invoice_by_id: dict[str, tuple[str, str, Decimal, Decimal]] = {}
    for invoice in invoices:
        if not isinstance(invoice, dict):
            raise ARPaymentEvidenceInvalid("Invoice header malformed")
        _company(invoice, company_id)
        identifier = _identifier(invoice.get("id"), "invoice ID")
        if identifier in invoice_by_id:
            raise ARPaymentEvidenceInvalid("Duplicate invoice ID")
        customer = _identifier(invoice.get("customer_id"), "customer ID")
        currency = _currency(invoice.get("currency"))
        total = _money(invoice.get("total"), "invoice total")
        balance = _money(invoice.get("balance"), "invoice balance")
        _transaction_date(invoice.get("txn_date"), as_of)
        if balance > total:
            raise ARPaymentEvidenceInvalid("Invoice balance exceeds total")
        invoice_by_id[identifier] = (customer, currency, total, balance)

    allocations: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    linked_ids: set[str] = set()
    seen_payments: set[str] = set()
    unapplied_payment_count = 0
    links_examined = 0
    for payment in payments:
        if not isinstance(payment, dict):
            raise ARPaymentEvidenceInvalid("Payment header malformed")
        _company(payment, company_id)
        identifier = _identifier(payment.get("id"), "payment ID")
        if identifier in seen_payments:
            raise ARPaymentEvidenceInvalid("Duplicate payment ID")
        seen_payments.add(identifier)
        customer = _identifier(payment.get("customer_id"), "customer ID")
        currency = _currency(payment.get("currency"))
        total = _money(payment.get("total"), "payment total")
        unapplied = _money(payment.get("unapplied"), "unapplied payment amount")
        _transaction_date(payment.get("txn_date"), as_of)
        raw_links = payment.get("allocations")
        if not isinstance(raw_links, list):
            raise ARPaymentEvidenceInvalid("Payment allocations unobserved")
        links_examined += len(raw_links)
        if len(raw_links) > MAX_DOCUMENTS or links_examined > MAX_DOCUMENTS:
            raise ARPaymentEvidenceInvalid("Payment allocation feed unbounded")
        allocated = Decimal("0")
        seen_links: set[str] = set()
        for link in raw_links:
            if not isinstance(link, dict):
                raise ARPaymentEvidenceInvalid("Payment allocation malformed")
            invoice_id = _identifier(link.get("invoice_id"), "linked invoice")
            if invoice_id in seen_links:
                raise ARPaymentEvidenceInvalid("Duplicate payment-to-invoice allocation")
            seen_links.add(invoice_id)
            if invoice_id not in invoice_by_id:
                raise ARPaymentEvidenceInvalid("Payment links to unknown invoice")
            invoice_customer, invoice_currency, _, _ = invoice_by_id[invoice_id]
            if customer != invoice_customer or currency != invoice_currency:
                raise ARPaymentEvidenceInvalid("Payment customer or currency mismatch")
            amount = _money(link.get("amount"), "applied payment amount")
            if amount == 0:
                raise ARPaymentEvidenceInvalid("Zero payment link not verified")
            allocated += amount
            allocations[invoice_id] += amount
            linked_ids.add(invoice_id)
        if allocated + unapplied != total:
            raise ARPaymentEvidenceInvalid("Payment allocations do not balance")
        if unapplied > 0:
            unapplied_payment_count += 1

    differences = 0
    partial = 0
    for invoice_id, (_, _, total, balance) in invoice_by_id.items():
        # A credit memo or write-off can reduce the balance without any
        # cash allocation. Only count an actual partially applied payment.
        if total > balance > 0 and allocations[invoice_id] > 0:
            partial += 1
        # Multiple distinct payment IDs can still overapply a single invoice.
        # A duplicate/replayed allocation must not become a soft variance.
        if allocations[invoice_id] > total:
            raise ARPaymentEvidenceInvalid("Invoice allocations exceed total")
        if total - balance != allocations[invoice_id]:
            differences += 1

    return {
        "status": "variance_detected" if differences else "aligned_not_certified",
        "reason": "other_adjustments_or_missing_links_possible" if differences else None,
        "invoice_count": len(invoice_by_id),
        "linked_invoice_count": len(linked_ids),
        "variance_invoice_count": differences,
        "partially_paid_invoice_count": partial,
        "unapplied_payment_count": unapplied_payment_count,
        "accounting_reconciled": False,
        "financial_write_authorized": False,
    }
