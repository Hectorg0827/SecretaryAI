"""Synthetic source reconciliation tests; no accounting or inventory writes."""
from decimal import Decimal
import pytest

from app.intelligence.wholesale_distribution.logistics.journey import Shipment, ShipmentLine
from app.intelligence.wholesale_distribution.logistics.purchase_bridge import (
    InvalidPurchaseEvidence, compare_purchase_records,
)


def shipment(po_numbers=("PO-1",), quantity=1200):
    return Shipment(
        shipment_id="SHP-1", company_id="company-A",
        po_numbers=list(po_numbers),
        lines=[ShipmentLine(sku="WINE", qty_cases=quantity)],
    )


def detail(number="PO-1", ordered=1200, received=1200, receipts=1):
    return {
        "order": {"po_number": number, "status": "received"},
        "lines": [{"sku": "WINE", "qty_ordered": ordered, "qty_received": received}],
        "posted_receipts": [{"id": f"receipt-{i}"} for i in range(receipts)],
        "ordered_cases": str(ordered),
        "received_cases": str(received),
        "remaining_cases": str(Decimal(str(ordered)) - Decimal(str(received))),
    }


def test_1200_received_cases_never_certify_this_specific_container():
    result = compare_purchase_records(shipment(), {"PO-1": detail()}, set())
    assert result["status"] == "po_quantities_match_not_receipt_attribution"
    assert result["po_quantities_match"] is True
    assert result["po_results"][0]["received_cases_all_conditions"] == "1200"
    assert result["posted_receipts_prove_shipment_received"] is False
    assert result["shipment_inventory_reconciled"] is False
    assert result["accounting_reconciled"] is False
    assert result["review_required"] is True


def test_shared_po_requires_explicit_receipt_to_shipment_allocation():
    result = compare_purchase_records(shipment(), {"PO-1": detail()}, {"PO-1"})
    assert result["status"] == "shared_po_needs_allocation_review"
    assert result["shared_po_numbers"] == ["PO-1"]
    assert result["shipment_inventory_reconciled"] is False


def test_partial_delivery_is_a_po_aggregate_not_sellable_stock():
    result = compare_purchase_records(shipment(), {"PO-1": detail(received=1180)}, set())
    assert result["po_results"][0]["remaining_cases"] == "20"
    assert result["po_results"][0]["received_cases_all_conditions"] == "1180"
    assert result["shipment_inventory_reconciled"] is False


def test_missing_po_reference_and_missing_purchasing_record_are_distinct():
    no_link = shipment(po_numbers=())
    assert compare_purchase_records(no_link, {}, set())["status"] == "no_po_reference"
    assert compare_purchase_records(shipment(), {"PO-1": None}, set())["status"] == "missing_purchasing_record"


def test_sku_difference_remains_review_needed():
    result = compare_purchase_records(shipment(quantity=600), {"PO-1": detail()}, set())
    assert result["status"] == "sku_quantity_difference"
    assert result["po_quantities_match"] is False


@pytest.mark.parametrize("row", [
    detail(ordered=1200, received=1300),
    detail(ordered="NaN", received=100),
    detail(ordered=1200, received=-10),
    {**detail(), "ordered_cases": "1100"},
    {**detail(), "lines": [{"sku": "WINE", "qty_ordered": 500, "qty_received": 300},
                            {"sku": "WINE", "qty_ordered": 700, "qty_received": 500}]},
])
def test_inconsistent_records_fail_closed(row):
    with pytest.raises(InvalidPurchaseEvidence):
        compare_purchase_records(shipment(), {"PO-1": row}, set())


def test_multi_po_aggregates_only_if_every_po_is_available():
    s = shipment(("PO-1", "PO-2"))
    a = detail("PO-1", ordered=500, received=500)
    b = detail("PO-2", ordered=700, received=0, receipts=0)
    result = compare_purchase_records(s, {"PO-1": a, "PO-2": b}, set())
    assert result["po_quantities_match"] is True
    assert result["ordered_sku_totals"] == {"WINE": "1200"}
    assert result["shipment_inventory_reconciled"] is False
