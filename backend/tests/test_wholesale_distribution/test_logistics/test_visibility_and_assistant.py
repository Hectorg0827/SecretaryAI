"""Role-scoped visibility and Lola's logistics answers (all 12 required questions)."""
import json
from datetime import date

import pytest

from app.intelligence.wholesale_distribution.logistics.assistant import LogisticsAssistant
from app.intelligence.wholesale_distribution.logistics.monitoring import LogisticsMonitor
from app.intelligence.wholesale_distribution.logistics.visibility import (
    LogisticsAccessDenied, LogisticsViewer,
)

from .journey_helpers import CO, S, build_fleet, ev, new_ledger

TODAY = date(2026, 11, 9)

OWNER = {"sub": "u-owner", "company_id": CO, "role": "owner"}
LOGISTICS = {"sub": "u-ops", "company_id": CO, "role": "back_office"}
REP1 = {"sub": "rep-1", "company_id": CO, "role": "sales_rep"}
REP2 = {"sub": "rep-2", "company_id": CO, "role": "sales_rep"}
VIEWER = {"sub": "u-v", "company_id": CO, "role": "viewer"}
OTHER_CO_OWNER = {"sub": "u-x", "company_id": "company-B", "role": "owner"}


@pytest.fixture(scope="module")
def ledger():
    return build_fleet(new_ledger())


def ask(ledger, user, q):
    return LogisticsAssistant(ledger, LogisticsViewer.from_user(user)).ask(q, as_of=TODAY)


# ── Visibility ───────────────────────────────────────────────────────────────


class TestVisibility:
    def test_tiers_follow_rbac(self):
        assert LogisticsViewer.from_user(OWNER).tier == "full_operations"
        assert LogisticsViewer.from_user(LOGISTICS).tier == "logistics"
        assert LogisticsViewer.from_user(REP1).tier == "sales"
        assert LogisticsViewer.from_user(VIEWER).tier == "none"
        assert LogisticsViewer.from_user(OWNER).can_view_commercial
        assert not LogisticsViewer.from_user(LOGISTICS).can_view_commercial

    def test_viewer_role_denied(self, ledger):
        with pytest.raises(LogisticsAccessDenied):
            LogisticsAssistant(ledger, LogisticsViewer.from_user(VIEWER))

    def test_other_company_cannot_see_or_ask(self, ledger):
        v = LogisticsViewer.from_user(OTHER_CO_OWNER)
        assert v.visible(ledger.shipments()) == []
        with pytest.raises(PermissionError):
            LogisticsAssistant(ledger, v)

    def test_logistics_staff_no_commercial_and_redacted_customers(self, ledger):
        d = LogisticsViewer.from_user(LOGISTICS).project(ledger.get("SHP-1"))
        blob = json.dumps(d)
        assert "commercial" not in d and "costs" not in d
        assert "unit_cost" not in blob and "Net 60" not in blob
        assert {a["customer_name"] for a in d["allocations"]} == {"[redacted]"}
        assert sum(a["qty_cases"] for a in d["allocations"]) == 1200   # quantities still visible
        assert d["milestones"]                                          # operational data present

    def test_logistics_staff_assigned_shipment_shows_customers(self, ledger):
        v = LogisticsViewer.from_user({**LOGISTICS, "assigned_shipment_ids": ["SHP-1"]})
        names = {a["customer_name"] for a in v.project(ledger.get("SHP-1"))["allocations"]}
        assert names == {"Bodega Uno", "Casa Dos"}

    def test_sales_rep_sees_only_own_customers(self, ledger):
        v = LogisticsViewer.from_user(REP2)
        assert [s.shipment_id for s in v.visible(ledger.shipments())] == ["SHP-1"]
        d = v.project(ledger.get("SHP-1"))
        blob = json.dumps(d)
        assert [a["order_id"] for a in d["allocations"]] == ["SO-502"]
        assert "Bodega Uno" not in blob and "CUST-1" not in blob
        for k in ("commercial", "costs", "supplier_name", "po_numbers", "pending_evidence"):
            assert k not in d
        assert "unit_cost" not in blob and "Vinos" not in blob

    def test_sales_rep_fails_closed_without_assignment(self, ledger):
        L = new_ledger()
        from .journey_helpers import create_import
        create_import(L)
        L.ingest(ev("allocation", S.ERP, order_id="SO-9", customer_id="CUST-9", sku="WINE-RED",
                    qty_cases=10))                  # no sales_rep_id
        assert LogisticsViewer.from_user(REP1).visible(L.shipments()) == []

    def test_sales_rep_by_customer_claim(self, ledger):
        v = LogisticsViewer.from_user({**REP2, "customer_ids": ["CUST-3"]})
        assert {s.shipment_id for s in v.visible(ledger.shipments())} == {"SHP-1", "SHP-2"}

    def test_alert_filtering_by_role(self, ledger):
        alerts = LogisticsMonitor(ledger).scan(TODAY)
        idx = {s.shipment_id: s for s in ledger.shipments()}
        ops = LogisticsViewer.from_user(LOGISTICS).filter_alerts(alerts, idx)
        assert not any(a["code"] in ("SUPPLIER_PERFORMANCE", "FREIGHT_COST_VARIANCE") for a in ops)
        dem = next(a for a in ops if a["code"] == "DEMURRAGE_ACCRUING")
        assert dem["financial_exposure_usd"] is None
        rep2 = LogisticsViewer.from_user(REP2).filter_alerts(alerts, idx)
        assert {a["code"] for a in rep2} <= {"SHIPMENT_DELAYED", "CUSTOMS_HOLD", "CUSTOMER_COMMITMENT_AT_RISK"}
        assert not any("CUST-1" in a["customer_ids"] for a in rep2)
        assert not any(a["code"] == "CUSTOMER_COMMITMENT_AT_RISK" for a in rep2)   # SO-501 is rep-1's
        rep1 = LogisticsViewer.from_user(REP1).filter_alerts(alerts, idx)
        assert any(a["code"] == "CUSTOMER_COMMITMENT_AT_RISK" and a["customer_ids"] == ["CUST-1"] for a in rep1)


