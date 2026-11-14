"""Conservative read-only comparison of logistics journeys with durable POs.

PO-level receipt totals never prove that a particular container was received,
especially when a purchase order spans more than one shipment.
"""
from decimal import Decimal, InvalidOperation


class InvalidPurchaseEvidence(ValueError):
    pass


def qty(value):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidPurchaseEvidence("Missing or malformed purchase quantity") from exc
    if not n.is_finite() or n < 0:
        raise InvalidPurchaseEvidence("Invalid purchase quantity")
    return n


def indexed_quantities(lines, field):
    values = {}
    for line in lines:
        sku = str(line.get("sku") or "").strip()
        if not sku or sku in values:
            raise InvalidPurchaseEvidence("Missing or duplicated SKU")
        values[sku] = qty(line.get(field))
    return values


def compare_purchase_records(shipment, purchase_details, shared_pos):
    """purchase_details comes exclusively from the tenant-scoped purchasing API."""
    pos = list(shipment.po_numbers)
    if len(pos) != len(set(pos)) or len(pos) > 20:
        raise InvalidPurchaseEvidence("Ambiguous number of purchase orders")
    if not pos:
        return {"shipment_id": shipment.shipment_id, "status": "no_po_reference",
                "po_results": [], "review_required": True,
                "posted_receipts_prove_shipment_received": False,
                "shipment_inventory_reconciled": False, "accounting_reconciled": False}
    if set(pos) != set(purchase_details):
        raise InvalidPurchaseEvidence("Incomplete purchase evidence")

    rows, order_by_sku, missing = [], {}, False
    for po_number in pos:
        detail = purchase_details[po_number]
        if detail is None:
            missing = True
            rows.append({"po_number": po_number, "status": "not_found"})
            continue
        order = detail.get("order") or {}
        if order.get("po_number") != po_number:
            raise InvalidPurchaseEvidence("Purchase order mismatch")
        state = str(order.get("status") or "")
        if state not in {"draft", "approved", "partially_received", "received"}:
            raise InvalidPurchaseEvidence("Unrecognized purchase status")
        lines = detail.get("lines")
        receipts = detail.get("posted_receipts")
        if not isinstance(lines, list) or not lines or not isinstance(receipts, list):
            raise InvalidPurchaseEvidence("Missing purchase lines or receipts")
        ordered = indexed_quantities(lines, "qty_ordered")
        received = indexed_quantities(lines, "qty_received")
        if any(amount <= 0 or received[sku] > amount for sku, amount in ordered.items()):
            raise InvalidPurchaseEvidence("Purchase quantities inconsistent")
        for sku, value in ordered.items():
            order_by_sku[sku] = order_by_sku.get(sku, Decimal(0)) + value
        ordered_total = sum(ordered.values(), Decimal(0))
        received_total = sum(received.values(), Decimal(0))
        if (qty(detail.get("ordered_cases")) != ordered_total
            or qty(detail.get("received_cases")) != received_total
            or qty(detail.get("remaining_cases")) != ordered_total - received_total):
            raise InvalidPurchaseEvidence("Purchase totals inconsistent")
        rows.append({
            "po_number": po_number, "status": state,
            "ordered_cases": str(ordered_total),
            "received_cases_all_conditions": str(received_total),
            "remaining_cases": str(ordered_total - received_total),
            "posted_receipt_count": len(receipts),
        })

    shipment_by_sku = indexed_quantities(
        [{"sku": line.sku, "qty_cases": line.qty_cases} for line in shipment.lines],
        "qty_cases",
    ) if shipment.lines else None
    same = (shipment_by_sku == order_by_sku
            if not missing and shipment_by_sku is not None else None)
    if missing:
        status = "missing_purchasing_record"
    elif shared_pos:
        status = "shared_po_needs_allocation_review"
    elif same is None:
        status = "shipment_line_evidence_missing"
    elif not same:
        status = "sku_quantity_difference"
    else:
        status = "po_quantities_match_not_receipt_attribution"
    return {
        "shipment_id": shipment.shipment_id,
        "status": status,
        "purchasing_source": "internal_purchasing_ledger",
        "read_consistency": "separate_tenant_scoped_queries",
        "po_results": rows,
        "po_quantities_match": same,
        "shared_po_numbers": sorted(shared_pos),
        "shipment_sku_totals": {k: str(v) for k, v in shipment_by_sku.items()}
            if shipment_by_sku is not None else None,
        "ordered_sku_totals": {k: str(v) for k, v in order_by_sku.items()}
            if not missing else None,
        "posted_receipts_prove_shipment_received": False,
        "shipment_inventory_reconciled": False,
        "accounting_reconciled": False,
        "review_required": True,
        "note": (
            "PO-level receipt totals cannot be attributed to this container without "
            "authenticated lot, receipt and shipment mapping. Received cases include "
            "damaged and quarantined units; no stock or QuickBooks write occurred."
        ),
    }
