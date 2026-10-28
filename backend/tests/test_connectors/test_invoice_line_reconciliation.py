"""Strict offline invoice-line normalization contracts for QBO and Desktop.

Fixture shapes are internal test expectations, NOT vendor-backed acceptance.
No connector network or write methods are called.
"""

from decimal import Decimal

import pytest

from app.connectors.qb_desktop import QBDesktopAdapter
from app.connectors.qb_online import QBOnlineAdapter


def qbo():
    return QBOnlineAdapter("fixture-client", "fixture-secret", "fixture-access", "fixture-refresh", "fixture-realm")


def desktop():
    return QBDesktopAdapter("fixture-key", "fixture-user")


def qbo_line(**changes):
    line = {
        "Id": "line-1", "DetailType": "SalesItemLineDetail", "Amount": "25.00",
        "SalesItemLineDetail": {
            "ItemRef": {"value": "item-1", "name": "Fixture Item"},
            "Qty": "5", "UnitPrice": "5.00",
        },
    }
    line.update(changes)
    return line


def qbo_invoice(**changes):
    invoice = {
        "Id": "qbo-inv-1", "TxnDate": "2026-10-01", "DueDate": "2026-10-31",
        "CustomerRef": {"value": "customer-1", "name": "Fixture Customer"},
        "TotalAmt": "25.00", "Balance": "20.00", "Line": [qbo_line()],
    }
    invoice.update(changes)
    return invoice


def desktop_line(**changes):
    line = {
        "id": "line-1", "itemId": "item-1", "name": "Fixture Item",
        "quantity": "5", "amount": "25.00", "unitPrice": "5.00",
    }
    line.update(changes)
    return line


def desktop_invoice(**changes):
    invoice = {
        "id": "desktop-inv-1", "transactionDate": "2026-10-01",
        "totalAmount": "25.00", "balanceRemaining": "20.00",
        "lineItems": [desktop_line()],
    }
    invoice.update(changes)
    return invoice


def test_qbo_valid_lines_preserve_exact_money_ids_and_quantity():
    invoice = qbo()._normalize_invoice(qbo_invoice())
    assert invoice.total == Decimal("25.00")
    assert invoice.balance == Decimal("20.00")
    assert invoice.line_items == [{
        "line_id": "line-1", "item_id": "item-1", "name": "Fixture Item",
        "description": "", "quantity": Decimal("5"),
        "unit_price": Decimal("5.00"), "amount": Decimal("25.00"),
    }]


@pytest.mark.parametrize("field", ["Amount", "DetailType", "SalesItemLineDetail"])
def test_qbo_missing_required_line_field_not_a_zero_or_skipped_sale(field):
    bad = qbo_line()
    del bad[field]
    with pytest.raises(ValueError, match="line"):
        qbo()._normalize_invoice(qbo_invoice(Line=[bad]))


@pytest.mark.parametrize("field", ["Qty", "ItemRef"])
def test_qbo_missing_nested_quantity_or_identity_is_not_a_sale_of_zero(field):
    bad = qbo_line()
    del bad["SalesItemLineDetail"][field]
    with pytest.raises(ValueError, match="line"):
        qbo()._normalize_invoice(qbo_invoice(Line=[bad]))


@pytest.mark.parametrize("field,value", [
    ("Amount", "NaN"), ("Amount", "Infinity"), ("Amount", "not money"),
    ("Qty", "NaN"), ("Qty", None), ("UnitPrice", "Infinity"),
])
def test_qbo_invalid_line_amount_quantity_or_price_fail_closed(field, value):
    bad = qbo_line()
    if field in ("Qty", "UnitPrice"):
        bad["SalesItemLineDetail"][field] = value
    else:
        bad[field] = value
    with pytest.raises(ValueError, match="line"):
        qbo()._normalize_invoice(qbo_invoice(Line=[bad]))


def test_qbo_absent_unit_price_is_unknown_not_verified_zero():
    line = qbo_line()
    del line["SalesItemLineDetail"]["UnitPrice"]
    result = qbo()._normalize_invoice(qbo_invoice(Line=[line]))
    assert result.line_items[0]["unit_price"] is None
    assert result.line_items[0]["amount"] == Decimal("25.00")


def test_qbo_signed_adjustment_preserved_not_clamped_or_assumed_positive():
    negative = qbo_line(Id="line-return", Amount="-5.00", SalesItemLineDetail={
        "ItemRef": {"value": "item-1"}, "Qty": "-1", "UnitPrice": "5.00",
    })
    invoice = qbo()._normalize_invoice(qbo_invoice(TotalAmt="20.00", Balance="20.00", Line=[qbo_line(), negative]))
    assert invoice.line_items[1]["quantity"] == Decimal("-1")
    assert invoice.line_items[1]["amount"] == Decimal("-5.00")


