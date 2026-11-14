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

    # Evidence confirmations are separate append-only events. The original
    # email/document report retains its untrusted source after confirmation,
    # so resolve the named employee's *actual confirmed evidence* before
    # projecting it as staff-attested. Never trust a bare confirmation claim.
    associations = defaultdict(list)
    active = {s.shipment_id: s for s in ledger.shipments()}
    events = ledger.events()
    confirmed_events = {
        (event.shipment_id, event.source_ref, event.confirmed_by)
        for event in events
        if (event.company_id == ledger.company_id
            and event.shipment_id in active
            and event.kind == "evidence_confirmed"
            and event.source == SourceType.USER_CONFIRMED
            and bool(event.confirmed_by)
            and bool(event.source_ref))
    }
    confirmed_evidence = {
        (shipment_id, evidence.evidence_id): evidence
        for shipment_id, shipment in active.items()
        for evidence in shipment.evidence
        if (evidence.kind in PO_EVENT_KINDS
            and evidence.status == "confirmed"
            and bool(evidence.reviewed_by)
            and (shipment_id, evidence.evidence_id, evidence.reviewed_by)
                in confirmed_events)
    }
    # Only events written under the real tenant can establish an association.
    # User-reported/email/AI links remain unverified until named confirmation.
    for event in events:
        if event.company_id != ledger.company_id or event.kind not in PO_EVENT_KINDS:
            continue
        if event.shipment_id not in active_shipments:
            continue
        numbers = event.payload.get("po_numbers") if isinstance(event.payload, dict) else None
        if not isinstance(numbers, list):
            continue
        for number in set(n for n in numbers if isinstance(n, str)):
            if number in po_index:
                origin = AUTHORISED_PO_LINK_SOURCES.get(event.source, "unverified_report")
                if origin == "unverified_report":
                    evidence = confirmed_evidence.get(
                        (event.shipment_id, f"evd-{event.idempotency_key[:16]}")
                    )
                    # A confirmation must point to precisely this event and
                    # its recorded PO payload; merely confirming other
                    # evidence on the shipment must not promote the PO link.
                    if (evidence is not None and evidence.payload == event.payload
                            and evidence.source == event.source
                            and evidence.source_ref == event.source_ref):
                        origin = "staff_attested"
                associations[number].append({
                    "shipment_id": event.shipment_id,
                    "origin": origin,
                    "source": event.source.value,
                    "source_ref": event.source_ref,
                })

    result = []
    for number, po in po_index.items():
        entries = associations[number]
        verified = [e for e in entries if e["origin"] != "unverified_report"]
        verified_ids = sorted({e["shipment_id"] for e in verified})
        reported_ids = sorted({
            e["shipment_id"] for e in entries if e["origin"] == "unverified_report"
        } - set(verified_ids))
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
