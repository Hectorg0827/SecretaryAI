"""Demand projections must never count draft or removed QuickBooks invoices."""
from datetime import date

from app.api.insights._supply_chain_sources import ItemIndex, clean_invoices, sales_by_item


def test_only_real_invoice_states_affect_supply_chain_demand():
    today = date(2026, 11, 2)
    rows = [
        {
            "status": status,
            "date": "2026-11-01",
            "total": 100,
            "customer_id": status,
            "line_items": [{"item_id": "item-1", "quantity": 5, "unit_price": 20}],
        }
        for status in ("draft", "deleted", "void", "refunded", "open", "paid")
    ]
    invoices, skipped = clean_invoices(rows, today=today, days=30)
    assert skipped == 0
    assert [invoice.customer_id for invoice in invoices] == ["open", "paid"]
    sales, invalid_lines = sales_by_item(invoices, ItemIndex([{"qb_id": "item-1"}]))
    assert invalid_lines == 0
    assert sum(line.quantity for line in sales[0]) == 10