def test_qbo_explicit_non_sales_tax_line_is_not_mistaken_for_sold_item():
    tax_line = {"Id": "tax-1", "DetailType": "TaxLineDetail", "Amount": "2.00"}
    invoice = qbo()._normalize_invoice(qbo_invoice(TotalAmt="27.00", Balance="27.00", Line=[qbo_line(), tax_line]))
    assert len(invoice.line_items) == 1
    assert invoice.total == Decimal("27.00")


def test_qbo_corrupt_observed_tax_adjustment_cannot_pass_unnoticed():
    tax_line = {"Id": "tax-1", "DetailType": "TaxLineDetail", "Amount": "NaN"}
    with pytest.raises(ValueError, match="line"):
        qbo()._normalize_invoice(qbo_invoice(Line=[qbo_line(), tax_line]))


def test_qbo_unknown_line_type_not_silently_discarded():
    line = {"Id": "other", "DetailType": "FutureUnrecognizedType", "Amount": "2.00"}
    with pytest.raises(ValueError, match="DetailType"):
        qbo()._normalize_invoice(qbo_invoice(Line=[qbo_line(), line]))


def test_qbo_zero_value_item_is_preserved_as_real_zero():
    free_line = qbo_line(Amount="0", SalesItemLineDetail={
        "ItemRef": {"value": "item-1"}, "Qty": "0", "UnitPrice": "0",
    })
    invoice = qbo()._normalize_invoice(qbo_invoice(TotalAmt="0", Balance="0", Line=[free_line]))
    assert invoice.line_items[0]["quantity"] == Decimal("0")
    assert invoice.line_items[0]["amount"] == Decimal("0")


@pytest.mark.parametrize("field,value", [
    ("TotalAmt", "NaN"), ("TotalAmt", "Infinity"), ("TotalAmt", "-1"),
    ("Balance", "Infinity"), ("Balance", "-1"),
])
def test_qbo_nonfinite_or_negative_header_money_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        qbo()._normalize_invoice(qbo_invoice(**{field: value}))


def test_qbo_balance_cannot_exceed_invoice_total():
    with pytest.raises(ValueError, match="Balance"):
        qbo()._normalize_invoice(qbo_invoice(Balance="50.00"))


@pytest.mark.parametrize("connector,raw", [
    (qbo, qbo_invoice(Line=None)), (qbo, qbo_invoice(Line=[])),
    (desktop, desktop_invoice(lineItems=None)), (desktop, desktop_invoice(lineItems=[])),
])
def test_positive_invoice_with_absent_or_empty_line_feed_is_incomplete(connector, raw):
    with pytest.raises(ValueError, match="line"):
        connector()._normalize_invoice(raw)


def test_explicit_zero_invoices_can_have_no_sold_items():
    qi = qbo()._normalize_invoice(qbo_invoice(TotalAmt="0", Balance="0", Line=[]))
    di = desktop()._normalize_invoice(desktop_invoice(totalAmount="0", balanceRemaining="0", lineItems=[]))
    assert qi.total == di.total == Decimal("0")
    assert qi.line_items == di.line_items == []


def test_desktop_invoice_lines_mapped_to_normalized_contract():
    line = desktop()._normalize_invoice(desktop_invoice()).line_items[0]
    assert line["line_id"] == "line-1"
    assert line["item_id"] == "item-1"
    assert line["quantity"] == Decimal("5")
    assert line["amount"] == Decimal("25.00")


def test_desktop_nested_item_identity_and_missing_rate_are_preserved():
    raw = desktop_line()
    raw.pop("itemId")
    raw.pop("unitPrice")
    raw["item"] = {"id": "item-1", "name": "Nested Item"}
    line = desktop()._normalize_invoice(desktop_invoice(lineItems=[raw])).line_items[0]
    assert line["item_id"] == "item-1"
    assert line["unit_price"] is None


def test_desktop_balance_exceeding_total_is_not_verified_ar():
    with pytest.raises(ValueError, match="balanceRemaining"):
        desktop()._normalize_invoice(desktop_invoice(balanceRemaining="99"))


@pytest.mark.parametrize("field", ["itemId", "quantity", "amount"])
def test_desktop_missing_line_identity_or_money_rejected(field):
    bad = desktop_line()
    del bad[field]
    with pytest.raises(ValueError, match="line"):
        desktop()._normalize_invoice(desktop_invoice(lineItems=[bad]))


@pytest.mark.parametrize("field,value", [
    ("quantity", "NaN"), ("amount", "Infinity"), ("amount", None), ("unitPrice", "NaN"),
])
def test_desktop_invalid_line_money_or_quantity_rejected(field, value):
    with pytest.raises(ValueError, match="line"):
        desktop()._normalize_invoice(desktop_invoice(lineItems=[desktop_line(**{field: value})]))


def test_duplicate_provider_line_ids_rejected_without_double_counting():
    with pytest.raises(ValueError, match="duplicate"):
        qbo()._normalize_invoice(qbo_invoice(Line=[qbo_line(), qbo_line()]))
    with pytest.raises(ValueError, match="duplicate"):
        desktop()._normalize_invoice(desktop_invoice(lineItems=[desktop_line(), desktop_line()]))
