"""
MANDATORY ACCEPTANCE SCENARIO

A purchase order expects 1,200 cases.  The warehouse receives 1,180 good
cases and 20 damaged cases.  Lola must reconcile the receipt, separate the
damaged inventory, identify the financial implications and prepare the
applicable claim — without inventing credits, inventory postings or
financial entries.  Stock changes go through the existing PolicyEngine +
ApprovalQueue.
"""
import pytest

from app.actions.approval_queue import ApprovalQueue
from app.domain.contracts import ActionClass
from app.domain.policy import PolicyEngine
from app.intelligence.wholesale_distribution.logistics.journey import SourceType
from app.intelligence.wholesale_distribution.logistics.receiving import (
    QUARANTINE_LOCATION, SALEABLE_LOCATION, ReceivedLine, ReceivingEvidence, ReceivingReconciler,
    submit_for_approval,
)

from .journey_helpers import CO, build_in_transit, ev, new_ledger


class RecordingDB:
    """Supabase-shaped fake: records every write, returns no policy rules."""

    def __init__(self):
        self.writes: list[tuple[str, str, dict]] = []
        self._table = None

    def table(self, name):
        self._table = name
        return self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self
    def lt(self, *a, **k): return self

    def insert(self, row):
        self.writes.append(("insert", self._table, row))
        return self

    def update(self, row):
        self.writes.append(("update", self._table, row))
        return self

    def upsert(self, row, **k):
        self.writes.append(("upsert", self._table, row))
        return self

    def execute(self):
        return type("R", (), {"data": []})()


def _scenario(evidence=None):
    L = new_ledger()
    build_in_transit(L)                  # PO-1001: 1,200 cases WINE-RED @ $24.00
    L.ingest(ev("milestone", SourceType.CARRIER_API, code="vessel_arrived", at="2026-11-10"))
    L.ingest(ev("milestone", SourceType.BROKER_EDI, code="customs_released", at="2026-11-11"))
    s = L.get("SHP-1")
    rec = ReceivingReconciler(CO).reconcile(
        s, [ReceivedLine("WINE-RED", good_cases=1180, damaged_cases=20, lot="L2026A")],
        received_by="user-wh-1", received_at="2026-11-13T09:00:00+00:00",
        evidence=evidence or ReceivingEvidence(),
    )
    return L, s, rec


class TestReconciliation:
    def test_quantities_reconcile_exactly(self):
        _, _, rec = _scenario()
        d = rec.to_dict()["totals"]
        assert d == {"expected_cases": 1200, "good_cases": 1180, "damaged_cases": 20,
                     "short_cases": 0, "over_cases": 0, "accounted_cases": 1200}
        assert rec.status == "discrepancy"
        line = rec.lines[0]
        assert (line.sku, line.lot) == ("WINE-RED", "L2026A")

    def test_damaged_inventory_separated_but_only_proposed(self):
        _, _, rec = _scenario()
        seg = rec.to_dict()["inventory_segregation"]
        assert seg[SALEABLE_LOCATION] == 1180
        assert seg[QUARANTINE_LOCATION] == 20
        assert seg["status"].startswith("PROPOSED")
        stock = [p for p in rec.proposals if p.capability == "inventory"]
        assert {(p.proposed_changes["location"], p.proposed_changes["quantity_cases"]) for p in stock} == {
            (SALEABLE_LOCATION, 1180), (QUARANTINE_LOCATION, 20)}
        assert all(p.action_class == ActionClass.COMMIT for p in stock)
        quarantine = next(p for p in stock if p.proposed_changes["location"] == QUARANTINE_LOCATION)
        assert quarantine.proposed_changes["saleable"] is False
        assert all(not p["executed"] for p in rec.to_dict()["proposals"])

    def test_financial_implications_stated_not_posted(self):
        _, _, rec = _scenario()
        f = rec.financial_implications()
        assert f["po_value_expected_usd"] == 28800.00
        assert f["saleable_value_received_usd"] == 28320.00
        assert f["damaged_value_at_po_cost_usd"] == 480.00      # 20 × $24.00
        assert f["short_value_at_po_cost_usd"] == 0.0
        assert f["effective_cost_per_saleable_case_usd"] == pytest.approx(28800 / 1180, abs=1e-4)
        assert f["status"].startswith("NOT POSTED")
        assert f["damaged_value_at_landed_cost_usd"] is None    # landed cost unknown → not invented

    def test_landed_cost_used_when_supplied(self):
        L, s, _ = _scenario()
        rec = ReceivingReconciler(CO).reconcile(
            s, [ReceivedLine("WINE-RED", 1180, 20)], "user-wh-1", landed_cost_per_case={"WINE-RED": 27.5})
        assert rec.financial_implications()["damaged_value_at_landed_cost_usd"] == 550.0

    def test_audit_trail_records_who_and_what(self):
        _, _, rec = _scenario()
        actions = [a["action"] for a in rec.audit_trail]
        assert actions == ["receipt_counted", "liability_assessed", "proposals_prepared"]
        assert rec.audit_trail[0]["actor"] == "user-wh-1"


