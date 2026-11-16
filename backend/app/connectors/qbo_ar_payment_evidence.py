"""Strict, GET-only QuickBooks Online AR header normalizers.

These functions translate provider records into the *offline* comparator's
contract, without persisting data or executing any financial action. QBO JSON
numbers are parsed through Decimal(str(...)) and validated to cents; this is
a bounded comparison, not an audited or immutable accounting snapshot.

A missing CurrencyRef can inherit a verified company HomeCurrency only when
the provider confirms multicurrency is disabled. Unsupported payment links
(including CreditMemo) and missing amounts still fail closed.
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


def _currency_code(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 3
            or not value.isascii() or not value.isupper() or not value.isalpha()):
        raise QBOARSourceInvalid("QBO currency is not verified")
    return value


def normalize_currency_preferences(raw: dict) -> dict:
    """Accept only provider-evidenced company currency settings, never USD by default."""
    if not isinstance(raw, dict):
        raise QBOARSourceInvalid("QBO preferences not verified")
    prefs = raw.get("CurrencyPrefs")
    if not isinstance(prefs, dict):
        raise QBOARSourceInvalid("QBO currency preferences missing")
    enabled = prefs.get("MultiCurrencyEnabled")
    if isinstance(enabled, bool):
        pass
    elif enabled in ("true", "false"):
        enabled = enabled == "true"
    else:
        raise QBOARSourceInvalid("QBO multicurrency state not verified")
    home = _currency_code(_reference(prefs.get("HomeCurrency"), "home currency"))
    return {"multicurrency_enabled": enabled, "home_currency": home}


def _currency(raw: object, currency_context: dict | None = None) -> str:
    if currency_context is not None:
        if (not isinstance(currency_context, dict)
                or type(currency_context.get("multicurrency_enabled")) is not bool):
            raise QBOARSourceInvalid("QBO currency context invalid")
        home = _currency_code(currency_context.get("home_currency"))
    if raw is None:
        if currency_context is not None and not currency_context["multicurrency_enabled"]:
            return home
        raise QBOARSourceInvalid("QBO missing transaction currency")
    value = _currency_code(_reference(raw, "currency reference"))
    if (currency_context is not None
            and not currency_context["multicurrency_enabled"]
            and value != home):
        raise QBOARSourceInvalid("QBO single-currency transaction mismatch")
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


def normalize_invoice_for_ar(
    raw: dict, *, currency_context: dict | None = None,
) -> dict:
    """Require source invoice evidence and verified currency provenance."""
    if not isinstance(raw, dict):
        raise QBOARSourceInvalid("QBO invoice record invalid")
    invoice_id = _identifier(raw.get("Id"), "invoice ID")
    customer_id = _reference(raw.get("CustomerRef"), "invoice customer")
    currency = _currency(raw.get("CurrencyRef"), currency_context)
    total = _amount(raw.get("TotalAmt"), "invoice total")
    balance = _amount(raw.get("Balance"), "invoice balance")
    if Decimal(balance) > Decimal(total):
        raise QBOARSourceInvalid("QBO invoice balance exceeds total")
    return {
        "id": invoice_id, "customer_id": customer_id, "currency": currency,
        "txn_date": _date(raw.get("TxnDate")),
        "total": total, "balance": balance,
    }


def normalize_payment_for_ar(
    raw: dict, *, currency_context: dict | None = None,
) -> dict:
    """Accept only unambiguous Invoice lines; never infer credit-memo cash."""
    if not isinstance(raw, dict):
        raise QBOARSourceInvalid("QBO payment record invalid")
    payment_id = _identifier(raw.get("Id"), "payment ID")
    customer_id = _reference(raw.get("CustomerRef"), "payment customer")
    currency = _currency(raw.get("CurrencyRef"), currency_context)
    total = _amount(raw.get("TotalAmt"), "payment total")
    # Intuit makes UnappliedAmt optional. It is safe to establish a zero
    # remainder only for a non-sparse, nonempty, invoice-only allocation set
    # whose verified line amounts exactly exhaust TotalAmt. All other
    # missing-field cases remain unassessed, not zero.
    missing_unapplied = raw.get("UnappliedAmt") is None
    unapplied = (
        None if missing_unapplied
        else _amount(raw["UnappliedAmt"], "unapplied amount")
    )
    when = _date(raw.get("TxnDate"))
    lines = raw.get("Line")
    if (lines is None and unapplied is not None
            and Decimal(total) == Decimal(unapplied)):
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
    if missing_unapplied:
        if (not allocations or Decimal(total) == 0
                or applied_total != Decimal(total)
                or (raw.get("sparse") is not None
                    and raw.get("sparse") is not False)):
            raise QBOARSourceInvalid("QBO unapplied amount cannot be verified")
        # Derived only from a complete, exactly balanced explicit application:
        # no fabricated unapplied cash or duplicate payment recognition.
        unapplied = "0.00"
    if applied_total + Decimal(unapplied) != Decimal(total):
        raise QBOARSourceInvalid("QBO payment line totals do not balance")
    return {
        "id": payment_id, "customer_id": customer_id,
        "currency": currency, "txn_date": when,
        "total": total, "unapplied": unapplied, "allocations": allocations,
    }
