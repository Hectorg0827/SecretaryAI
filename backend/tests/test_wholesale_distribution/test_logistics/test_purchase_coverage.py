"""No-write PO-to-journey provenance coverage regressions."""
import pytest

from app.intelligence.wholesale_distribution.logistics.journey import (
    LogisticsEvent, ShipmentLedger, SourceType,
)
from app.intelligence.wholesale_distribution.logistics.purchase_coverage import (
    PurchaseCoverageInvalid, purchase_journey_coverage,
)


def po(number="PO-1", status="approved", company="company-A"):
    return {
        "company_id": company, "po_number": number,
        "supplier_ref": "SUP-TEST", "status": status,
        "created_at": "2026-11-13T10:00:00Z",
    }


def event(ledger, number="PO-1", shipment="SHP-1", source=SourceType.ERP):
    return ledger.ingest(LogisticsEvent(
        company_id=ledger.company_id, shipment_id=shipment,
        kind="shipment_created", source=source,
        confirmed_by="employee-1" if source == SourceType.USER_CONFIRMED else "",
        payload={"po_numbers": [number], "container_numbers": ["MSCU1234566"]},
    ))


def test_zero_events_is_not_mistaken_for_no_purchase_orders():
    ledger = ShipmentLedger("company-A")
    result = purchase_journey_coverage([po("PO-1"), po("PO-2", "received")], ledger)
    assert result["purchase_order_count"] == 2
    assert result["not_linked_count"] == 2
    assert result["orders"][1]["shipment_link_status"] == "not_linked"
    assert result["orders"][1]["warehouse_receipt_attributed_to_shipment"] is False
    assert result["shipment_status_verified_by_po_link"] is False


def test_erp_link_is_recorded_but_never_claims_delivery():
    ledger = ShipmentLedger("company-A")
    event(ledger)
    result = purchase_journey_coverage([po()], ledger)
    row = result["orders"][0]
    assert row["shipment_link_status"] == "linked_erp_recorded"
    assert row["linked_shipment_ids"] == ["SHP-1"]
    assert row["shipping_status_verified"] is False
    assert row["container_receipt_verified"] is False


def test_email_created_shipment_link_remains_unverified_even_if_event_applier_says_verified():
    ledger = ShipmentLedger("company-A")
    event(ledger, source=SourceType.EMAIL_PARSED)
    row = purchase_journey_coverage([po()], ledger)["orders"][0]
    assert row["shipment_link_status"] == "unverified_report_only"
    assert row["linked_shipment_ids"] == []
    assert row["reported_unverified_shipment_ids"] == ["SHP-1"]


def test_user_confirmation_is_distinct_from_erp():
    ledger = ShipmentLedger("company-A")
    event(ledger, source=SourceType.USER_CONFIRMED)
    row = purchase_journey_coverage([po()], ledger)["orders"][0]
    assert row["shipment_link_status"] == "linked_staff_attested"
    assert row["requires_review"] is True


def test_one_po_multiple_shipments_requires_explicit_attribution():
    ledger = ShipmentLedger("company-A")
    event(ledger, shipment="SHP-1")
    event(ledger, shipment="SHP-2")
    row = purchase_journey_coverage([po("PO-1", "received")], ledger)["orders"][0]
    assert row["shipment_link_status"] == "multiple_journeys_review_required"
    assert row["linked_shipment_ids"] == ["SHP-1", "SHP-2"]
    assert row["container_receipt_verified"] is False


def test_unrelated_shipment_and_po_are_not_silently_matched():
    ledger = ShipmentLedger("company-A")
    event(ledger, number="PO-OTHER")
    row = purchase_journey_coverage([po()], ledger)["orders"][0]
    assert row["shipment_link_status"] == "not_linked"


@pytest.mark.parametrize("rows", [
    [po(), po()],
    [po(status="processed")],
    [po(company="company-B")],
    [po(number="")],
    [po(number="P" * 101)],
])
def test_invalid_or_ambiguous_purchase_rows_fail_closed(rows):
    with pytest.raises(PurchaseCoverageInvalid):
        purchase_journey_coverage(rows, ShipmentLedger("company-A"))


def test_record_cap_is_explicit_not_silent():
    ledger = ShipmentLedger("company-A")
    with pytest.raises(PurchaseCoverageInvalid, match="bounded"):
        purchase_journey_coverage([po(f"PO-{i}") for i in range(201)], ledger)


def test_unverified_new_shipment_identifiers_are_hidden_until_named_person_confirms():
    ledger = ShipmentLedger("company-A")
    response = event(ledger, source=SourceType.EMAIL_PARSED)
    assert response.verified is False
    assert response.evidence_id.startswith("evd-")
    shipment = ledger.get("SHP-1")
    assert shipment.po_numbers == []
    assert shipment.container_numbers == []
    assert shipment.pending_evidence()[0].kind == "shipment_created"

    coverage = purchase_journey_coverage([po()], ledger)["orders"][0]
    assert coverage["shipment_link_status"] == "unverified_report_only"
    assert coverage["linked_shipment_ids"] == []

    ledger.confirm_evidence(response.evidence_id, confirmed_by="operations-1")
    assert shipment is not ledger.get("SHP-1")  # event source preserves atomic snapshots
    assert ledger.get("SHP-1").po_numbers == ["PO-1"]
    assert ledger.get("SHP-1").container_numbers == ["MSCU1234566"]
    updated = purchase_journey_coverage([po()], ledger)["orders"][0]
    assert updated["shipment_link_status"] == "linked_staff_attested"


def test_unverified_update_cannot_replace_or_append_official_shipment_identifiers():
    ledger = ShipmentLedger("company-A")
    event(ledger)
    reported = ledger.ingest(LogisticsEvent(
        company_id="company-A", shipment_id="SHP-1", kind="shipment_updated",
        source=SourceType.EMAIL_PARSED, source_ref="gmail-test-12",
        payload={"po_numbers": ["PO-UNVERIFIED"], "bol_number": "UNVERIFIEDBOL"},
    ))
    assert reported.verified is False
    assert reported.evidence_id
    original = ledger.get("SHP-1")
    assert original.po_numbers == ["PO-1"]
    assert original.bol_number == ""
    assert len(original.pending_evidence()) == 1
    ledger.confirm_evidence(reported.evidence_id, confirmed_by="manager-1")
    updated = ledger.get("SHP-1")
    assert updated.po_numbers == ["PO-1", "PO-UNVERIFIED"]
    assert updated.bol_number == "UNVERIFIEDBOL"
    assert not updated.pending_evidence()
