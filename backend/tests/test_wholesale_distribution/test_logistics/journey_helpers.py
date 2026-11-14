"""Shared builders for the logistics journey tests (not a test module)."""
from __future__ import annotations

from app.intelligence.wholesale_distribution.logistics.journey import (
    InMemoryEventStore, LogisticsEvent, ShipmentLedger, SourceType,
)

CO = "company-A"
CONTAINER = "MSCU1234566"        # valid ISO 6346 check digit
CONTAINER_2 = "TCKU7654323"
BOL = "MAEU240001234"

S = SourceType


def ev(kind, source, *, sid="SHP-1", code="", at="", ref="", by="", company=CO, **payload):
    return LogisticsEvent(company_id=company, shipment_id=sid, kind=kind, source=source, code=code,
                          occurred_at=at, source_ref=ref, confirmed_by=by, payload=payload)


def new_ledger(store=None, company=CO) -> ShipmentLedger:
    return ShipmentLedger(company, store=store if store is not None else InMemoryEventStore())


def create_import(ledger: ShipmentLedger, sid="SHP-1", container=CONTAINER, bol=BOL, po="PO-1001",
                  qty=1200, unit_cost=24.0, company=CO, **extra):
    payload = {
        "mode": "ocean", "supply_chain_type": "overseas", "po_numbers": [po],
        "supplier_id": "SUP-VINOS", "supplier_name": "Vinos del Sur SA",
        "carrier": "Maersk", "forwarder": "Global Fwd", "broker": "Rapid Customs",
        "warehouse": "WH-Miami", "container_numbers": [container], "bol_number": bol,
        "origin": "Valencia", "destination": "Port of Miami",
        "po_sent_at": "2026-09-01T10:00:00+00:00", "requested_ship_date": "2026-09-20",
        "lines": [{"sku": "WINE-RED", "qty_cases": qty, "unit_cost": unit_cost, "lot": "L2026A",
                   "description": "Tempranillo 12x750"}],
        "commercial": {"payment_terms": "Net 60", "incoterm": "FOB Valencia", "freight_quote_usd": 3000.0},
    }
    payload.update(extra)
    return ledger.ingest(ev("shipment_created", S.ERP, sid=sid, company=company, **payload))


def allocate(ledger, sid="SHP-1", order="SO-501", customer="CUST-1", name="Bodega Uno", rep="rep-1",
             qty=600, promised="2026-11-20"):
    return ledger.ingest(ev("allocation", S.ERP, sid=sid, order_id=order, customer_id=customer,
                            customer_name=name, sales_rep_id=rep, sku="WINE-RED", qty_cases=qty,
                            promised_date=promised))


def build_in_transit(ledger, sid="SHP-1", **kw):
    """Import that has been booked and departed, with a verified ETA."""
    create_import(ledger, sid=sid, **kw)
    ledger.ingest(ev("milestone", S.ERP, sid=sid, code="po_approved", at="2026-09-01"))
    ledger.ingest(ev("milestone", S.ERP, sid=sid, code="po_sent", at="2026-09-01T10:00:00+00:00"))
    ledger.ingest(ev("milestone", S.SUPPLIER_EDI, sid=sid, code="supplier_confirmed", at="2026-09-02"))
    ledger.ingest(ev("milestone", S.FORWARDER_API, sid=sid, code="freight_booked", at="2026-09-10"))
    ledger.ingest(ev("milestone", S.CARRIER_API, sid=sid, code="vessel_departed", at="2026-09-21"))
    ledger.ingest(ev("eta", S.CARRIER_API, sid=sid, ref="evt-eta-1", eta="2026-11-10"))


TODAY = "2026-11-09"   # Monday


