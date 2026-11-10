"""Regression tests for bugs fixed in the existing 9-stage engines, plus connector adapters."""
import pytest

from app.intelligence.wholesale_distribution.logistics.cost_reconciliation import CostReconciliationEngine
from app.intelligence.wholesale_distribution.logistics.customs import CustomsMonitor
from app.intelligence.wholesale_distribution.logistics.freight import FreightCostParser
from app.intelligence.wholesale_distribution.logistics.journey import ShipmentLedger, SourceType
from app.intelligence.wholesale_distribution.logistics.logistics_module import LogisticsModule
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    FreightBooking, FreightCostComponent, ProcurementCycle, ProcurementStage, PurchaseOrder, POLineItem,
)
from app.intelligence.wholesale_distribution.logistics.store import (
    SupabaseEventStore, parcel_tracking_to_events,
)

from .journey_helpers import CO, allocate, build_in_transit, new_ledger


class TestBrokerEmail:
    """Was: any email containing 'released' became a release — even 'NOT released'."""

    @pytest.mark.parametrize("subject,body,expected", [
        ("Status", "Cargo NOT released. Hold placed for FDA review.", "hold_placed"),
        ("Status", "Your cargo has been released by CBP.", "released"),
        ("Status", "Entry has been filed; release pending.", "entry_filed"),
        ("Status", "Container selected for exam.", "hold_placed"),
        ("Status", "We have not yet cleared the cargo.", None),
    ])
    def test_precedence(self, subject, body, expected):
        e = CustomsMonitor("c").parse_broker_email("BK", subject, body)
        assert (e.milestone if e else None) == expected

    def test_exam_reason(self):
        e = CustomsMonitor("c").parse_broker_email("BK", "CBP", "Container selected for intensive exam.")
        assert e.hold_reason == "Customs exam ordered"


class TestFreightParser:
    """Was: only the first invoice line was ever read."""

    def test_reads_every_line(self):
        comps = FreightCostParser().parse_invoice_text(
            "Ocean freight $2,800.00\nBAF $350.00\nDrayage $650.00\nCustoms broker fee $125.00", 1200)
        assert {c.component: c.amount_usd for c in comps} == {
            "ocean_freight": 2800.0, "fuel_surcharge": 350.0, "drayage": 650.0, "broker_fees": 125.0}


class TestLandedCostAccumulation:
    """Was: repeated components overwrote each other (second drayage leg lost)."""

    def test_repeated_component_summed(self):
        b = FreightBooking(cost_components=[
            FreightCostComponent("drayage", 600), FreightCostComponent("drayage", 400),
            FreightCostComponent("ocean_freight", 2000)])
        lcd = CostReconciliationEngine("c").build_landed_cost("PO", "A", 1000, "S", 10.0, b)
        assert lcd.drayage_per_case == pytest.approx(1.0)
        assert lcd.ocean_freight_per_case == pytest.approx(2.0)


class TestPipelineStatus:
    """Was: AttributeError (read fields that don't exist on ProcurementCycle)."""

    def test_pipeline_status_works(self):
        po = PurchaseOrder("PO-1", "SUP-1", "Sup", "a@b.c", "addr", "Net 30", "2026-09-01",
                           line_items=[POLineItem("A", "a", 100, 1.0, 100.0)])
        c = ProcurementCycle(company_id="c", purchase_order=po)
        c.advance_to(ProcurementStage.IN_TRANSIT)
        st = LogisticsModule("c").pipeline_status(c)
        assert st["current_stage"] == "5_in_transit" and st["supplier_id"] == "SUP-1"
        assert st["total_cases"] == 100

    def test_describe_lists_journey_and_never_executes(self):
        d = LogisticsModule("c").describe()
        assert len(d["journey"]["stages"]) == 14
        assert "stock adjustments" in d["journey"]["never_executes"]


