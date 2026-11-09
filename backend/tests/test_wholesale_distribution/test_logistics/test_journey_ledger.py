"""
Journey ledger: transitions, provenance, ETA, customs holds, duplicates,
tenant isolation and failure recovery.
"""
import pytest

from app.intelligence.wholesale_distribution.logistics.journey import (
    InMemoryEventStore, JourneyStage, LedgerError, ShipmentLedger, StoreUnavailableError,
)

from .journey_helpers import CO, CONTAINER, S, allocate, build_in_transit, create_import, ev, new_ledger


# ── Shipment transitions ─────────────────────────────────────────────────────


class TestTransitions:
    def test_verified_milestones_advance_stage(self):
        L = new_ledger()
        build_in_transit(L)
        s = L.get("SHP-1")
        assert s.current_stage == JourneyStage.IN_TRANSIT
        L.ingest(ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-10"))
        L.ingest(ev("milestone", S.BROKER_EDI, code="customs_released", at="2026-11-11"))
        assert L.get("SHP-1").current_stage == JourneyStage.CUSTOMS

    def test_all_fourteen_stages_reported_with_required_fields(self):
        L = new_ledger()
        build_in_transit(L)
        stages = L.get("SHP-1").stage_view()
        assert len(stages) == 14
        for st in stages:
            for key in ("status", "responsible_party", "expected_date", "actual_date",
                        "verified_milestones", "documents", "exceptions"):
                assert key in st
        by = {s["stage"]: s for s in stages}
        assert by[JourneyStage.SUPPLIER_CONFIRMATION.value]["status"] == "complete"
        assert by[JourneyStage.SUPPLIER_CONFIRMATION.value]["responsible_party"] == "supplier: Vinos del Sur SA"
        assert by[JourneyStage.CUSTOMS.value]["responsible_party"] == "broker: Rapid Customs"
        assert by[JourneyStage.DELIVERY.value]["status"] == "not_started"

    def test_domestic_shipment_marks_customs_and_port_not_applicable(self):
        L = new_ledger()
        create_import(L, mode="ground", supply_chain_type="domestic")
        by = {s["stage"]: s["status"] for s in L.get("SHP-1").stage_view()}
        assert by[JourneyStage.CUSTOMS.value] == "not_applicable"
        assert by[JourneyStage.PORT_ARRIVAL.value] == "not_applicable"

    def test_expected_vs_actual_dates(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("plan", S.ERP, expected_dates={"customs_released": "2026-11-12"}))
        L.ingest(ev("milestone", S.BROKER_EDI, code="customs_released", at="2026-11-14"))
        st = {s["stage"]: s for s in L.get("SHP-1").stage_view()}[JourneyStage.CUSTOMS.value]
        assert st["expected_date"] == "2026-11-12"
        assert st["actual_date"] == "2026-11-14"

    def test_pickup_before_customs_release_raises_sequence_exception(self):
        L = new_ledger()
        build_in_transit(L)
        r = L.ingest(ev("milestone", S.TERMINAL_API, code="gate_out_port", at="2026-11-12"))
        assert r.verified
        assert [e.code for e in r.exceptions] == ["SEQUENCE_ANOMALY"]

    def test_unknown_milestone_rejected(self):
        L = new_ledger()
        create_import(L)
        with pytest.raises(LedgerError):
            L.ingest(ev("milestone", S.CARRIER_API, code="teleported"))

    def test_event_for_unknown_shipment_rejected(self):
        with pytest.raises(LedgerError):
            new_ledger().ingest(ev("milestone", S.CARRIER_API, code="vessel_departed", sid="NOPE"))

    def test_events_on_cancelled_shipment_flagged(self):
        L = new_ledger()
        create_import(L)
        L.ingest(ev("shipment_updated", S.ERP, cancelled=True))
        r = L.ingest(ev("milestone", S.CARRIER_API, code="vessel_departed", at="2026-09-21"))
        assert "EVENT_AFTER_CANCEL" in [e.code for e in r.exceptions]


# ── Provenance: unverified sources never become authoritative ────────────────


class TestProvenance:
    @pytest.mark.parametrize("source", [S.EMAIL_PARSED, S.AI_INFERRED, S.DOCUMENT_EXTRACTED, S.MANUAL_NOTE])
    def test_unverified_release_is_evidence_only(self, source):
        L = new_ledger()
        build_in_transit(L)
        r = L.ingest(ev("milestone", source, code="customs_released", at="2026-11-11", ref="msg-77"))
        s = L.get("SHP-1")
        assert not r.verified and r.evidence_id
        assert "customs_released" not in s.milestones
        assert s.current_stage == JourneyStage.IN_TRANSIT
        assert [e.code for e in s.pending_evidence()] == ["customs_released"]

    def test_wrong_machine_source_for_milestone_is_not_authoritative(self):
        # A WMS cannot declare a customs release.
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("milestone", S.WMS, code="customs_released", at="2026-11-11"))
        assert not L.get("SHP-1").has("customs_released")

    def test_human_confirmation_promotes_evidence_with_audit(self):
        L = new_ledger()
        build_in_transit(L)
        r = L.ingest(ev("milestone", S.EMAIL_PARSED, code="customs_released", at="2026-11-11", ref="msg-77"))
        c = L.confirm_evidence(r.evidence_id, confirmed_by="user-ops-7")
        s = L.get("SHP-1")
        assert c.verified
        m = s.milestones["customs_released"]
        assert m.source == S.USER_CONFIRMED and m.confirmed_by == "user-ops-7"
        assert m.source_ref == "msg-77"
        assert not s.pending_evidence()
        assert s.audit[-1]["by"] == "user-ops-7"

    @pytest.mark.parametrize("actor", ["ai", "system", "lola", "", "  AI "])
    def test_ai_or_system_cannot_confirm(self, actor):
        L = new_ledger()
        build_in_transit(L)
        r = L.ingest(ev("milestone", S.EMAIL_PARSED, code="customs_released", at="2026-11-11"))
        with pytest.raises(LedgerError):
            L.confirm_evidence(r.evidence_id, confirmed_by=actor)

    def test_user_confirmed_event_requires_named_human(self):
        L = new_ledger()
        create_import(L)
        with pytest.raises(LedgerError):
            L.ingest(ev("milestone", S.USER_CONFIRMED, code="po_approved", by="system"))

    def test_conflicting_identifier_from_email_not_applied(self):
        L = new_ledger()
        create_import(L)
        r = L.ingest(ev("shipment_updated", S.EMAIL_PARSED, bol_number="FAKE999999"))
        assert L.get("SHP-1").bol_number == "MAEU240001234"
        assert [e.code for e in r.exceptions] == ["IDENTIFIER_CONFLICT"]