def build_fleet(ledger: ShipmentLedger) -> ShipmentLedger:
    """
    SHP-1  on the water, verified ETA slips 10th → 15th; two customer orders (rep-1, rep-2)
    SHP-2  arrived, free time expired, customs HOLD, docs missing, email *claims* release
    SHP-3  PO sent, no supplier confirmation, past requested ship date
    SHP-4  delivered with POD; freight invoiced 20% over quote
    SHP-5/6 warehouse appointments overlap on the same dock
    SHP-7  domestic, arrives Thursday (verified)
    """
    build_in_transit(ledger, sid="SHP-1")
    ledger.ingest(ev("eta", S.CARRIER_API, sid="SHP-1", ref="evt-eta-2", eta="2026-11-15"))
    allocate(ledger, sid="SHP-1", order="SO-501", customer="CUST-1", name="Bodega Uno", rep="rep-1",
             qty=600, promised="2026-11-14")
    allocate(ledger, sid="SHP-1", order="SO-502", customer="CUST-2", name="Casa Dos", rep="rep-2",
             qty=600, promised="2026-11-30")

    build_in_transit(ledger, sid="SHP-2", container="TCKU7654323", bol="MAEU240009999", po="PO-1002")
    ledger.ingest(ev("milestone", S.CARRIER_API, sid="SHP-2", code="vessel_arrived", at="2026-11-03"))
    ledger.ingest(ev("port_terms", S.TERMINAL_API, sid="SHP-2", last_free_day="2026-11-07",
                     demurrage_rate_per_day=175.0))
    ledger.ingest(ev("document", S.DOCUMENT_EXTRACTED, sid="SHP-2", doc_id="d-bol-2",
                     doc_type="bill_of_lading", status="validated"))
    ledger.ingest(ev("milestone", S.BROKER_EDI, sid="SHP-2", code="customs_hold", at="2026-11-04",
                     reason="Missing commercial invoice"))
    ledger.ingest(ev("milestone", S.EMAIL_PARSED, sid="SHP-2", code="customs_released", at="2026-11-08",
                     ref="gmail-msg-123"))
    allocate(ledger, sid="SHP-2", order="SO-601", customer="CUST-3", name="Tienda Tres", rep="rep-1",
             qty=300, promised="2026-11-12")

    create_import(ledger, sid="SHP-3", container="MAEU5550007", bol="", po="PO-1003")
    ledger.ingest(ev("shipment_updated", S.ERP, sid="SHP-3", requested_ship_date="2026-10-25"))
    ledger.ingest(ev("milestone", S.ERP, sid="SHP-3", code="po_sent", at="2026-11-01T09:00:00+00:00"))
    ledger.ingest(ev("milestone", S.EMAIL_PARSED, sid="SHP-3", code="supplier_confirmed", at="2026-11-02",
                     ref="gmail-msg-456"))

    create_import(ledger, sid="SHP-4", container="CMAU1002002", bol="CMDU77001", po="PO-0990",
                  mode="ground", supply_chain_type="domestic")
    allocate(ledger, sid="SHP-4", order="SO-401", customer="CUST-1", name="Bodega Uno", rep="rep-1",
             qty=1200, promised="2026-11-01")
    ledger.ingest(ev("milestone", S.WMS, sid="SHP-4", code="warehouse_received", at="2026-10-28"))
    ledger.ingest(ev("milestone", S.CARRIER_API, sid="SHP-4", code="delivered", at="2026-10-31",
                     order_id="SO-401"))
    ledger.ingest(ev("milestone", S.CARRIER_API, sid="SHP-4", code="pod_received", at="2026-10-31",
                     ref="epod-1", order_id="SO-401", doc_id="epod-1"))
    ledger.ingest(ev("cost", S.ERP, sid="SHP-4", ref="INV-4", component="drayage", amount_usd=3600.0))

    for sid, cont, start, end in (("SHP-5", "CSQU3054383", "2026-11-12T08:00:00", "2026-11-12T10:00:00"),
                                  ("SHP-6", "MSKU0000005", "2026-11-12T09:00:00", "2026-11-12T11:00:00")):
        create_import(ledger, sid=sid, container=cont, bol=f"BOL-{sid}", po=f"PO-{sid}",
                      mode="ground", supply_chain_type="domestic")
        ledger.ingest(ev("appointment", S.WMS, sid=sid, start=start, end=end, dock="D4", warehouse="WH-Miami"))

    create_import(ledger, sid="SHP-7", container="", bol="BOL-SHP-7", po="PO-1007",
                  mode="ground", supply_chain_type="domestic")
    ledger.ingest(ev("milestone", S.CARRIER_API, sid="SHP-7", code="vessel_departed", at="2026-11-08"))
    ledger.ingest(ev("eta", S.CARRIER_API, sid="SHP-7", eta="2026-11-12", location="warehouse"))
    ledger.ingest(ev("eta", S.EMAIL_PARSED, sid="SHP-2", ref="gmail-msg-789", eta="2026-11-11"))
    return ledger
