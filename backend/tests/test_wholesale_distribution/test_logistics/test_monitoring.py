"""Proactive monitoring: detection, prioritization, drafts that are never sent."""
from datetime import date

import pytest

from app.intelligence.wholesale_distribution.logistics.landed_cost import landed_cost
from app.intelligence.wholesale_distribution.logistics.monitoring import LogisticsMonitor

from .journey_helpers import S, build_fleet, build_in_transit, create_import, ev, new_ledger

TODAY = date(2026, 11, 9)


@pytest.fixture
def alerts():
    return LogisticsMonitor(build_fleet(new_ledger())).scan(TODAY)


def _codes(alerts, sid=None):
    return {a.code for a in alerts if sid is None or a.shipment_id == sid}


class TestDetections:
    def test_delayed_shipment(self, alerts):
        a = next(a for a in alerts if a.code == "SHIPMENT_DELAYED")
        assert a.shipment_id == "SHP-1" and "5 day" in a.title

    def test_late_supplier_confirmation_mentions_unverified_email(self, alerts):
        a = next(a for a in alerts if a.code == "SUPPLIER_CONFIRMATION_LATE")
        assert a.shipment_id == "SHP-3" and "unverified confirmation" in a.message

    def test_missing_bol_and_customs_docs(self, alerts):
        assert "MISSING_BOL" in _codes(alerts, "SHP-1")
        crit = next(a for a in alerts if a.code == "MISSING_CUSTOMS_DOCUMENTS" and a.shipment_id == "SHP-2")
        assert crit.severity == "critical" and "commercial_invoice" in crit.message

    def test_demurrage_accruing_with_exposure(self, alerts):
        a = next(a for a in alerts if a.code == "DEMURRAGE_ACCRUING")
        assert a.shipment_id == "SHP-2"
        assert a.financial_exposure_usd == 350.0          # 2 days × $175
        assert a.contains_commercial

    def test_demurrage_risk_before_last_free_day(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-06"))
        L.ingest(ev("port_terms", S.TERMINAL_API, last_free_day="2026-11-10"))
        a = next(a for a in LogisticsMonitor(L).scan(TODAY) if a.code == "DEMURRAGE_RISK")
        assert "NOT yet released" in a.message

    def test_detention_after_pickup(self):
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("milestone", S.BROKER_EDI, code="customs_released", at="2026-11-01"))
        L.ingest(ev("milestone", S.TERMINAL_API, code="gate_out_port", at="2026-11-02"))
        a = next(a for a in LogisticsMonitor(L).scan(TODAY) if a.code.startswith("DETENTION"))
        assert a.code == "DETENTION_ACCRUING" and a.financial_exposure_usd == 300.0   # 7 out − 4 free = 3 × $100

    def test_customs_hold(self, alerts):
        assert "CUSTOMS_HOLD" in _codes(alerts, "SHP-2")

    def test_appointment_conflict(self, alerts):
        a = next(a for a in alerts if a.code == "APPOINTMENT_CONFLICT")
        assert a.shipment_id == "SHP-6" and "D4" in a.title

    def test_customer_commitment_at_risk_has_customer_draft(self, alerts):
        a = next(a for a in alerts if a.code == "CUSTOMER_COMMITMENT_AT_RISK" and a.shipment_id == "SHP-1")
        assert a.customer_ids == ["CUST-1"]
        assert a.draft["to_role"] == "customer" and a.draft["send"] is False
        # SO-502 (promised 30th) is fine
        assert not any("SO-502" in x.title for x in alerts)

    def test_freight_variance(self, alerts):
        a = next(a for a in alerts if a.code == "FREIGHT_COST_VARIANCE")
        assert a.shipment_id == "SHP-4" and "+20.0%" in a.title

    def test_no_freight_variance_without_invoice(self, alerts):
        assert [a.shipment_id for a in alerts if a.code == "FREIGHT_COST_VARIANCE"] == ["SHP-4"]

    def test_overdue_po_only_without_progress(self, alerts):
        assert [a.shipment_id for a in alerts if a.code == "PO_OVERDUE"] == ["SHP-3"]

    def test_supplier_performance(self, alerts):
        a = next(a for a in alerts if a.code == "SUPPLIER_PERFORMANCE")
        assert "67%" in a.message

    def test_supplier_damage_rate_from_receipts(self):
        L = new_ledger()
        create_import(L)
        L.ingest(ev("receipt_reconciled", S.USER_CONFIRMED, by="u1",
                    summary={"expected_cases": 1200, "damaged_cases": 30, "short_cases": 0,
                             "liability": "supplier"}))
        a = next(a for a in LogisticsMonitor(L).scan(TODAY) if a.code == "SUPPLIER_PERFORMANCE")
        assert "2.5%" in a.message

    def test_unverified_reports_surface_for_review(self, alerts):
        refs = {a.message for a in alerts if a.code == "UNVERIFIED_REPORT"}
        assert any("gmail-msg-123" in m for m in refs)


