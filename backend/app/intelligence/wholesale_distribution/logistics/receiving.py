"""
Warehouse receiving reconciliation, damage segregation and claim preparation.

What this module does
─────────────────────
• Compares what a PO expected with what the warehouse counted, per SKU / lot:
  good, damaged, short, over.
• Separates damaged cases from saleable stock — as *proposed* postings to a
  quarantine location, never as applied stock changes.
• States the financial exposure of the discrepancy at PO cost (and landed
  cost where known), clearly labelled as an estimate.  It never records a
  credit, a payable adjustment or a journal entry.
• Assesses who is likely responsible (supplier vs carrier) from the evidence
  available, and says what evidence is missing.
• Prepares a claim *draft* for review.  Nothing is sent.

What it hands off
─────────────────
Every stock change and every claim becomes an ``ActionProposal`` (Workstream 1
contract, ``app.domain.contracts``) with ``ActionClass.COMMIT`` (stock) or
``ActionClass.DRAFT`` (claim).  ``submit_for_approval`` runs each through the
tenant's ``PolicyEngine`` and parks it in the existing ``ApprovalQueue``
(``drafts`` table).  Execution after approval belongs to the existing
transactional mechanisms — this module has no code path that writes stock,
money or external messages.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.domain.contracts import ActionClass, ActionProposal, DataPath

from .journey import LogisticsEvent, Shipment, SourceType


SALEABLE_LOCATION = "AVAILABLE"
QUARANTINE_LOCATION = "QUARANTINE-DAMAGED"


@dataclass
class ReceivedLine:
    sku: str
    good_cases: int = 0
    damaged_cases: int = 0
    lot: str = ""
    damage_description: str = ""


@dataclass
class ReceivingEvidence:
    """Facts that decide liability.  Unknown is a valid answer (None)."""
    damage_noted_on_delivery_receipt: Optional[bool] = None   # exception written on POD/BOL at delivery
    seal_intact: Optional[bool] = None
    seal_number_matches: Optional[bool] = None
    damage_cause: str = "unknown"        # transit_handling | packaging_or_manufacturing | unknown
    photos_attached: bool = False
    damage_report_doc_id: str = ""
    delivering_carrier: str = ""


@dataclass
class ReconciledLine:
    sku: str
    lot: str
    expected_cases: int
    good_cases: int
    damaged_cases: int
    short_cases: int
    over_cases: int
    unit_cost: float
    landed_cost_per_case: Optional[float]

    @property
    def status(self) -> str:
        if self.short_cases == 0 and self.over_cases == 0 and self.damaged_cases == 0:
            return "matched"
        return "discrepancy"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status
        return d


@dataclass
class LiabilityAssessment:
    party: str                           # supplier | carrier | undetermined | none
    confidence: str                      # high | medium | low
    rationale: list[str] = field(default_factory=list)
    evidence_needed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClaimDraft:
    claim_id: str
    claim_type: str                      # carrier_cargo_claim | supplier_damage_claim | supplier_shortage_claim
    counterparty: str
    po_numbers: list[str]
    bol_number: str
    container_numbers: list[str]
    lines: list[dict]
    claimed_amount_estimate_usd: float
    amount_basis: str
    supporting_documents_required: list[str]
    supporting_documents_on_file: list[str]
    filing_guidance: str
    narrative: str
    status: str = "draft"                # draft only — never sent from here
    requires_approval: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReceiptReconciliation:
    reconciliation_id: str
    company_id: str
    shipment_id: str
    po_numbers: list[str]
    received_at: str
    received_by: str
    lines: list[ReconciledLine]
    liability: LiabilityAssessment
    claims: list[ClaimDraft]
    proposals: list[ActionProposal]
    audit_trail: list[dict]
    warnings: list[str] = field(default_factory=list)

    # ── Totals ───────────────────────────────────────────────────────────────

    @property
    def expected(self) -> int:
        return sum(l.expected_cases for l in self.lines)

    @property
    def good(self) -> int:
        return sum(l.good_cases for l in self.lines)

    @property
    def damaged(self) -> int:
        return sum(l.damaged_cases for l in self.lines)

    @property
    def short(self) -> int:
        return sum(l.short_cases for l in self.lines)

    @property
    def over(self) -> int:
        return sum(l.over_cases for l in self.lines)

    @property
    def status(self) -> str:
        return "matched" if all(l.status == "matched" for l in self.lines) else "discrepancy"

    def financial_implications(self) -> dict:
        damaged_cost = sum(l.damaged_cases * l.unit_cost for l in self.lines)
        short_cost = sum(l.short_cases * l.unit_cost for l in self.lines)
        landed_known = all(l.landed_cost_per_case is not None for l in self.lines if l.damaged_cases or l.short_cases)
        damaged_landed = sum(l.damaged_cases * (l.landed_cost_per_case or 0) for l in self.lines)
        short_landed = sum(l.short_cases * (l.landed_cost_per_case or 0) for l in self.lines)
        billed_value = sum(l.expected_cases * l.unit_cost for l in self.lines)
        saleable_value = sum(l.good_cases * l.unit_cost for l in self.lines)
        return {
            "basis": "PO unit cost per case (estimate)",
            "po_value_expected_usd": round(billed_value, 2),
            "saleable_value_received_usd": round(saleable_value, 2),
            "damaged_value_at_po_cost_usd": round(damaged_cost, 2),
            "short_value_at_po_cost_usd": round(short_cost, 2),
            "total_exposure_at_po_cost_usd": round(damaged_cost + short_cost, 2),
            "damaged_value_at_landed_cost_usd": round(damaged_landed, 2) if landed_known else None,
            "short_value_at_landed_cost_usd": round(short_landed, 2) if landed_known else None,
            "effective_cost_per_saleable_case_usd": (
                round(billed_value / self.good, 4) if self.good else None
            ),
            "recommendations": _finance_recommendations(self, damaged_cost + short_cost),
            "status": "NOT POSTED — no credit, payable adjustment or journal entry has been made",
        }

    def to_dict(self) -> dict:
        return {
            "reconciliation_id": self.reconciliation_id,
            "shipment_id": self.shipment_id,
            "po_numbers": self.po_numbers,
            "received_at": self.received_at,
            "received_by": self.received_by,
            "status": self.status,
            "totals": {
                "expected_cases": self.expected, "good_cases": self.good,
                "damaged_cases": self.damaged, "short_cases": self.short,
                "over_cases": self.over,
                "accounted_cases": self.good + self.damaged,
            },
            "lines": [l.to_dict() for l in self.lines],
            "inventory_segregation": {
                SALEABLE_LOCATION: self.good,
                QUARANTINE_LOCATION: self.damaged,
                "status": "PROPOSED — requires approval through the inventory approval workflow",
            },
            "financial_implications": self.financial_implications(),
            "liability": self.liability.to_dict(),
            "claims": [c.to_dict() for c in self.claims],
            "proposals": [_proposal_dict(p) for p in self.proposals],
            "audit_trail": self.audit_trail,
            "warnings": self.warnings,
        }


class ReceivingReconciler:
    """Reconcile a warehouse count against the PO on a shipment."""

    def __init__(self, company_id: str):
        self.company_id = company_id

    def reconcile(
        self,
        shipment: Shipment,
        received: list[ReceivedLine],
        received_by: str,
        evidence: Optional[ReceivingEvidence] = None,
        received_at: str = "",
        landed_cost_per_case: Optional[dict[str, float]] = None,
    ) -> ReceiptReconciliation:
        if shipment.company_id != self.company_id:
            raise PermissionError("shipment belongs to a different company")
        evidence = evidence or ReceivingEvidence()
        received_at = received_at or datetime.now(timezone.utc).isoformat()
        landed_cost_per_case = landed_cost_per_case or {}
        warnings: list[str] = []
        audit = [_audit("receipt_counted", received_by, {
            "lines": [asdict(r) for r in received], "evidence": asdict(evidence)})]

        for r in received:
            if r.good_cases < 0 or r.damaged_cases < 0:
                raise ValueError(f"negative case count for {r.sku}")

        expected_by_sku: dict[str, dict] = {}
        for l in shipment.lines:
            e = expected_by_sku.setdefault(l.sku.upper(), {"qty": 0, "unit_cost": l.unit_cost, "lot": l.lot})
            e["qty"] += l.qty_cases
        received_by_sku: dict[str, dict] = {}
        for r in received:
            agg = received_by_sku.setdefault(r.sku.upper(), {"good": 0, "damaged": 0, "lots": []})
            agg["good"] += r.good_cases
            agg["damaged"] += r.damaged_cases
            if r.lot and r.lot not in agg["lots"]:
                agg["lots"].append(r.lot)

        lines: list[ReconciledLine] = []
        for sku in list(expected_by_sku) + [s for s in received_by_sku if s not in expected_by_sku]:
            exp = expected_by_sku.get(sku, {"qty": 0, "unit_cost": 0.0, "lot": ""})
            got = received_by_sku.get(sku, {"good": 0, "damaged": 0, "lots": []})
            accounted = got["good"] + got["damaged"]
            if sku not in expected_by_sku:
                warnings.append(f"SKU {sku} received but not on PO — held for review, not valued")
            if exp["lot"] and got["lots"] and exp["lot"] not in got["lots"]:
                warnings.append(f"SKU {sku}: received lot(s) {got['lots']} differ from PO lot {exp['lot']}")
            lines.append(ReconciledLine(
                sku=sku, lot=",".join(got["lots"]) or exp["lot"],
                expected_cases=exp["qty"], good_cases=got["good"], damaged_cases=got["damaged"],
                short_cases=max(0, exp["qty"] - accounted), over_cases=max(0, accounted - exp["qty"]),
                unit_cost=float(exp["unit_cost"] or 0.0),
                landed_cost_per_case=landed_cost_per_case.get(sku),
            ))
            if not exp["unit_cost"] and (got["damaged"] or exp["qty"] > accounted):
                warnings.append(f"SKU {sku}: no PO unit cost on file — financial exposure understated")

        liability = assess_liability(lines, evidence, shipment)
        audit.append(_audit("liability_assessed", "system", liability.to_dict()))

        rec = ReceiptReconciliation(
            reconciliation_id=f"rcv-{uuid.uuid4().hex[:12]}", company_id=self.company_id,
            shipment_id=shipment.shipment_id, po_numbers=list(shipment.po_numbers),
            received_at=received_at, received_by=received_by, lines=lines,
            liability=liability, claims=[], proposals=[], audit_trail=audit, warnings=warnings,
        )
        rec.claims = build_claims(rec, shipment, evidence)
        rec.proposals = build_proposals(rec, shipment, received_by)
        rec.audit_trail.append(_audit("proposals_prepared", "system", {
            "proposal_count": len(rec.proposals), "claim_drafts": [c.claim_id for c in rec.claims]}))
        return rec

    def to_ledger_event(self, rec: ReceiptReconciliation, source: SourceType,
                        confirmed_by: str = "") -> LogisticsEvent:
        """Record the reconciliation summary and its exceptions on the shipment."""
        exceptions = []
        if rec.damaged:
            exceptions.append({"code": "RECEIPT_DAMAGE", "severity": "alert",
                               "message": f"{rec.damaged} damaged case(s) received; segregated to quarantine "
                                          f"(proposed). Likely responsible: {rec.liability.party}.",
                               "data": {"damaged_cases": rec.damaged, "liability": rec.liability.party}})
        if rec.short:
            exceptions.append({"code": "RECEIPT_SHORTAGE", "severity": "alert",
                               "message": f"{rec.short} case(s) short against PO.",
                               "data": {"short_cases": rec.short}})
        if rec.over:
            exceptions.append({"code": "RECEIPT_OVERAGE", "severity": "warning",
                               "message": f"{rec.over} case(s) over PO quantity.",
                               "data": {"over_cases": rec.over}})
        return LogisticsEvent(
            company_id=rec.company_id, shipment_id=rec.shipment_id, kind="receipt_reconciled",
            source=source, occurred_at=rec.received_at, source_ref=rec.reconciliation_id,
            confirmed_by=confirmed_by,
            payload={"summary": {
                "reconciliation_id": rec.reconciliation_id, "status": rec.status,
                "expected_cases": rec.expected, "good_cases": rec.good,
                "damaged_cases": rec.damaged, "short_cases": rec.short, "over_cases": rec.over,
                "liability": rec.liability.party,
                "claim_ids": [c.claim_id for c in rec.claims],
            }, "exceptions": exceptions},
        )


# ─── Liability ────────────────────────────────────────────────────────────────


def assess_liability(lines: list[ReconciledLine], ev: ReceivingEvidence, shipment: Shipment) -> LiabilityAssessment:
    damaged = sum(l.damaged_cases for l in lines)
    short = sum(l.short_cases for l in lines)
    if not damaged and not short:
        return LiabilityAssessment("none", "high", ["Receipt matches the PO."])

    rationale, needed = [], []
    supplier_pts = carrier_pts = 0

    if ev.seal_intact is False or ev.seal_number_matches is False:
        carrier_pts += 2
        rationale.append("Seal broken or mismatched on arrival — points to loss/damage in carrier custody.")
    elif ev.seal_intact and ev.seal_number_matches:
        if short:
            supplier_pts += 2
            rationale.append("Seal intact and matching, yet cases short — points to short-shipment at origin.")
    else:
        needed.append("Seal number and condition recorded at container opening")

    if damaged:
        if ev.damage_cause == "packaging_or_manufacturing":
            supplier_pts += 2
            rationale.append("Damage attributed to packaging/manufacturing — supplier quality issue.")
        elif ev.damage_cause == "transit_handling":
            carrier_pts += 1
            rationale.append("Damage consistent with transit handling.")
        else:
            needed.append("Inspection finding on damage cause (transit handling vs packaging)")

        if ev.damage_noted_on_delivery_receipt is True:
            carrier_pts += 2
            rationale.append("Damage was noted on the delivery receipt at hand-off — preserves a carrier claim.")
        elif ev.damage_noted_on_delivery_receipt is False:
            rationale.append("Damage NOT noted at delivery (concealed damage) — carrier claim is weaker; "
                             "notify carrier promptly.")
            supplier_pts += 1 if ev.damage_cause != "transit_handling" else 0
        else:
            needed.append("Copy of signed delivery receipt / POD showing whether exceptions were noted")
        if not ev.photos_attached:
            needed.append("Photos of damaged cases and packaging")
        if not ev.damage_report_doc_id:
            needed.append("Warehouse damage (OS&D) report")

    if supplier_pts == carrier_pts:
        party, conf = "undetermined", "low"
        rationale.append("Evidence does not yet separate supplier from carrier responsibility.")
    else:
        party = "supplier" if supplier_pts > carrier_pts else "carrier"
        conf = "high" if abs(supplier_pts - carrier_pts) >= 3 else "medium"
    return LiabilityAssessment(party, conf, rationale, needed)


# ─── Claims ───────────────────────────────────────────────────────────────────


def build_claims(rec: ReceiptReconciliation, shipment: Shipment, ev: ReceivingEvidence) -> list[ClaimDraft]:
    claims: list[ClaimDraft] = []
    party = rec.liability.party
    docs_on_file = sorted(shipment.document_types())

    def _lines(kind: str) -> list[dict]:
        out = []
        for l in rec.lines:
            qty = l.damaged_cases if kind == "damage" else l.short_cases
            if qty:
                out.append({"sku": l.sku, "lot": l.lot, "cases": qty, "unit_cost": l.unit_cost,
                            "extended_cost": round(qty * l.unit_cost, 2)})
        return out

    for kind in ("damage", "shortage"):
        lines = _lines(kind)
        if not lines:
            continue
        amount = round(sum(x["extended_cost"] for x in lines), 2)
        cases = sum(x["cases"] for x in lines)
        if party == "carrier":
            ctype, counterparty = "carrier_cargo_claim", ev.delivering_carrier or shipment.carrier or "carrier"
            required = ["bill_of_lading", "commercial_invoice", "packing_list", "proof_of_delivery",
                        "damage_report", "photos"]
            guidance = ("Notify the carrier in writing immediately. Typical limits: US domestic truck "
                        "(Carmack) claims within 9 months of delivery; ocean (COGSA) written notice of "
                        "concealed damage within 3 days and suit within 1 year. Confirm against the "
                        "contract / bill of lading terms before filing.")
        elif party == "supplier":
            ctype = "supplier_damage_claim" if kind == "damage" else "supplier_shortage_claim"
            counterparty = shipment.supplier_name or shipment.supplier_id or "supplier"
            required = ["commercial_invoice", "packing_list", "warehouse_receipt", "damage_report", "photos"]
            guidance = ("File under the supplier agreement's quality / shortage terms. Check the "
                        "notification window in the purchase terms.")
        else:
            ctype = f"pending_liability_{kind}_claim"
            counterparty = "to be determined (supplier or carrier)"
            required = ["bill_of_lading", "commercial_invoice", "packing_list", "proof_of_delivery",
                        "warehouse_receipt", "damage_report", "photos"]
            guidance = ("Liability not yet determined — gather the evidence listed before choosing "
                        "between a supplier claim and a carrier claim. Send a precautionary notice to "
                        "the carrier to protect the claim window.")
        narrative = (
            f"Receipt of PO {', '.join(rec.po_numbers) or 'n/a'} "
            f"(B/L {shipment.bol_number or 'n/a'}, container {', '.join(shipment.container_numbers) or 'n/a'}): "
            f"expected {rec.expected} cases, counted {rec.good} good and {rec.damaged} damaged"
            f"{f', {rec.short} short' if rec.short else ''}. "
            f"This draft covers {cases} {kind} case(s) valued at ${amount:,.2f} at PO cost. "
            f"DRAFT FOR INTERNAL REVIEW — not sent."
        )
        claims.append(ClaimDraft(
            claim_id=f"clm-{uuid.uuid4().hex[:10]}", claim_type=ctype, counterparty=counterparty,
            po_numbers=list(rec.po_numbers), bol_number=shipment.bol_number,
            container_numbers=list(shipment.container_numbers), lines=lines,
            claimed_amount_estimate_usd=amount, amount_basis="PO unit cost per case",
            supporting_documents_required=required,
            supporting_documents_on_file=[d for d in required if d in docs_on_file],
            filing_guidance=guidance, narrative=narrative,
        ))
    return claims


# ─── Proposals (handed to the existing approval mechanisms) ──────────────────


def build_proposals(rec: ReceiptReconciliation, shipment: Shipment, requested_by: str) -> list[ActionProposal]:
    props: list[ActionProposal] = []
    corr = rec.reconciliation_id
    for l in rec.lines:
        if l.good_cases:
            props.append(ActionProposal(
                action_type="logistics.post_inventory_receipt", action_class=ActionClass.COMMIT,
                description=f"Receive {l.good_cases} good case(s) of {l.sku} into {SALEABLE_LOCATION}",
                company_id=rec.company_id, requested_by=requested_by, capability="inventory",
                path=DataPath.API, entity_type="inventory_item", entity_id=l.sku,
                amount=Decimal(str(round(l.good_cases * l.unit_cost, 2))),
                proposed_changes={"sku": l.sku, "lot": l.lot, "quantity_cases": l.good_cases,
                                  "location": SALEABLE_LOCATION, "po_numbers": rec.po_numbers,
                                  "shipment_id": rec.shipment_id},
                correlation_id=corr,
            ))
        if l.damaged_cases:
            props.append(ActionProposal(
                action_type="logistics.post_inventory_quarantine", action_class=ActionClass.COMMIT,
                description=f"Receive {l.damaged_cases} damaged case(s) of {l.sku} into "
                            f"{QUARANTINE_LOCATION} (not saleable)",
                company_id=rec.company_id, requested_by=requested_by, capability="inventory",
                path=DataPath.API, entity_type="inventory_item", entity_id=l.sku,
                amount=Decimal(str(round(l.damaged_cases * l.unit_cost, 2))),
                proposed_changes={"sku": l.sku, "lot": l.lot, "quantity_cases": l.damaged_cases,
                                  "location": QUARANTINE_LOCATION, "saleable": False,
                                  "po_numbers": rec.po_numbers, "shipment_id": rec.shipment_id},
                correlation_id=corr,
            ))
    for c in rec.claims:
        props.append(ActionProposal(
            action_type="logistics.file_claim", action_class=ActionClass.DRAFT,
            description=f"Review {c.claim_type} draft vs {c.counterparty} for ${c.claimed_amount_estimate_usd:,.2f}",
            company_id=rec.company_id, requested_by=requested_by, capability="claims",
            path=DataPath.API, entity_type="claim", entity_id=c.claim_id,
            amount=Decimal(str(c.claimed_amount_estimate_usd)),
            proposed_changes={"claim": c.to_dict(), "send": False},
            correlation_id=corr,
        ))
    return props


async def submit_for_approval(proposals: list[ActionProposal], policy_engine: Any,
                              approval_queue: Any) -> list[dict]:
    """
    Evaluate each proposal with the tenant's PolicyEngine and park it in the
    ApprovalQueue.  Never executes anything: even an "allow" decision is queued
    so the existing approval/execution flow performs the stock change.
    """
    results = []
    for p in proposals:
        decision = await policy_engine.evaluate(p)
        if decision.effect == "deny":
            results.append({"action_type": p.action_type, "entity_id": p.entity_id,
                            "status": "blocked_by_policy", "rule": decision.rule_name})
            continue
        draft_id = await approval_queue.enqueue(
            company_id=p.company_id, action_type=p.action_type,
            content={**_proposal_dict(p), "policy": {
                "effect": decision.effect, "rule": decision.rule_name,
                "approval_roles": decision.approval_roles}},
            created_by="ai",
        )
        results.append({"action_type": p.action_type, "entity_id": p.entity_id,
                        "status": "pending_approval", "draft_id": draft_id,
                        "policy_effect": decision.effect})
    return results


# ─── helpers ──────────────────────────────────────────────────────────────────


def _finance_recommendations(rec: ReceiptReconciliation, exposure: float) -> list[str]:
    out = []
    if exposure <= 0:
        return out
    if rec.liability.party == "carrier":
        out.append(f"Expected recovery source: carrier cargo claim for ${exposure:,.2f} (draft prepared). "
                   "The supplier invoice is not disputed on this evidence — pay it on normal terms.")
    elif rec.liability.party == "supplier":
        out.append(f"Before paying the supplier invoice in full, have finance review the ${exposure:,.2f} "
                   "discrepancy (hold or short-pay only through the normal AP approval).")
        out.append("Expected recovery source: supplier credit/replacement (draft claim prepared).")
    else:
        out.append(f"Recovery source undetermined — complete the evidence list first. Finance may wish to "
                   f"review the ${exposure:,.2f} before releasing the supplier payment.")
    out.append("Do not write off or revalue quarantined stock until inspection/disposition is approved.")
    return out


def _proposal_dict(p: ActionProposal) -> dict:
    return {
        "action_type": p.action_type, "action_class": p.action_class.value,
        "description": p.description, "capability": p.capability,
        "entity_type": p.entity_type, "entity_id": p.entity_id,
        "amount": str(p.amount) if p.amount is not None else None,
        "proposed_changes": p.proposed_changes, "correlation_id": p.correlation_id,
        "requested_by": p.requested_by, "executed": False,
    }


def _audit(action: str, actor: str, data: dict) -> dict:
    return {"at": datetime.now(timezone.utc).isoformat(), "action": action, "actor": actor, "data": data}
