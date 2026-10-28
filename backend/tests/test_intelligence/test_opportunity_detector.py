"""Deterministic, provider-free regression cases for proactive recommendations."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.connectors.base import Customer, Invoice
from app.intelligence.opportunity_detector import detect_opportunities


def _customer() -> Customer:
    return Customer(
        id="internal-1", qb_id="qb-c1", name="Fixture Account", email=None,
        phone=None, state=None, balance=Decimal("0"), total_sales=Decimal("0"),
    )


def _invoice(*, days_ago=60, balance="125.50", total="250.00") -> Invoice:
    today = date.today()
    return Invoice(
        id="internal-i1", qb_id="qb-i1", customer_id="qb-c1",
        customer_name="Fixture Account", date=today - timedelta(days=days_ago),
        due_date=today - timedelta(days=25), total=Decimal(total),
        balance=Decimal(balance), status="open", line_items=[],
    )


def _stock(**overrides) -> dict:
    item = {
        "qb_id": "qb-sku1", "product_name": "Fixture Case", "qb_qty": 10,
        "warehouse_qty": 10, "total_qty": 10, "quantity_source": "quickbooks",
        "source_quality": "partial", "weekly_sell_rate": 10,
        "weekly_sell_rate_observed": True, "weekly_sell_rate_source_status": "observed",
        "open_po_qty": 0, "open_po_qty_observed": True,
        "open_po_source_status": "observed",
    }
    return {**item, **overrides}


def test_dataclass_records_generate_evidence_backed_drafts_without_crashing():
    events = detect_opportunities([_customer()], [_invoice()], [])
    assert {ev["event_type"] for ev in events} == {"dormant_account", "overdue_invoice"}
    assert all(ev["action_type"] == "draft_customer_email" for ev in events)


def test_zero_invoice_total_does_not_try_dictionary_access_on_dataclass():
    events = detect_opportunities([_customer()], [_invoice(total="0")], [])
    assert any(ev["event_type"] == "overdue_invoice" for ev in events)


def test_incomplete_or_nan_balance_does_not_become_a_verified_zero():
    invoice = {"qb_id": "i1", "customer_id": "c1", "customer_name": "Fixture",
               "order_date": "2025-12-15", "due_date": "2026-01-01"}
    with pytest.raises(ValueError, match="balance"):
        detect_opportunities([], [invoice], [])
    with pytest.raises(ValueError, match="balance"):
        detect_opportunities([], [{**invoice, "balance": "NaN"}], [])


def test_mixed_mapping_and_dataclass_order_dates_are_compared_as_dates():
    invoices = [
        _invoice(days_ago=70),
        {"customer_id": "qb-c1", "order_date": (date.today() - timedelta(days=80)).isoformat(),
         "total_amount": "25", "balance": "0", "due_date": None},
    ]
    events = detect_opportunities([_customer()], invoices, [])
    assert len([ev for ev in events if ev["event_type"] == "dormant_account"]) == 1


def test_proactive_stock_does_not_add_duplicate_quickbooks_and_warehouse_quantities():
    events = detect_opportunities([], [], [_stock()])
    assert len(events) == 1
    assert events[0]["event_type"] == "low_stock"
    assert "~1.0 weeks" in events[0]["body"]


@pytest.mark.parametrize("missing", [
    {"weekly_sell_rate_observed": False},
    {"weekly_sell_rate_source_status": "stale"},
    {"open_po_qty_observed": False},
    {"open_po_source_status": "unavailable"},
    {"quantity_source": "unverified"},
    {"weekly_sell_rate": float("nan")},
    {"total_qty": None},
    {"open_po_qty": 2},
])
def test_no_purchase_order_draft_without_verified_stock_demand_and_po_status(missing):
    assert detect_opportunities([], [], [_stock(**missing)]) == []


def test_verified_zero_stock_with_demand_and_no_open_po_still_generates_draft():
    events = detect_opportunities([], [], [_stock(total_qty=0, qb_qty=0, warehouse_qty=0)])
    assert len(events) == 1
    assert events[0]["event_type"] == "low_stock"
    assert "out of stock" in events[0]["title"].lower()


def test_zero_demand_does_not_trigger_speculative_restocking():
    assert detect_opportunities([], [], [_stock(weekly_sell_rate=0, total_qty=0)]) == []