# ── The 12 questions ─────────────────────────────────────────────────────────


class TestQuestions:
    def test_where_is_my_container(self, ledger):
        a = ask(ledger, OWNER, "Where is my container MSCU1234566?")
        assert a.intent == "where_is"
        assert "verified ETA 2026-11-15" in a.answer and "carrier_api" in a.answer
        # Allocated to customers, but physically still on the water:
        assert "Container & carrier movement" in a.answer and "Departed origin" in a.answer
        assert a.links[0]["orders"] == ["SO-501", "SO-502"]

    def test_where_is_reports_unverified_eta_separately(self, ledger):
        a = ask(ledger, OWNER, "where is container TCKU7654323")
        assert "unverified report says 2026-11-11" in a.answer and "CUSTOMS HOLD" in a.answer
        assert "gmail-msg-123" in a.links[0]["communications"]

    def test_delayed(self, ledger):
        a = ask(ledger, OWNER, "What shipments are delayed?")
        assert a.intent == "delayed" and "MSCU1234566 delayed 5 day" in a.answer

    def test_arriving_this_week(self, ledger):
        a = ask(ledger, OWNER, "Which shipments arrive this week?")
        assert a.intent == "arriving_this_week"
        # Mon 9th → Sun 15th: SHP-7 (12th) and SHP-1 (verified 15th after its delay)
        assert [x["shipment_id"] for x in a.data["verified"]] == ["SHP-7", "SHP-1"]
        assert a.data["window"] == ["2026-11-09", "2026-11-15"]
        # SHP-2's emailed ETA is listed only as unverified (and it has already arrived)
        assert all(x["shipment_id"] != "SHP-2" for x in a.data["verified"])

    def test_demurrage(self, ledger):
        a = ask(ledger, OWNER, "Which containers are at risk of demurrage?")
        assert a.intent == "demurrage" and "TCKU7654323" in a.answer and "$350" in a.answer

    def test_demurrage_no_dollars_for_logistics_staff(self, ledger):
        a = ask(ledger, LOGISTICS, "Which containers are at risk of demurrage?")
        assert "$350" not in a.answer and a.restricted

    def test_missing_customs_documents(self, ledger):
        a = ask(ledger, OWNER, "What customs documents are missing?")
        rows = {r["shipment_id"]: r for r in a.data["shipments"]}
        assert rows["SHP-2"]["missing"] == ["commercial_invoice", "packing_list"]

    def test_broker_released_email_is_not_trusted(self, ledger):
        a = ask(ledger, OWNER, "Has the broker released the cargo for TCKU7654323?")
        assert a.intent == "broker_released"
        assert "NO — on customs hold" in a.answer
        assert a.data["shipments"][0]["released"] is False
        assert a.data["shipments"][0]["unverified_release_report"]["source"] == "email_parsed"

    def test_broker_released_unverified_only(self):
        L = new_ledger()
        from .journey_helpers import build_in_transit
        build_in_transit(L)
        L.ingest(ev("milestone", S.CARRIER_API, code="vessel_arrived", at="2026-11-08"))
        L.ingest(ev("milestone", S.EMAIL_PARSED, code="customs_released", at="2026-11-09", ref="m1"))
        a = ask(L, OWNER, "Has the broker released the cargo?")
        assert "NOT CONFIRMED" in a.answer

    def test_overdue_pos(self, ledger):
        a = ask(ledger, OWNER, "Which purchase orders are overdue?")
        assert a.intent == "overdue_pos" and "PO-1003" in a.answer

    def test_overdue_pos_hidden_from_sales(self, ledger):
        assert ask(ledger, REP1, "Which purchase orders are overdue?").restricted

    def test_why_hasnt_customer_received_order(self, ledger):
        a = ask(ledger, OWNER, "Why hasn't Tienda Tres received their order?")
        assert a.intent == "customer_not_received"
        assert "customs hold" in a.answer and "SO-601" in a.answer

    def test_customer_order_by_number_for_rep(self, ledger):
        a = ask(ledger, REP2, "Why hasn't the customer received order SO-502?")
        assert "SO-502" in a.answer and "running 5 day(s) late" in a.answer

    def test_rep_cannot_query_other_reps_order(self, ledger):
        a = ask(ledger, REP2, "Why hasn't the customer received order SO-501?")
        assert "SO-501" not in json.dumps(a.data) and "couldn't find" in a.answer

    def test_in_transit_inventory(self, ledger):
        a = ask(ledger, OWNER, "What inventory is currently in transit?")
        assert a.data["by_sku"]["WINE-RED"]["in_transit"] == 1200 + 1200   # SHP-1 + SHP-7
        assert a.data["by_sku"]["WINE-RED"]["at_port"] == 1200             # SHP-2

    def test_proof_of_delivery(self, ledger):
        a = ask(ledger, OWNER, "Which deliveries have proof of delivery?")
        assert [r["order_id"] for r in a.data["with_pod"]] == ["SO-401"]

    def test_landed_cost_owner(self, ledger):
        a = ask(ledger, OWNER, "What is the landed cost per case for PO-0990?")
        lc = a.data["landed_costs"][0]
        assert lc["landed_cost_per_case_usd"] == 27.0                    # (28,800 + 3,600) / 1,200
        assert lc["status"] == "final"

    def test_landed_cost_restricted(self, ledger):
        for u in (LOGISTICS, REP1):
            a = ask(ledger, u, "What is the landed cost per case?")
            assert a.restricted and "$" not in a.answer

    def test_supplier_responsibility(self):
        L = new_ledger()
        from .journey_helpers import create_import
        create_import(L)
        L.ingest(ev("receipt_reconciled", S.USER_CONFIRMED, by="u1",
                    summary={"expected_cases": 1200, "good_cases": 1180, "damaged_cases": 20,
                             "short_cases": 0, "liability": "carrier", "claim_ids": ["clm-1"]}))
        a = ask(L, OWNER, "Which suppliers are responsible for shortages or damage?")
        assert a.intent == "supplier_responsibility"
        assert "20 damaged" in a.answer and "likely responsible: carrier" in a.answer
        assert ask(L, REP1, "Which suppliers are responsible for damage?").restricted

    def test_unknown_question_offers_help(self, ledger):
        assert ask(ledger, OWNER, "tell me a joke").intent == "unknown"
