"""Strict, GET-only QuickBooks Online AR header normalizers.

These functions translate provider records into the *offline* comparator's
contract, without persisting data or executing any financial action. QBO JSON
numbers are parsed through Decimal(str(...)) and validated to cents; this is
a bounded comparison, not an audited or immutable accounting snapshot.

A missing CurrencyRef, unsupported payment link (including CreditMemo), or
unknown allocation amount is NOT automatically USD or an invoice payment.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

MAX_RECORDS = 5000
MAX_MONEY = Decimal("999999999999.99")
CENT = Decimal("0.01")


class QBOARSourceInvalid(ValueError):
    """Provider response cannot support a trustworthy cash application preview."""


def _identifier(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        raise QBOARSourceInvalid(f"QBO {field} is not verified")
    return raw


def _reference(raw: object, field: str) -> str:
    if not isinstance(raw, dict):
        raise QBOARSourceInvalid(f"QBO {field} is not verified")
    return _identifier(raw.get("value"), field)


def _currency(raw: object) -> str:
    value = _reference(raw, "currency reference")
    if (len(value) != 3 or not value.isascii()
            or not value.isupper() or not value.isalpha()):
        raise QBOARSourceInvalid("QBO currency is not verified")
    return value


def _amount(raw: object, field: str) -> str:
    if raw is None or isinstance(raw, bool):
        raise QBOARSourceInvalid(f"QBO {field} is not verified")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise QBOARSourceInvalid(f"QBO {field} is invalid") from exc
    if (not value.is_finite() or value < 0 or value > MAX_MONEY
            or value != value.quantize(CENT)):
        raise QBOARSourceInvalid(f"QBO {field} is invalid")
    return str(value.quantize(CENT))


def _date(raw: object) -> date:
    if not isinstance(raw, str):
        raise QBOARSourceInvalid("QBO transaction date not verified")
    try:
        d = date.fromisoformat(raw)
    except ValueError as exc:
        raise QBOARSourceInvalid("QBO transaction date invalid") from exc
    if d.isoformat() != raw:
        raise QBOARSourceInvalid("QBO transaction date not canonical")
    return d


def normalize_invoice_for_ar(raw: dict) -> dict:
    """Require explicit invoice amounts, date, customer and source currency."""
    if not isinstance(raw, dict):
        raise QBOARSourceInvalid("QBO invoice record invalid")
    invoice_id = _identifier(raw.get("Id"), "invoice ID")
    customer_id = _reference(raw.get("CustomerRef"), "invoice customer")
    currency = _currency(raw.get("CurrencyRef"))
    total = _amount(raw.get("TotalAmt"), "invoice total")
    balance = _amount(raw.get("Balance"), "invoice balance")
    if Decimal(balance) > Decimal(total):
        raise QBOARSourceInvalid("QBO invoice balance exceeds total")
    return {
        "id": invoice_id, "customer_id": customer_id, "currency": currency,
        "txn_date": _date(raw.get("TxnDate")),
        "total": total, "balance": balance,
    }


def normalize_payment_for_ar(raw: dict) -> dict:
    """Accept only unambiguous Invoice lines; never infer credit-memo cash."""
    if not isinstance(raw, dict):
        raise QBOARSourceInvalid("QBO payment record invalid")
    payment_id = _identifier(raw.get("Id"), "payment ID")
    customer_id = _reference(raw.get("CustomerRef"), "payment customer")
    currency = _currency(raw.get("CurrencyRef"))
    total = _amount(raw.get("TotalAmt"), "payment total")
    unapplied = _amount(raw.get("UnappliedAmt"), "unapplied amount")
    when = _date(raw.get("TxnDate"))
    lines = raw.get("Line")
    if lines is None and Decimal(total) == Decimal(unapplied):
        lines = []
    if not isinstance(lines, list) or len(lines) > MAX_RECORDS:
        raise QBOARSourceInvalid("QBO payment lines incomplete or unbounded")

    allocations: list[dict] = []
    for line in lines:
        if not isinstance(line, dict):
            raise QBOARSourceInvalid("QBO payment line invalid")
        linked = line.get("LinkedTxn")
        if not isinstance(linked, list) or len(linked) != 1:
            # A line referencing an Invoice plus a CreditMemo cannot be
            # allocated unambiguously without separate credit accounting.
            raise QBOARSourceInvalid("QBO payment linked transaction unsupported")
        item = linked[0]
        if not isinstance(item, dict) or item.get("TxnType") != "Invoice":
            raise QBOARSourceInvalid("QBO payment adjustment unsupported")
        invoice_id = _identifier(item.get("TxnId"), "linked invoice ID")
        line_amount = _amount(line.get("Amount"), "payment line amount")
        if Decimal(line_amount) == 0:
            raise QBOARSourceInvalid("QBO zero-valued allocation not verified")
        allocations.append({"invoice_id": invoice_id, "amount": line_amount})

    applied_total = sum((Decimal(x["amount"]) for x in allocations), Decimal("0"))
    if applied_total + Decimal(unapplied) != Decimal(total):
        raise QBOARSourceInvalid("QBO payment line totals do not balance")
    return {
        "id": payment_id, "customer_id": customer_id,
        "currency": currency, "txn_date": when,
        "total": total, "unapplied": unapplied, "allocations": allocations,
    }