class TestParcelAdapter:
    def test_stub_or_error_results_create_no_events(self):
        for status in ("credentials_not_configured", "error", "unknown"):
            assert parcel_tracking_to_events(CO, "SHP-1", "SO-501",
                                             {"tracking_number": "1Z999", "status": status}) == []

    def test_delivered_result_updates_order_and_milestone(self):
        L = new_ledger()
        build_in_transit(L)
        allocate(L)
        evs = parcel_tracking_to_events(CO, "SHP-1", "SO-501", {
            "tracking_number": "1Z999", "carrier": "ups", "status": "D", "description": "Delivered",
            "events": [{"timestamp": "2026-11-18T14:00:00Z"}]})
        for e in evs:
            L.ingest(e)
        s = L.get("SHP-1")
        a = next(a for a in s.allocations if a.order_id == "SO-501")
        assert a.status == "delivered" and a.delivery_source == "carrier_api"
        assert s.milestones["delivered"].source == SourceType.CARRIER_API


class _FakeTable:
    def __init__(self, rows):
        self.rows, self.filters, self.pending = rows, [], None

    def upsert(self, row, on_conflict="", **k):
        self.pending = row
        return self

    def select(self, *a): return self
    def order(self, *a, **k): return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def eq(self, c, v):
        self.filters.append((c, v))
        return self

    def execute(self):
        if self.pending is not None:
            if not any(r["company_id"] == self.pending["company_id"]
                       and r["idempotency_key"] == self.pending["idempotency_key"] for r in self.rows):
                self.rows.append(dict(self.pending))
            return type("R", (), {"data": []})()
        data = [r for r in self.rows if all(r.get(c) == v for c, v in self.filters)]
        if getattr(self, "window", None):
            data = data[self.window[0]:self.window[1] + 1]
        return type("R", (), {"data": data[:1000]})()      # PostgREST caps every response


class _FakeDB:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "logistics_events"
        return _FakeTable(self.rows)


class TestSupabaseStore:
    def test_round_trip_and_tenant_filter(self):
        db = _FakeDB()
        store = SupabaseEventStore(db)
        L = ShipmentLedger(CO, store)
        build_in_transit(L)
        other = ShipmentLedger("company-B", store)
        from .journey_helpers import create_import
        create_import(other, sid="SHP-B", company="company-B")
        rebuilt = ShipmentLedger.load(CO, store)
        assert [s.shipment_id for s in rebuilt.shipments()] == ["SHP-1"]
        assert rebuilt.get("SHP-1").to_dict() == L.get("SHP-1").to_dict()


class TestNoFakeVesselPosition:
    def test_stub_tracker_position_not_presented_as_real(self):
        from app.intelligence.wholesale_distribution.logistics.freight import TransitTracker
        ev_ = TransitTracker("c").check_shipment(
            FreightBooking(container_numbers=["MSCU1234566"], eta="2026-11-10"), [], {})
        assert ev_.vessel_lat is None and ev_.vessel_lon is None
        assert ev_.status_description == "no_live_tracking_feed"


class TestEventStorePagination:
    """The log is read page by page, so a large tenant never replays a partial history."""

    def _db(self, n, company="co-1"):
        db = _FakeDB()
        for i in range(n):
            db.rows.append({"event_id": f"e{i:06d}", "company_id": company, "idempotency_key": f"k{i}",
                            "inserted_at": f"2026-01-01T00:00:{i:06d}", "recorded_at": f"2026-01-01T{i:06d}"})
        return db

    def test_reads_every_page(self, monkeypatch):
        from app.intelligence.wholesale_distribution.logistics import store
        from app.intelligence.wholesale_distribution.logistics.journey import LogisticsEvent
        monkeypatch.setattr(LogisticsEvent, "from_dict", staticmethod(lambda row: row["event_id"]))
        db = self._db(2_345)
        got = store.SupabaseEventStore(db).load("co-1")
        assert len(got) == 2_345 and got[0] == "e000000" and got[-1] == "e002344"

    def test_exact_page_multiple(self, monkeypatch):
        from app.intelligence.wholesale_distribution.logistics import store
        from app.intelligence.wholesale_distribution.logistics.journey import LogisticsEvent
        monkeypatch.setattr(LogisticsEvent, "from_dict", staticmethod(lambda row: row["event_id"]))
        assert len(store.SupabaseEventStore(self._db(2_000)).load("co-1")) == 2_000

    def test_too_large_refuses_instead_of_truncating(self, monkeypatch):
        from app.intelligence.wholesale_distribution.logistics import store
        monkeypatch.setattr(store, "MAX_EVENTS", 2_000)
        with pytest.raises(store.LedgerTooLarge):
            store.SupabaseEventStore(self._db(2_500)).load("co-1")
