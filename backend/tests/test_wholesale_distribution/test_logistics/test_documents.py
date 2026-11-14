"""Document intelligence: identify, extract, validate, link — evidence, not authorization."""
import pytest

from app.intelligence.wholesale_distribution.logistics.documents import (
    DocumentIntelligence, DocumentType, broker_status, is_valid_container_number,
)
from app.intelligence.wholesale_distribution.logistics.journey import SourceType

from .journey_helpers import BOL, CONTAINER, allocate, build_in_transit, create_import, ev, new_ledger

DI = DocumentIntelligence()

BOL_TEXT = f"""BILL OF LADING
B/L No: {BOL}
Shipper: Vinos del Sur SA   Consignee: Acme Imports LLC   Notify party: Rapid Customs
Port of loading: Valencia   Port of discharge: Miami
Container: {CONTAINER}  Seal No: SL445566
PO# PO-1001   1200 cases Tempranillo
"""

PACKING_TEXT = f"""PACKING LIST
PO No: PO-1001   Container {CONTAINER}
SKU: WINE-RED  Lot: L2026A  1,200 cases  Gross weight 18,000 kg
"""

FREIGHT_INVOICE = f"""FREIGHT INVOICE   Invoice No: GF-77821
B/L {BOL}   Container {CONTAINER}
Ocean freight ............ $2,800.00
Fuel surcharge (BAF) ..... $350.00
Destination THC .......... $275.00
Drayage .................. $650.00
TOTAL DUE ................ $4,075.00
"""

POD_TEXT = """PROOF OF DELIVERY  Delivery receipt
Order SO-501   B/L MAEU240001234
Delivered on: 2026-11-18
Received by: Maria Lopez
600 cases received in good order
"""


class TestContainerNumbers:
    def test_iso6346_published_example(self):
        assert is_valid_container_number("CSQU3054383")
        assert not is_valid_container_number("CSQU3054384")

    def test_typo_in_container_number_is_flagged(self):
        doc = DI.extract("BILL OF LADING  B/L No: X  Container MSCU1234565")
        assert doc.fields["container_numbers"] == []
        assert doc.fields["invalid_container_numbers"] == ["MSCU1234565"]


class TestClassification:
    @pytest.mark.parametrize("text,expected", [
        (BOL_TEXT, DocumentType.BILL_OF_LADING),
        (PACKING_TEXT, DocumentType.PACKING_LIST),
        ("COMMERCIAL INVOICE  Incoterm FOB  Country of origin: Spain  HS code 2204.21", DocumentType.COMMERCIAL_INVOICE),
        (FREIGHT_INVOICE, DocumentType.FREIGHT_INVOICE),
        ("CBP Form 7501 Entry Summary  Entry No: 123-4567890-1", DocumentType.CUSTOMS_ENTRY),
        ("Cargo Release notification: CBP release 1C issued", DocumentType.CUSTOMS_RELEASE),
        ("ARRIVAL NOTICE  ETA: 2026-11-10  Last free day: 2026-11-15", DocumentType.ARRIVAL_NOTICE),
        ("WAREHOUSE RECEIPT WR# 5521 putaway complete", DocumentType.WAREHOUSE_RECEIPT),
        ("Delivery appointment confirmation  Dock door 4  Appointment date: 2026-11-16 08:00-10:00",
         DocumentType.DELIVERY_APPOINTMENT),
        (POD_TEXT, DocumentType.PROOF_OF_DELIVERY),
        ("DAMAGE REPORT  OS&D  20 cases crushed, photos attached", DocumentType.DAMAGE_REPORT),
        ("SUPPLIER CLAIM  Claim no C-1  claim amount $480.00", DocumentType.SUPPLIER_CLAIM),
        ("Customs broker update — customs status: hold for exam", DocumentType.BROKER_NOTICE),
        ("hello there, lunch on friday?", DocumentType.UNKNOWN),
    ])
    def test_identifies_document(self, text, expected):
        assert DI.classify(text)[0] == expected


class TestExtraction:
    def test_bol_fields(self):
        f = DI.extract(BOL_TEXT).fields
        assert f["bol_number"] == BOL
        assert f["container_numbers"] == [CONTAINER]
        assert f["seal_numbers"] == ["SL445566"]
        assert "PO-1001" in f["po_numbers"]

    def test_packing_list_quantity_sku_lot(self):
        f = DI.extract(PACKING_TEXT).fields
        assert f["quantities_cases"] == [1200]
        assert f["skus"] == ["WINE-RED"] and f["lots"] == ["L2026A"]

    def test_freight_invoice_components_and_total(self):
        f = DI.extract(FREIGHT_INVOICE).fields
        assert f["cost_components"] == {"ocean_freight": 2800.0, "fuel_surcharge": 350.0,
                                        "port_charges": 275.0, "drayage": 650.0}
        assert f["total_amount"] == 4075.0

    def test_pod_signature_and_date(self):
        f = DI.extract(POD_TEXT).fields
        assert f["signed_by"].startswith("Maria Lopez")
        assert f["dates"]["delivered"] == "2026-11-18"

    def test_damage_count(self):
        f = DI.extract("DAMAGE REPORT  20 cases damaged on arrival, 2 cases short").fields
        assert f["damaged_cases"] == 20 and f["short_cases"] == 2


class TestBrokerStatus:
    @pytest.mark.parametrize("text,status", [
        ("Your cargo has been released by CBP.", "released"),
        ("Cargo NOT released — awaiting FDA review.", "not_released"),
        ("Entry filed; release pending.", "not_released"),
        ("Container placed on hold. It has been released from the vessel only.", "hold"),
        ("CBP ordered an intensive exam.", "exam"),
        ("We have not yet cleared the shipment.", "not_released"),
    ])
    def test_conservative_reading(self, text, status):
        assert broker_status(text) == status