class TestLiabilityAndClaims:
    def test_carrier_claim_when_damage_noted_at_delivery(self):
        _, _, rec = _scenario(ReceivingEvidence(
            damage_noted_on_delivery_receipt=True, seal_intact=True, seal_number_matches=True,
            damage_cause="transit_handling", delivering_carrier="Acme Drayage"))
        assert rec.liability.party == "carrier"
        [claim] = rec.claims
        assert claim.claim_type == "carrier_cargo_claim"
        assert claim.counterparty == "Acme Drayage"
        assert claim.claimed_amount_estimate_usd == 480.0
        assert claim.status == "draft" and claim.requires_approval
        assert "not sent" in claim.narrative.lower()
        # Carrier at fault → don't tell finance to hold the supplier's payment.
        recs = " ".join(rec.financial_implications()["recommendations"])
        assert "not disputed" in recs

    def test_supplier_claim_when_packaging_defect_and_seal_intact(self):
        _, _, rec = _scenario(ReceivingEvidence(
            damage_noted_on_delivery_receipt=False, seal_intact=True, seal_number_matches=True,
            damage_cause="packaging_or_manufacturing", photos_attached=True, damage_report_doc_id="d1"))
        assert rec.liability.party == "supplier"
        assert rec.claims[0].claim_type == "supplier_damage_claim"
        assert rec.claims[0].counterparty == "Vinos del Sur SA"
        assert "hold or short-pay" in " ".join(rec.financial_implications()["recommendations"])

    def test_undetermined_when_evidence_missing(self):
        _, _, rec = _scenario()
        assert rec.liability.party == "undetermined"
        assert rec.claims[0].claim_type == "pending_liability_damage_claim"
        assert "Photos of damaged cases and packaging" in rec.liability.evidence_needed

    def test_shortage_with_intact_seal_points_to_supplier(self):
        L = new_ledger()
        build_in_transit(L)
        rec = ReceivingReconciler(CO).reconcile(
            L.get("SHP-1"), [ReceivedLine("WINE-RED", 1170, 0)], "user-wh-1",
            ReceivingEvidence(seal_intact=True, seal_number_matches=True))
        assert rec.short == 30 and rec.liability.party == "supplier"
        assert rec.claims[0].claim_type == "supplier_shortage_claim"
        assert rec.claims[0].claimed_amount_estimate_usd == 720.0

    def test_broken_seal_points_to_carrier(self):
        L = new_ledger()
        build_in_transit(L)
        rec = ReceivingReconciler(CO).reconcile(
            L.get("SHP-1"), [ReceivedLine("WINE-RED", 1170, 0)], "user-wh-1",
            ReceivingEvidence(seal_intact=False))
        assert rec.liability.party == "carrier"

    def test_clean_receipt_no_claims(self):
        L = new_ledger()
        build_in_transit(L)
        rec = ReceivingReconciler(CO).reconcile(L.get("SHP-1"), [ReceivedLine("WINE-RED", 1200, 0)], "u")
        assert rec.status == "matched" and rec.claims == [] and rec.liability.party == "none"
        assert len(rec.proposals) == 1