# ── ETA provenance ───────────────────────────────────────────────────────────


class TestEtaProvenance:
    def test_verified_eta_change_computes_delay(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("eta", S.CARRIER_API, ref="evt-eta-2", eta="2026-11-15"))
        s = L.get("SHP-1")
        assert s.baseline_eta().eta == "2026-11-10"
        assert s.current_eta().eta == "2026-11-15"
        assert s.eta_delay_days() == 5

    def test_email_eta_does_not_override_verified_eta(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("eta", S.EMAIL_PARSED, ref="msg-1", eta="2026-11-03"))
        s = L.get("SHP-1")
        assert s.current_eta().eta == "2026-11-10"
        assert s.reported_eta().eta == "2026-11-03"
        assert s.reported_eta().source == S.EMAIL_PARSED
        assert s.eta_delay_days() == 0
        d = s.to_dict()["eta"]
        assert d["current"]["verified"] is True and d["reported_unverified"]["verified"] is False

    def test_eta_requires_date(self):
        L = new_ledger()
        create_import(L)
        with pytest.raises(LedgerError):
            L.ingest(ev("eta", S.CARRIER_API, eta="soon"))


# ── Customs holds ────────────────────────────────────────────────────────────


class TestCustomsHolds:
    def test_hold_opens_critical_exception_and_release_resolves_it(self):
        L = new_ledger()
        build_in_transit(L)
        r = L.ingest(ev("milestone", S.BROKER_EDI, code="customs_hold", at="2026-11-11",
                        reason="FDA hold — label review"))
        assert r.exceptions[0].code == "CUSTOMS_HOLD" and r.exceptions[0].severity == "critical"
        stage = {s["stage"]: s for s in L.get("SHP-1").stage_view()}[JourneyStage.CUSTOMS.value]
        assert stage["status"] == "exception"
        L.ingest(ev("milestone", S.BROKER_EDI, code="customs_released", at="2026-11-14"))
        assert not [e for e in L.get("SHP-1").open_exceptions() if e.code == "CUSTOMS_HOLD"]

    def test_emailed_hold_is_not_an_official_hold(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("milestone", S.EMAIL_PARSED, code="customs_hold", at="2026-11-11"))
        s = L.get("SHP-1")
        assert not s.has("customs_hold") and not s.open_exceptions()
        assert s.pending_evidence()[0].code == "customs_hold"


# ── Duplicates / idempotency ─────────────────────────────────────────────────