class TestValidationAndLinking:
    def test_links_by_container_and_validates(self):
        L = new_ledger()
        build_in_transit(L)
        doc, events = DI.process(BOL_TEXT, L)
        assert doc.linked_shipment_id == "SHP-1" and doc.link_basis == "container"
        assert doc.status == "validated"
        assert doc.to_dict()["authorization"].startswith("none")

    def test_quantity_mismatch_fails_validation(self):
        L = new_ledger()
        create_import(L)
        doc, _ = DI.process(PACKING_TEXT.replace("1,200 cases", "1,150 cases"), L)
        assert doc.status == "validation_failed"
        assert any("1150" in c for c in doc.failed_checks)

    def test_wrong_bol_fails_validation(self):
        L = new_ledger()
        create_import(L)
        doc, _ = DI.process(BOL_TEXT.replace(f"B/L No: {BOL}", "B/L No: OTHER1234567"), L)
        assert doc.status == "validation_failed"

    def test_ambiguous_link_is_not_guessed(self):
        L = new_ledger()
        create_import(L, sid="SHP-1", container="MSCU1234566", bol="BOL-AAA111", po="PO-1")
        create_import(L, sid="SHP-2", container="TCKU7654323", bol="BOL-BBB222", po="PO-1")
        doc, events = DI.process("PACKING LIST  PO No: PO-1  10 cases", L)
        assert doc.linked_shipment_id == "" and doc.link_candidates == ["SHP-1", "SHP-2"]
        assert events == []

    def test_unreviewed_pod_is_evidence_not_milestone(self):
        L = new_ledger()
        build_in_transit(L)
        allocate(L)
        L.ingest(ev("milestone", SourceType.CARRIER_API, code="delivered", at="2026-11-18", order_id="SO-501"))
        doc, events = DI.process(POD_TEXT, L)
        for e in events:
            L.ingest(e)
        s = L.get("SHP-1")
        assert "proof_of_delivery" in s.document_types()
        assert not s.has("pod_received")
        assert [e.code for e in s.pending_evidence()] == ["pod_received"]

    def test_reviewed_validated_pod_becomes_verified(self):
        L = new_ledger()
        build_in_transit(L)
        allocate(L)
        L.ingest(ev("milestone", SourceType.CARRIER_API, code="delivered", at="2026-11-18", order_id="SO-501"))
        doc, events = DI.process(POD_TEXT, L, reviewed_by="user-cs-3")
        for e in events:
            L.ingest(e)
        s = L.get("SHP-1")
        assert s.milestones["pod_received"].source == SourceType.DOCUMENT_VALIDATED
        assert s.milestones["pod_received"].confirmed_by == "user-cs-3"

    def test_broker_not_released_email_does_not_suggest_release(self):
        L = new_ledger()
        build_in_transit(L)
        doc, events = DI.process(f"Customs broker update for container {CONTAINER}: "
                                 "cargo NOT released, awaiting documents.", L)
        assert doc.fields["customs_status"] == "not_released"
        assert "suggests_milestone" not in events[0].payload

    def test_freight_invoice_creates_unverified_cost_lines(self):
        L = new_ledger()
        create_import(L)
        doc, events = DI.process(FREIGHT_INVOICE, L)
        for e in events:
            L.ingest(e)
        costs = L.get("SHP-1").costs
        assert sum(c.amount_usd for c in costs) == 4075.0
        assert not any(c.verified for c in costs)

    def test_arrival_notice_sets_last_free_day_only_as_evidence(self):
        L = new_ledger()
        build_in_transit(L)
        doc, events = DI.process(f"ARRIVAL NOTICE  Container {CONTAINER}  ETA: 2026-11-10  "
                                 "Last free day: 2026-11-15", L)
        for e in events:
            L.ingest(e)
        s = L.get("SHP-1")
        assert "last_free_day" not in s.port_terms
        assert any(e.kind == "port_terms" for e in s.pending_evidence())


class TestReDoSResistance:
    """Hostile uploads must not hang the server (CodeQL py/polynomial-redos)."""

    @pytest.mark.parametrize("label", ["seal", "sku", "lot", "batch", "bol", "b/l", "po", "booking",
                                       "entry no", "short", "total", "dock", "signed by", "date"])
    def test_label_followed_by_huge_whitespace_is_fast(self, label):
        import time
        start = time.perf_counter()
        DI.extract(label + " " * 100_000 + "!")
        DI.extract((label + " ") * 30_000)
        assert time.perf_counter() - start < 2.0

    def test_long_digit_runs_are_fast(self):
        import time
        start = time.perf_counter()
        DI.extract("1" * 100_000 + "\n" + "1," * 50_000 + "\nFREIGHT INVOICE " + "9" * 50_000)
        assert time.perf_counter() - start < 2.0

    def test_labels_still_extract_after_hardening(self):
        f = DI.extract("BILL OF LADING  B/L Number: MAEU240001234   Seal #: SL445566\n"
                       "PO No. PO-1001   SKU: WINE-RED   Lot No: L2026A   1,200 cases\n"
                       "Booking ref: BKG12345   Entry No: 123-4567890").fields
        assert f["bol_number"] == "MAEU240001234" and f["seal_numbers"] == ["SL445566"]
        assert f["po_numbers"] == ["PO-1001"] and f["skus"] == ["WINE-RED"] and f["lots"] == ["L2026A"]
        assert f["booking_ref"] == "BKG12345" and f["entry_number"] == "123-4567890"
        assert f["quantities_cases"] == [1200]