class TestApprovalHandOff:
    async def test_proposals_queued_through_existing_policy_and_approval(self):
        _, _, rec = _scenario(ReceivingEvidence(damage_noted_on_delivery_receipt=True,
                                                damage_cause="transit_handling"))
        db = RecordingDB()
        results = await submit_for_approval(rec.proposals, PolicyEngine(CO, db), ApprovalQueue(db))
        assert {r["status"] for r in results} == {"pending_approval"}
        stock = [r for r in results if r["action_type"].startswith("logistics.post_inventory")]
        assert {r["policy_effect"] for r in stock} == {"require_approval"}   # COMMIT default
        tables = {t for _, t, _ in db.writes}
        assert tables == {"drafts"}                                          # nothing else touched
        assert not any(t in ("inventory", "invoices", "payments", "journal_entries") for t in tables)
        drafts = [row for _, _, row in db.writes]
        assert all(d["status"] == "pending" and d["company_id"] == CO for d in drafts)

    async def test_policy_deny_blocks_without_queueing(self):
        _, _, rec = _scenario()

        class DenyAll:
            async def evaluate(self, p):
                return type("D", (), {"effect": "deny", "rule_name": "freeze", "approval_roles": []})()

        db = RecordingDB()
        results = await submit_for_approval(rec.proposals, DenyAll(), ApprovalQueue(db))
        assert {r["status"] for r in results} == {"blocked_by_policy"}
        assert db.writes == []

    async def test_even_allow_is_queued_never_executed(self):
        _, _, rec = _scenario()

        class AllowAll:
            async def evaluate(self, p):
                return type("D", (), {"effect": "allow", "rule_name": "auto", "approval_roles": []})()

        db = RecordingDB()
        results = await submit_for_approval(rec.proposals, AllowAll(), ApprovalQueue(db))
        assert {r["status"] for r in results} == {"pending_approval"}
        assert {t for _, t, _ in db.writes} == {"drafts"}


class TestLedgerIntegration:
    def test_reconciliation_recorded_with_exception_and_claim(self):
        L, s, rec = _scenario(ReceivingEvidence(damage_noted_on_delivery_receipt=True,
                                                damage_cause="transit_handling"))
        r = L.ingest(ReceivingReconciler(CO).to_ledger_event(rec, SourceType.USER_CONFIRMED, "user-wh-1"))
        assert [e.code for e in r.exceptions] == ["RECEIPT_DAMAGE"]
        sh = L.get("SHP-1")
        assert sh.receipt["damaged_cases"] == 20 and sh.receipt["liability"] == "carrier"
        assert sh.receipt["claim_ids"] == [rec.claims[0].claim_id]

    def test_cross_company_shipment_rejected(self):
        L, s, _ = _scenario()
        with pytest.raises(PermissionError):
            ReceivingReconciler("company-B").reconcile(s, [ReceivedLine("WINE-RED", 1, 0)], "u")

    def test_negative_counts_rejected(self):
        L, s, _ = _scenario()
        with pytest.raises(ValueError):
            ReceivingReconciler(CO).reconcile(s, [ReceivedLine("WINE-RED", -5, 0)], "u")

    def test_unknown_sku_flagged_not_valued(self):
        L, s, _ = _scenario()
        rec = ReceivingReconciler(CO).reconcile(
            s, [ReceivedLine("WINE-RED", 1200, 0), ReceivedLine("MYSTERY", 5, 0)], "u")
        assert any("MYSTERY" in w for w in rec.warnings)
        assert rec.over == 5