class TestDuplicates:
    def test_same_event_twice_is_ignored(self):
        L = new_ledger()
        build_in_transit(L)
        e = ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-10", ref="ais-1")
        e2 = ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-10", ref="ais-1")
        assert L.ingest(e).applied
        r = L.ingest(e2)
        assert r.duplicate and not r.applied
        assert len([x for x in L.get("SHP-1").audit if x["code"] == "vessel_arrived"]) == 1

    def test_duplicate_freight_invoice_line_not_double_counted(self):
        L = new_ledger()
        create_import(L)
        L.ingest(ev("cost", S.FORWARDER_API, ref="INV-9", component="ocean_freight", amount_usd=2800))
        L.ingest(ev("cost", S.FORWARDER_API, ref="INV-9", component="ocean_freight", amount_usd=2800,
                    basis="invoiced", note="resend"))
        assert sum(c.amount_usd for c in L.get("SHP-1").costs) == 2800

    def test_later_duplicate_milestone_keeps_earliest_unless_correction(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-10"))
        L.ingest(ev("milestone", S.TERMINAL_API, code="vessel_arrived", at="2026-11-11"))
        assert L.get("SHP-1").milestone_date("vessel_arrived") == "2026-11-10"
        L.ingest(ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-12", correction=True))
        assert L.get("SHP-1").milestone_date("vessel_arrived") == "2026-11-12"


# ── Cross-company isolation ──────────────────────────────────────────────────


class TestTenantIsolation:
    def test_ledger_rejects_other_company_event(self):
        L = new_ledger()
        with pytest.raises(LedgerError):
            create_import(L, company="company-B")

    def test_rebuild_ignores_other_company_rows(self):
        store = InMemoryEventStore()
        a, b = ShipmentLedger("company-A", store), ShipmentLedger("company-B", store)
        create_import(a, sid="SHP-A")
        create_import(b, sid="SHP-B", company="company-B")
        rebuilt = ShipmentLedger.rebuild("company-A", store._rows and
                                         [__import__("app.intelligence.wholesale_distribution.logistics.journey",
                                                     fromlist=["LogisticsEvent"]).LogisticsEvent.from_dict(r)
                                          for r in store._rows])
        assert [s.shipment_id for s in rebuilt.shipments()] == ["SHP-A"]
        assert rebuilt.find(CONTAINER)[0].company_id == "company-A"

    def test_same_container_in_two_companies_never_crosses(self):
        store = InMemoryEventStore()
        a, b = ShipmentLedger("company-A", store), ShipmentLedger("company-B", store)
        create_import(a, sid="SHP-A")
        create_import(b, sid="SHP-B", company="company-B")
        assert [s.shipment_id for s in ShipmentLedger.load("company-B", store).find(CONTAINER)] == ["SHP-B"]


# ── Failure recovery ─────────────────────────────────────────────────────────


class TestFailureRecovery:
    def test_store_outage_leaves_state_unchanged_and_retry_succeeds(self):
        store = InMemoryEventStore()
        L = new_ledger(store)
        build_in_transit(L)
        before = L.get("SHP-1").to_dict()
        store.fail_next = True
        e = ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-10")
        with pytest.raises(StoreUnavailableError):
            L.ingest(e)
        assert L.get("SHP-1").to_dict() == before
        assert L.ingest(e).applied            # same event can be retried
        assert L.get("SHP-1").has("vessel_arrived")

    def test_rebuild_from_log_reproduces_state(self):
        store = InMemoryEventStore()
        L = new_ledger(store)
        build_in_transit(L)
        allocate(L)
        r = L.ingest(ev("milestone", S.EMAIL_PARSED, code="customs_released", at="2026-11-11"))
        L.confirm_evidence(r.evidence_id, "user-ops-7")
        L.ingest(ev("milestone", S.BROKER_EDI, code="customs_hold", at="2026-11-12", reason="exam"))
        rebuilt = ShipmentLedger.load(CO, store)
        a, b = L.get("SHP-1").to_dict(), rebuilt.get("SHP-1").to_dict()
        assert a == b

    def test_replaying_the_whole_log_twice_is_harmless(self):
        store = InMemoryEventStore()
        L = new_ledger(store)
        build_in_transit(L)
        rebuilt = ShipmentLedger.load(CO, store)
        for e in store.load(CO):
            assert rebuilt.ingest(e).duplicate
        assert rebuilt.get("SHP-1").to_dict() == L.get("SHP-1").to_dict()

    def test_malformed_historic_event_does_not_block_recovery(self):
        store = InMemoryEventStore()
        L = new_ledger(store)
        build_in_transit(L)
        bad = ev("milestone", S.CARRIER_API, code="not_a_code")
        store._rows.append(bad.to_dict())
        rebuilt = ShipmentLedger.load(CO, store)
        assert rebuilt.get("SHP-1").has("vessel_departed")
