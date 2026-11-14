"""Read-only PO-to-shipment visibility based on recorded event provenance.

The purchasing ledger is authoritative for the PO's own state only. Linking
its reference to a shipment does NOT prove the container departed, arrived,
was received, or reconciled with QuickBooks.
"""
from __future__ import annotations

from collections import defaultdict

from .journey import ShipmentLedger, SourceType

AUTHORISED_PO_LINK_SOURCES = {
    SourceType.ERP: "erp_recorded",
    SourceType.USER_CONFIRMED: "staff_attested",
    SourceType.DOCUMENT_VALIDATED: "reviewed_document",
}
PO_EVENT_KINDS = {"shipment_created", "shipment_updated"}


class PurchaseCoverageInvalid(ValueError):
    pass


def purchase_journey_coverage(rows: list[dict], ledger: ShipmentLedger) -> dict:
    """Classify all in-scope POs, including POs without any logistics events."""
    if len(rows) > 200:
        raise PurchaseCoverageInvalid("Purchase order coverage exceeds bounded scope")

    po_index = {}
    for row in rows:
        number = str(row.get("po_number") or "").strip()
        status = str(row.get("status") or "")
        if not number or number in po_index or len(number) > 100:
            raise PurchaseCoverageInvalid("Purchase order identity inconsistent")
        if status not in {"draft", "approved", "partially_received", "received"}:
            raise PurchaseCoverageInvalid("Purchase order status invalid")
        if str(row.get("company_id")) != str(ledger.company_id):
            raise PurchaseCoverageInvalid("Purchase order belongs to another tenant")
        po_index[number] = row

    # Only events written under the real tenant can establish an association.
    # User-reported/email/AI links remain explicitly non-authoritative.
    associations = defaultdict(list)
    active_shipments = {s.shipment_id for s in ledger.shipments()}
    for event in ledger.events():
        if event.company_id != ledger.company_id or event.kind not in PO_EVENT_KINDS:
            continue
        if event.shipment_id not in active_shipments:
            continue
        numbers = event.payload.get("po_numbers") if isinstance(event.payload, dict) else None
        if not isinstance(numbers, list):
            continue
        for number in set(n for n in numbers if isinstance(n, str)):
            if number in po_index:
                associations[number].append({
                    "shipment_id": event.shipment_id,
                    "origin": AUTHORISED_PO_LINK_SOURCES.get(event.source, "unverified_report"),
                    "source": event.source.value,
                    "source_ref": event.source_ref,
                })

    result = []
    for number, po in po_index.items():
        entries = associations[number]
        verified = [e for e in entries if e["origin"] != "unverified_report"]
        verified_ids = sorted({e["shipment_id"] for e in verified})
        reported_ids = sorted({e["shipment_id"] for e in entries if e["origin"] == "unverified_report"})
        if len(verified_ids) > 1:
            link_state = "multiple_journeys_review_required"
        elif any(e["origin"] == "erp_recorded" for e in verified):
            link_state = "linked_erp_recorded"
        elif any(e["origin"] == "reviewed_document" for e in verified):
            link_state = "linked_reviewed_document"
        elif verified:
            link_state = "linked_staff_attested"
        elif reported_ids:
            link_state = "unverified_report_only"
        else:
            link_state = "not_linked"

        result.append({
            "po_number": number,
            "po_status": po["status"],
            "supplier_ref": po.get("supplier_ref"),
            "created_at": po.get("created_at"),
            "shipment_link_status": link_state,
            "linked_shipment_ids": verified_ids,
            "reported_unverified_shipment_ids": reported_ids,
            "requires_review": link_state not in {"linked_erp_recorded"},
            "shipping_status_verified": False,
            "container_receipt_verified": False,
            "warehouse_receipt_attributed_to_shipment": False,
        })
    return {
        "purchase_order_count": len(result),
        "not_linked_count": sum(x["shipment_link_status"] == "not_linked" for x in result),
        "unverified_report_count": sum(x["shipment_link_status"] == "unverified_report_only" for x in result),
        "review_required_count": sum(x["requires_review"] for x in result),
        "orders": result,
        "source": "tenant_scoped_purchasing_orders_and_logistics_events",
        "scope": "recorded company POs, at most 200; no inference about external shipments",
        "shipment_status_verified_by_po_link": False,
        "inventory_posted_by_this_operation": False,
    }