class TestSafetyAndPriority:
    def test_drafts_never_sent(self, alerts):
        drafts = [a.draft for a in alerts if a.draft]
        assert drafts
        assert all(d["send"] is False and d["status"] == "draft" and d["requires_approval"] for d in drafts)

    def test_sorted_by_priority_critical_first(self, alerts):
        prios = [a.priority for a in alerts]
        assert prios == sorted(prios, reverse=True)
        assert alerts[0].severity == "critical"

    def test_unverified_facts_do_not_trigger_operational_alerts(self):
        # An emailed ETA slip must not create a delay alert.
        L = new_ledger()
        build_in_transit(L)
        L.ingest(ev("eta", S.EMAIL_PARSED, eta="2026-11-30"))
        assert "SHIPMENT_DELAYED" not in _codes(LogisticsMonitor(L).scan(TODAY))

    def test_alert_ids_stable_across_scans(self):
        L = build_fleet(new_ledger())
        a = [x.alert_id for x in LogisticsMonitor(L).scan(TODAY)]
        b = [x.alert_id for x in LogisticsMonitor(L).scan(TODAY)]
        assert a == b and len(set(a)) == len(a)


class TestLandedCost:
    def test_estimated_until_all_components_verified(self):
        L = new_ledger()
        create_import(L)                                  # 1,200 cs × $24
        L.ingest(ev("cost", S.FORWARDER_API, ref="F1", component="ocean_freight", amount_usd=3000))
        lc = landed_cost(L.get("SHP-1"))
        assert lc["status"] == "estimated"
        assert set(lc["missing_components"]) == {"customs_duty", "broker_fees", "drayage"}
        for comp, amt in (("customs_duty", 1440), ("broker_fees", 160), ("drayage", 600)):
            L.ingest(ev("cost", S.ERP, ref=f"X-{comp}", component=comp, amount_usd=amt))
        lc = landed_cost(L.get("SHP-1"))
        assert lc["status"] == "final"
        assert lc["total_landed_cost_usd"] == 28800 + 5200
        assert lc["landed_cost_per_case_usd"] == pytest.approx(34000 / 1200, abs=1e-4)

    def test_unverified_invoice_keeps_estimate(self):
        L = new_ledger()
        create_import(L)
        L.ingest(ev("cost", S.DOCUMENT_EXTRACTED, ref="F1", component="ocean_freight", amount_usd=3000))
        assert landed_cost(L.get("SHP-1"))["unverified_components"] == ["ocean_freight"]

    def test_per_saleable_case_after_damage(self):
        L = new_ledger()
        create_import(L)
        L.ingest(ev("cost", S.ERP, ref="F1", component="ocean_freight", amount_usd=1200))
        L.ingest(ev("receipt_reconciled", S.USER_CONFIRMED, by="u1",
                    summary={"good_cases": 1180, "damaged_cases": 20, "expected_cases": 1200}))
        lc = landed_cost(L.get("SHP-1"))
        assert lc["landed_cost_per_case_usd"] == 25.0
        assert lc["landed_cost_per_saleable_case_usd"] == pytest.approx(30000 / 1180, abs=1e-4)
