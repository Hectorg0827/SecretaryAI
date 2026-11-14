"""
End-to-end product journey — the 14-stage logistics ledger.

This module extends (it does not replace) the 9-stage procurement pipeline in
``pipeline.py``.  The 9-stage engines compute things (reorder quantities, PO
drafts, freight parsing, demurrage maths).  This ledger records what has
actually *happened* to a shipment, where each fact came from, and whether that
fact is trustworthy enough to drive decisions.

Core rules
──────────
1. Provenance first.  Every fact enters as a ``LogisticsEvent`` carrying a
   ``SourceType``.  Only sources listed as authoritative for a milestone can
   create a *verified* milestone.  An email, an AI interpretation, or an
   unreviewed document extraction becomes ``Evidence`` — visible as "reported,
   not confirmed" — and never moves the shipment's official stage.
2. Humans can promote evidence.  ``confirm_evidence`` records who confirmed it.
   AI / system actors can never be the confirmer.
3. Idempotent.  Every event has an idempotency key; replaying a feed or
   receiving the same webhook twice changes nothing.
4. Atomic.  An event is applied to a copy of the shipment; the copy only
   replaces the live state once the event store has durably accepted the
   event.  A storage failure leaves the ledger exactly as it was.
5. Rebuildable.  State is a pure function of the ordered event log
   (``ShipmentLedger.rebuild``), which is how recovery after a crash works.
6. Tenant-scoped.  A ledger belongs to exactly one company and rejects events
   for any other company.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional, Protocol


# ─── Stages ───────────────────────────────────────────────────────────────────


class JourneyStage(str, Enum):
    """The 14 stages of a product's journey from supplier to customer."""
    DEMAND_REPLENISHMENT = "01_demand_replenishment"
    PO_APPROVAL = "02_po_preparation_approval"
    SUPPLIER_CONFIRMATION = "03_supplier_confirmation"
    PRODUCTION_READINESS = "04_production_shipment_readiness"
    FREIGHT_BOOKING = "05_freight_booking"
    IN_TRANSIT = "06_container_carrier_movement"
    CUSTOMS = "07_customs_documentation_clearance"
    PORT_ARRIVAL = "08_port_arrival_demurrage"
    WAREHOUSE_SCHEDULING = "09_warehouse_scheduling_receiving"
    INVENTORY_RECONCILIATION = "10_inventory_reconciliation"
    CUSTOMER_ALLOCATION = "11_customer_order_allocation"
    DELIVERY = "12_final_distribution_delivery"
    PROOF_OF_DELIVERY = "13_proof_of_delivery"
    CLAIMS_RECONCILIATION = "14_claims_returns_financial_reconciliation"

    @property
    def order(self) -> int:
        return int(self.value[:2])

    @property
    def label(self) -> str:
        return STAGE_LABELS[self]


STAGE_LABELS: dict[JourneyStage, str] = {
    JourneyStage.DEMAND_REPLENISHMENT: "Demand & replenishment",
    JourneyStage.PO_APPROVAL: "Purchase-order preparation & approval",
    JourneyStage.SUPPLIER_CONFIRMATION: "Supplier confirmation",
    JourneyStage.PRODUCTION_READINESS: "Production & shipment readiness",
    JourneyStage.FREIGHT_BOOKING: "Freight booking",
    JourneyStage.IN_TRANSIT: "Container & carrier movement",
    JourneyStage.CUSTOMS: "Customs documentation & clearance",
    JourneyStage.PORT_ARRIVAL: "Port arrival & demurrage exposure",
    JourneyStage.WAREHOUSE_SCHEDULING: "Warehouse scheduling & receiving",
    JourneyStage.INVENTORY_RECONCILIATION: "Inventory reconciliation",
    JourneyStage.CUSTOMER_ALLOCATION: "Customer order allocation",
    JourneyStage.DELIVERY: "Final distribution & delivery",
    JourneyStage.PROOF_OF_DELIVERY: "Proof of delivery",
    JourneyStage.CLAIMS_RECONCILIATION: "Claims, returns & financial reconciliation",
}

# Default responsible party per stage (overridable per shipment via `parties`).
STAGE_RESPONSIBLE_ROLE: dict[JourneyStage, str] = {
    JourneyStage.DEMAND_REPLENISHMENT: "purchasing",
    JourneyStage.PO_APPROVAL: "purchasing",
    JourneyStage.SUPPLIER_CONFIRMATION: "supplier",
    JourneyStage.PRODUCTION_READINESS: "supplier",
    JourneyStage.FREIGHT_BOOKING: "forwarder",
    JourneyStage.IN_TRANSIT: "carrier",
    JourneyStage.CUSTOMS: "broker",
    JourneyStage.PORT_ARRIVAL: "drayage",
    JourneyStage.WAREHOUSE_SCHEDULING: "warehouse",
    JourneyStage.INVENTORY_RECONCILIATION: "warehouse",
    JourneyStage.CUSTOMER_ALLOCATION: "sales_ops",
    JourneyStage.DELIVERY: "delivery_carrier",
    JourneyStage.PROOF_OF_DELIVERY: "delivery_carrier",
    JourneyStage.CLAIMS_RECONCILIATION: "finance",
}

# Stages that run alongside the physical journey rather than describing where goods are.
PARALLEL_STAGES = {JourneyStage.CUSTOMER_ALLOCATION}

# Stages that only apply to cross-border (overseas) shipments.
IMPORT_ONLY_STAGES = {JourneyStage.CUSTOMS, JourneyStage.PORT_ARRIVAL}


# ─── Provenance ───────────────────────────────────────────────────────────────


class SourceType(str, Enum):
    """Where a fact came from.  Drives whether it can be authoritative."""
    # Machine feeds — may only be set by integrations / connectors, never by
    # a person typing into the API (enforced at the API layer).
    CARRIER_API = "carrier_api"          # ocean line / parcel carrier tracking API
    TERMINAL_API = "terminal_api"        # port terminal availability / LFD feed
    CUSTOMS_SYSTEM = "customs_system"    # e.g. CBP ACE cargo release
    BROKER_EDI = "broker_edi"            # customs broker EDI / portal feed
    FORWARDER_API = "forwarder_api"      # freight forwarder booking feed
    SUPPLIER_EDI = "supplier_edi"        # supplier EDI 855 / 856 / portal
    WMS = "wms"                          # warehouse management system
    ERP = "erp"                          # QuickBooks / accounting / ERP
    SYSTEM_CALCULATED = "system_calculated"  # deterministic engine output
    # Human-verified
    USER_CONFIRMED = "user_confirmed"        # named person verified the fact
    DOCUMENT_VALIDATED = "document_validated"  # validated document + named reviewer
    # Not authoritative on their own
    EMAIL_PARSED = "email_parsed"
    AI_INFERRED = "ai_inferred"
    DOCUMENT_EXTRACTED = "document_extracted"  # extracted, not yet reviewed
    MANUAL_NOTE = "manual_note"


MACHINE_SOURCES = {
    SourceType.CARRIER_API, SourceType.TERMINAL_API, SourceType.CUSTOMS_SYSTEM,
    SourceType.BROKER_EDI, SourceType.FORWARDER_API, SourceType.SUPPLIER_EDI,
    SourceType.WMS, SourceType.ERP, SourceType.SYSTEM_CALCULATED,
}
HUMAN_VERIFIED_SOURCES = {SourceType.USER_CONFIRMED, SourceType.DOCUMENT_VALIDATED}
UNVERIFIED_SOURCES = {
    SourceType.EMAIL_PARSED, SourceType.AI_INFERRED,
    SourceType.DOCUMENT_EXTRACTED, SourceType.MANUAL_NOTE,
}

# Actor ids that may never "confirm" anything.
NON_HUMAN_ACTORS = {"", "ai", "system", "lola", "assistant", "bot", "automation"}


# ─── Milestone catalogue ──────────────────────────────────────────────────────

_H = HUMAN_VERIFIED_SOURCES


@dataclass(frozen=True)
class MilestoneSpec:
    code: str
    stage: JourneyStage
    label: str
    authoritative: frozenset[SourceType]
    completes_stage: bool = False


def _spec(code, stage, label, sources, completes=False) -> MilestoneSpec:
    return MilestoneSpec(code, stage, label, frozenset(set(sources) | _H), completes)


S = SourceType
J = JourneyStage
MILESTONES: dict[str, MilestoneSpec] = {m.code: m for m in [
    _spec("replenishment_recommended", J.DEMAND_REPLENISHMENT, "Replenishment recommended",
          {S.SYSTEM_CALCULATED}, True),
    _spec("po_drafted", J.PO_APPROVAL, "PO drafted", {S.SYSTEM_CALCULATED, S.ERP}),
    _spec("po_approved", J.PO_APPROVAL, "PO approved", {S.ERP}, True),
    _spec("po_sent", J.PO_APPROVAL, "PO sent to supplier", {S.ERP, S.SUPPLIER_EDI}),
    _spec("supplier_confirmed", J.SUPPLIER_CONFIRMATION, "Supplier confirmed PO",
          {S.SUPPLIER_EDI}, True),
    _spec("production_complete", J.PRODUCTION_READINESS, "Production complete", {S.SUPPLIER_EDI}),
    _spec("cargo_ready", J.PRODUCTION_READINESS, "Cargo ready for pickup",
          {S.SUPPLIER_EDI, S.FORWARDER_API}, True),
    _spec("freight_booked", J.FREIGHT_BOOKING, "Freight booked",
          {S.FORWARDER_API, S.CARRIER_API}, True),
    _spec("gate_in_origin", J.IN_TRANSIT, "Container gated in at origin", {S.CARRIER_API, S.TERMINAL_API}),
    _spec("vessel_departed", J.IN_TRANSIT, "Departed origin", {S.CARRIER_API, S.FORWARDER_API}),
    _spec("transshipment", J.IN_TRANSIT, "Transshipment", {S.CARRIER_API}),
    _spec("vessel_arrived", J.IN_TRANSIT, "Arrived destination port",
          {S.CARRIER_API, S.TERMINAL_API}, True),
    _spec("isf_filed", J.CUSTOMS, "ISF filed", {S.BROKER_EDI, S.CUSTOMS_SYSTEM}),
    _spec("entry_filed", J.CUSTOMS, "Customs entry filed", {S.BROKER_EDI, S.CUSTOMS_SYSTEM}),
    _spec("customs_hold", J.CUSTOMS, "Customs hold placed", {S.BROKER_EDI, S.CUSTOMS_SYSTEM}),
    _spec("customs_exam", J.CUSTOMS, "Customs exam ordered", {S.BROKER_EDI, S.CUSTOMS_SYSTEM}),
    _spec("duties_paid", J.CUSTOMS, "Duties paid", {S.BROKER_EDI, S.CUSTOMS_SYSTEM, S.ERP}),
    _spec("customs_released", J.CUSTOMS, "Released by customs",
          {S.BROKER_EDI, S.CUSTOMS_SYSTEM}, True),
    _spec("discharged", J.PORT_ARRIVAL, "Discharged from vessel", {S.TERMINAL_API, S.CARRIER_API}),
    _spec("available_for_pickup", J.PORT_ARRIVAL, "Available for pickup", {S.TERMINAL_API}),
    _spec("gate_out_port", J.PORT_ARRIVAL, "Picked up from port",
          {S.TERMINAL_API, S.CARRIER_API}, True),
    _spec("empty_returned", J.PORT_ARRIVAL, "Empty container returned", {S.TERMINAL_API, S.CARRIER_API}),
    _spec("warehouse_appointment_set", J.WAREHOUSE_SCHEDULING, "Warehouse appointment set", {S.WMS}),
    _spec("arrived_at_warehouse", J.WAREHOUSE_SCHEDULING, "Arrived at warehouse",
          {S.WMS, S.CARRIER_API}, True),
    _spec("warehouse_received", J.INVENTORY_RECONCILIATION, "Received into warehouse", {S.WMS}),
    _spec("inventory_reconciled", J.INVENTORY_RECONCILIATION, "Receipt reconciled",
          {S.WMS, S.ERP}, True),
    _spec("customer_allocated", J.CUSTOMER_ALLOCATION, "Allocated to customer orders",
          {S.ERP, S.WMS}, True),
    _spec("out_for_delivery", J.DELIVERY, "Out for delivery", {S.CARRIER_API, S.WMS}),
    _spec("delivered", J.DELIVERY, "Delivered", {S.CARRIER_API}, True),
    _spec("pod_received", J.PROOF_OF_DELIVERY, "Proof of delivery on file", {S.CARRIER_API}, True),
    _spec("claim_filed", J.CLAIMS_RECONCILIATION, "Claim filed", {S.ERP}),
    _spec("return_received", J.CLAIMS_RECONCILIATION, "Return received", {S.WMS, S.ERP}),
    _spec("financially_reconciled", J.CLAIMS_RECONCILIATION, "Financially reconciled", {S.ERP}, True),
]}

# Milestones whose verified presence is required before another may be
# accepted without raising a sequence exception.
PREREQUISITES: dict[str, tuple[str, ...]] = {
    "gate_out_port": ("customs_released",),
    "warehouse_received": ("customs_released",),
    "pod_received": ("delivered",),
}

# Document types each stage expects (used for the stage checklist).
STAGE_DOCUMENTS: dict[JourneyStage, tuple[str, ...]] = {
    J.PO_APPROVAL: ("purchase_order",),
    J.SUPPLIER_CONFIRMATION: ("order_confirmation",),
    J.FREIGHT_BOOKING: ("booking_confirmation",),
    J.IN_TRANSIT: ("bill_of_lading",),
    J.CUSTOMS: ("commercial_invoice", "packing_list", "customs_entry", "customs_release"),
    J.PORT_ARRIVAL: ("arrival_notice",),
    J.WAREHOUSE_SCHEDULING: ("delivery_appointment",),
    J.INVENTORY_RECONCILIATION: ("warehouse_receipt",),
    J.PROOF_OF_DELIVERY: ("proof_of_delivery",),
    J.CLAIMS_RECONCILIATION: ("freight_invoice",),
}

# Documents customs needs before (or at) arrival for an ocean import.
REQUIRED_CUSTOMS_DOCUMENTS = ("bill_of_lading", "commercial_invoice", "packing_list")


# ─── Errors ───────────────────────────────────────────────────────────────────


class LedgerError(ValueError):
    """Invalid event (unknown kind / milestone, malformed payload, …)."""


class TenantMismatchError(LedgerError):
    """Event or ledger belongs to a different company."""


class RecordNotFoundError(LedgerError):
    """Shipment, evidence, allocation or exception does not exist."""


class ConfirmerRequiredError(LedgerError):
    """A human-verified source or confirmation lacks a named person."""


class StoreUnavailableError(RuntimeError):
    """The durable event store did not accept the event."""


# ─── Events ───────────────────────────────────────────────────────────────────


EVENT_KINDS = {
    "shipment_created", "shipment_updated", "milestone", "eta", "plan",
    "document", "cost", "allocation", "allocation_update", "appointment",
    "port_terms", "exception", "exception_resolved", "evidence_confirmed",
    "receipt_reconciled",
}


@dataclass
class LogisticsEvent:
    """One immutable fact about a shipment, with where it came from."""
    company_id: str
    shipment_id: str
    kind: str
    source: SourceType
    code: str = ""                       # milestone code where relevant
    occurred_at: str = ""                # when it happened in the real world
    source_ref: str = ""                 # e.g. email message id, EDI control #, doc id
    payload: dict = field(default_factory=dict)
    confirmed_by: str = ""               # human id for USER_CONFIRMED / DOCUMENT_VALIDATED
    recorded_at: str = field(default_factory=lambda: _now_iso())
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = ""

    def __post_init__(self):
        if isinstance(self.source, str) and not isinstance(self.source, SourceType):
            self.source = SourceType(self.source)
        if not self.idempotency_key:
            self.idempotency_key = self.compute_key()

    def compute_key(self) -> str:
        basis = json.dumps({
            "c": self.company_id, "s": self.shipment_id, "k": self.kind,
            "code": self.code, "at": self.occurred_at, "src": self.source.value,
            "ref": self.source_ref, "p": self.payload, "by": self.confirmed_by,
        }, sort_keys=True, default=str)
        return hashlib.sha256(basis.encode()).hexdigest()[:32]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticsEvent":
        fields_ = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**fields_)


# ─── State records ────────────────────────────────────────────────────────────


@dataclass
class Milestone:
    code: str
    stage: JourneyStage
    label: str
    occurred_at: str
    source: SourceType
    source_ref: str = ""
    confirmed_by: str = ""
    event_id: str = ""
    responsible_party: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code, "stage": self.stage.value, "label": self.label,
            "occurred_at": self.occurred_at, "source": self.source.value,
            "source_ref": self.source_ref, "confirmed_by": self.confirmed_by,
            "responsible_party": self.responsible_party, "verified": True,
            "notes": self.notes,
        }


@dataclass
class Evidence:
    """A reported-but-unverified fact (email, AI reading, unreviewed doc)."""
    evidence_id: str
    kind: str                            # "milestone" | "eta" | "allocation_update" …
    code: str
    occurred_at: str
    source: SourceType
    source_ref: str = ""
    payload: dict = field(default_factory=dict)
    status: str = "unverified"           # unverified | confirmed | rejected
    reviewed_by: str = ""
    recorded_at: str = ""

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id, "kind": self.kind, "code": self.code,
            "occurred_at": self.occurred_at, "source": self.source.value,
            "source_ref": self.source_ref, "payload": self.payload,
            "status": self.status, "reviewed_by": self.reviewed_by, "verified": False,
        }


@dataclass
class EtaRecord:
    eta: str
    source: SourceType
    recorded_at: str
    source_ref: str = ""
    verified: bool = False
    location: str = ""                   # e.g. "destination_port", "warehouse"

    def to_dict(self) -> dict:
        return {
            "eta": self.eta, "source": self.source.value, "recorded_at": self.recorded_at,
            "source_ref": self.source_ref, "verified": self.verified, "location": self.location,
        }


@dataclass
class DocumentRef:
    doc_id: str
    doc_type: str
    status: str                          # extracted | validated | validation_failed | reviewed
    source_ref: str = ""
    reviewed_by: str = ""
    fields: dict = field(default_factory=dict)
    received_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CostEntry:
    component: str                       # ocean_freight | customs_duty | drayage | …
    amount_usd: float
    basis: str = "invoiced"              # quoted | invoiced | accrued
    source: SourceType = SourceType.MANUAL_NOTE
    source_ref: str = ""
    verified: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        return d


@dataclass
class ShipmentLine:
    sku: str
    qty_cases: int
    description: str = ""
    lot: str = ""
    unit_cost: float = 0.0               # commercial — PO cost per case

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CustomerAllocation:
    order_id: str
    customer_id: str
    sku: str
    qty_cases: int
    customer_name: str = ""
    sales_rep_id: str = ""
    promised_date: str = ""
    status: str = "allocated"            # allocated | out_for_delivery | delivered | cancelled
    delivered_at: str = ""
    delivery_source: str = ""
    pod_doc_id: str = ""
    tracking_number: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShipmentException:
    exception_id: str
    code: str
    severity: str                        # info | warning | alert | critical
    message: str
    stage: Optional[JourneyStage] = None
    detected_at: str = ""
    resolved: bool = False
    resolved_by: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "exception_id": self.exception_id, "code": self.code, "severity": self.severity,
            "message": self.message, "stage": self.stage.value if self.stage else None,
            "detected_at": self.detected_at, "resolved": self.resolved,
            "resolved_by": self.resolved_by, "data": self.data,
        }


@dataclass
class Shipment:
    """Aggregate state for one physical shipment (one BOL / booking)."""
    shipment_id: str
    company_id: str
    mode: str = "ocean"                  # ocean | ground | air | parcel
    supply_chain_type: str = "overseas"  # overseas | domestic
    po_numbers: list[str] = field(default_factory=list)
    supplier_id: str = ""
    supplier_name: str = ""
    carrier: str = ""
    forwarder: str = ""
    broker: str = ""
    warehouse: str = ""
    container_numbers: list[str] = field(default_factory=list)
    bol_number: str = ""
    booking_ref: str = ""
    origin: str = ""
    destination: str = ""
    lines: list[ShipmentLine] = field(default_factory=list)
    allocations: list[CustomerAllocation] = field(default_factory=list)
    commercial: dict = field(default_factory=dict)   # payment terms, incoterm, freight quote…
    parties: dict = field(default_factory=dict)      # role → name override

    po_sent_at: str = ""
    requested_ship_date: str = ""
    expected_dates: dict = field(default_factory=dict)   # milestone code → ISO date
    milestones: dict = field(default_factory=dict)       # code → Milestone
    evidence: list = field(default_factory=list)         # list[Evidence]
    eta_history: list = field(default_factory=list)      # list[EtaRecord]
    documents: list = field(default_factory=list)        # list[DocumentRef]
    costs: list = field(default_factory=list)            # list[CostEntry]
    exceptions: list = field(default_factory=list)       # list[ShipmentException]
    port_terms: dict = field(default_factory=dict)       # last_free_day, demurrage_rate_per_day, …
    appointment: dict = field(default_factory=dict)      # start, end, dock, warehouse
    receipt: dict = field(default_factory=dict)          # last reconciliation summary
    cancelled: bool = False
    created_at: str = ""
    updated_at: str = ""
    audit: list = field(default_factory=list)            # [{event_id, kind, code, source, at, by}]

    # ── Derived ──────────────────────────────────────────────────────────────

    @property
    def is_import(self) -> bool:
        return self.supply_chain_type == "overseas" or self.mode == "ocean"

    @property
    def total_cases(self) -> int:
        return sum(l.qty_cases for l in self.lines)

    def has(self, code: str) -> bool:
        return code in self.milestones

    def milestone_date(self, code: str) -> Optional[str]:
        m = self.milestones.get(code)
        return m.occurred_at if m else None

    @property
    def current_stage(self) -> JourneyStage:
        """
        Furthest *physical / process* stage with a verified milestone.
        Unverified evidence never counts.  Customer allocation is a parallel
        commercial track (inbound stock is often allocated while still on the
        water), so it only counts when nothing else has happened yet.
        """
        stages = [m.stage for m in self.milestones.values() if m.stage not in PARALLEL_STAGES]
        if not stages:
            stages = [m.stage for m in self.milestones.values()]
        if not stages:
            return JourneyStage.DEMAND_REPLENISHMENT
        return max(stages, key=lambda s: s.order)

    def responsible_party(self, stage: JourneyStage) -> str:
        role = STAGE_RESPONSIBLE_ROLE[stage]
        named = self.parties.get(role) or {
            "supplier": self.supplier_name, "carrier": self.carrier,
            "forwarder": self.forwarder, "broker": self.broker,
            "warehouse": self.warehouse,
        }.get(role, "")
        return f"{role}: {named}" if named else role

    # ── ETA provenance ───────────────────────────────────────────────────────

    def baseline_eta(self) -> Optional[EtaRecord]:
        verified = [e for e in self.eta_history if e.verified]
        return verified[0] if verified else None

    def current_eta(self) -> Optional[EtaRecord]:
        """Latest *verified* ETA.  Unverified ETAs never override it."""
        verified = [e for e in self.eta_history if e.verified]
        return verified[-1] if verified else None

    def reported_eta(self) -> Optional[EtaRecord]:
        """Latest unverified ETA if it is newer than the verified one."""
        unverified = [e for e in self.eta_history if not e.verified]
        if not unverified:
            return None
        cur = self.current_eta()
        latest = unverified[-1]
        if cur is None or latest.recorded_at > cur.recorded_at:
            return latest
        return None

    def eta_delay_days(self) -> int:
        base, cur = self.baseline_eta(), self.current_eta()
        if not base or not cur:
            return 0
        return _days_between(base.eta, cur.eta) or 0

    # ── Documents ────────────────────────────────────────────────────────────

    def document_types(self, include_failed: bool = False) -> set[str]:
        return {
            d.doc_type for d in self.documents
            if include_failed or d.status != "validation_failed"
        }

    def missing_documents(self, required: Iterable[str]) -> list[str]:
        have = self.document_types()
        return [d for d in required if d not in have]

    def open_exceptions(self) -> list[ShipmentException]:
        return [e for e in self.exceptions if not e.resolved]

    def pending_evidence(self) -> list[Evidence]:
        return [e for e in self.evidence if e.status == "unverified"]

    # ── Stage view ───────────────────────────────────────────────────────────

    def stage_view(self) -> list[dict]:
        """Status, dates, party, documents and exceptions for all 14 stages."""
        out = []
        for stage in JourneyStage:
            specs = [s for s in MILESTONES.values() if s.stage == stage]
            done = [self.milestones[s.code] for s in specs if s.code in self.milestones]
            completion = next((s for s in specs if s.completes_stage), None)
            reported = [
                e for e in self.pending_evidence()
                if e.kind == "milestone" and e.code in {s.code for s in specs}
            ]
            stage_exc = [e for e in self.open_exceptions() if e.stage == stage]
            docs_expected = STAGE_DOCUMENTS.get(stage, ())
            have = self.document_types()

            if stage in IMPORT_ONLY_STAGES and not self.is_import:
                status = "not_applicable"
            elif stage_exc:
                status = "exception"
            elif completion and completion.code in self.milestones:
                status = "complete"
            elif done:
                status = "in_progress"
            elif reported:
                status = "reported_unverified"
            else:
                status = "not_started"

            out.append({
                "stage": stage.value,
                "order": stage.order,
                "label": stage.label,
                "status": status,
                "responsible_party": self.responsible_party(stage),
                "expected_date": self.expected_dates.get(completion.code) if completion else None,
                "actual_date": (
                    self.milestones[completion.code].occurred_at
                    if completion and completion.code in self.milestones else None
                ),
                "verified_milestones": [m.to_dict() for m in done],
                "unverified_reports": [e.to_dict() for e in reported],
                "documents": {d: (d in have) for d in docs_expected},
                "exceptions": [e.to_dict() for e in stage_exc],
            })
        return out

    def to_dict(self) -> dict:
        cur, base, rep = self.current_eta(), self.baseline_eta(), self.reported_eta()
        return {
            "shipment_id": self.shipment_id,
            "company_id": self.company_id,
            "mode": self.mode,
            "supply_chain_type": self.supply_chain_type,
            "po_numbers": list(self.po_numbers),
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "carrier": self.carrier,
            "forwarder": self.forwarder,
            "broker": self.broker,
            "warehouse": self.warehouse,
            "container_numbers": list(self.container_numbers),
            "bol_number": self.bol_number,
            "booking_ref": self.booking_ref,
            "origin": self.origin,
            "destination": self.destination,
            "current_stage": self.current_stage.value,
            "current_stage_label": self.current_stage.label,
            "cancelled": self.cancelled,
            "eta": {
                "current": cur.to_dict() if cur else None,
                "baseline": base.to_dict() if base else None,
                "reported_unverified": rep.to_dict() if rep else None,
                "delay_days": self.eta_delay_days(),
            },
            "lines": [l.to_dict() for l in self.lines],
            "total_cases": self.total_cases,
            "allocations": [a.to_dict() for a in self.allocations],
            "commercial": dict(self.commercial),
            "costs": [c.to_dict() for c in self.costs],
            "milestones": {k: m.to_dict() for k, m in self.milestones.items()},
            "pending_evidence": [e.to_dict() for e in self.pending_evidence()],
            "documents": [d.to_dict() for d in self.documents],
            "exceptions": [e.to_dict() for e in self.open_exceptions()],
            "port_terms": dict(self.port_terms),
            "appointment": dict(self.appointment),
            "receipt": dict(self.receipt),
            "stages": self.stage_view(),
            "updated_at": self.updated_at,
        }


# ─── Event store protocol ─────────────────────────────────────────────────────


class EventStore(Protocol):
    def append(self, event: LogisticsEvent) -> None: ...
    def load(self, company_id: str) -> list[LogisticsEvent]: ...


@dataclass
class ApplyResult:
    applied: bool
    duplicate: bool = False
    verified: bool = False               # milestone/eta became authoritative
    evidence_id: str = ""                # set when recorded as unverified evidence
    exceptions: list = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "applied": self.applied, "duplicate": self.duplicate, "verified": self.verified,
            "evidence_id": self.evidence_id,
            "exceptions": [e.to_dict() for e in self.exceptions], "message": self.message,
        }


# ─── Ledger ───────────────────────────────────────────────────────────────────


class ShipmentLedger:
    """
    Event-sourced, tenant-scoped ledger of shipments.

        ledger = ShipmentLedger("company-1", store=SupabaseEventStore(db))
        ledger.ingest(LogisticsEvent(...))
    """

    def __init__(self, company_id: str, store: Optional[EventStore] = None):
        if not company_id:
            raise TenantMismatchError("company_id is required")
        self.company_id = company_id
        self._store = store
        self._shipments: dict[str, Shipment] = {}
        self._seen_keys: set[str] = set()
        self._events: list[LogisticsEvent] = []

    # ── Construction / recovery ──────────────────────────────────────────────

    @classmethod
    def rebuild(cls, company_id: str, events: Iterable[LogisticsEvent],
                store: Optional[EventStore] = None) -> "ShipmentLedger":
        """Replay an event log into a fresh ledger (crash recovery / cold start)."""
        ledger = cls(company_id, store=None)
        # Stable sort: ties keep the store's insertion order (never a random id).
        for ev in sorted(events, key=lambda e: e.recorded_at):
            if ev.company_id != company_id:
                continue  # never let another tenant's rows leak in
            try:
                ledger.ingest(ev)
            except LedgerError:
                continue  # a bad historic row must not block recovery
        ledger._store = store
        return ledger

    @classmethod
    def load(cls, company_id: str, store: EventStore) -> "ShipmentLedger":
        return cls.rebuild(company_id, store.load(company_id), store=store)

    # ── Queries ──────────────────────────────────────────────────────────────

    def get(self, shipment_id: str) -> Optional[Shipment]:
        return self._shipments.get(shipment_id)

    def shipments(self, include_cancelled: bool = False) -> list[Shipment]:
        return [s for s in self._shipments.values() if include_cancelled or not s.cancelled]

    def events(self) -> list[LogisticsEvent]:
        return list(self._events)

    def find(self, identifier: str) -> list[Shipment]:
        """Find shipments by container, BOL, booking, PO, shipment id or order id."""
        if not identifier:
            return []
        needle = _norm(identifier)
        hits = []
        for s in self._shipments.values():
            keys = {_norm(s.shipment_id), _norm(s.bol_number), _norm(s.booking_ref)}
            keys |= {_norm(c) for c in s.container_numbers}
            keys |= {_norm(p) for p in s.po_numbers}
            keys |= {_norm(a.order_id) for a in s.allocations}
            keys.discard("")
            if needle in keys:
                hits.append(s)
        return hits

    def find_evidence(self, evidence_id: str) -> tuple[Optional[Shipment], Optional[Evidence]]:
        for s in self._shipments.values():
            for e in s.evidence:
                if e.evidence_id == evidence_id:
                    return s, e
        return None, None

    # ── Commands ─────────────────────────────────────────────────────────────

    def ingest(self, event: LogisticsEvent) -> ApplyResult:
        """Validate, apply atomically, persist, then commit."""
        if event.company_id != self.company_id:
            raise TenantMismatchError("event belongs to a different company")
        if event.kind not in EVENT_KINDS:
            raise LedgerError(f"unknown event kind: {event.kind!r}")
        if event.idempotency_key in self._seen_keys:
            return ApplyResult(applied=False, duplicate=True, message="duplicate event ignored")

        if event.source in HUMAN_VERIFIED_SOURCES and _is_non_human(event.confirmed_by):
            raise ConfirmerRequiredError(
                f"source {event.source.value} requires a named human confirmer (confirmed_by)"
            )

        existing = self._shipments.get(event.shipment_id)
        if existing is None and event.kind != "shipment_created":
            raise RecordNotFoundError(f"unknown shipment {event.shipment_id!r}")
        if existing is not None and event.kind == "shipment_created":
            # Re-creation with different content is treated as an update.
            event_kind = "shipment_updated"
        else:
            event_kind = event.kind

        working = copy.deepcopy(existing) if existing else Shipment(
            shipment_id=event.shipment_id, company_id=self.company_id,
            created_at=event.occurred_at or event.recorded_at,
        )
        result = _APPLY[event_kind](working, event)
        working.updated_at = event.recorded_at
        working.audit.append({
            "event_id": event.event_id, "kind": event.kind, "code": event.code,
            "source": event.source.value, "source_ref": event.source_ref,
            "at": event.recorded_at, "by": event.confirmed_by or event.source.value,
            "verified": result.verified,
        })

        # Persist BEFORE committing so a store failure leaves state untouched.
        if self._store is not None:
            try:
                self._store.append(event)
            except Exception as exc:  # noqa: BLE001 — any store error is fatal for this event
                raise StoreUnavailableError(f"event store rejected event: {exc}") from exc

        self._shipments[event.shipment_id] = working
        self._seen_keys.add(event.idempotency_key)
        self._events.append(event)
        result.applied = True
        return result

    def confirm_evidence(self, evidence_id: str, confirmed_by: str,
                         occurred_at: str = "", note: str = "") -> ApplyResult:
        """A named person promotes unverified evidence to a verified fact."""
        if _is_non_human(confirmed_by):
            raise ConfirmerRequiredError("evidence can only be confirmed by a named person")
        shipment, ev = self.find_evidence(evidence_id)
        if not shipment or not ev:
            raise RecordNotFoundError(f"evidence {evidence_id!r} not found")
        if ev.status != "unverified":
            return ApplyResult(applied=False, duplicate=True, message=f"evidence already {ev.status}")
        return self.ingest(LogisticsEvent(
            company_id=self.company_id, shipment_id=shipment.shipment_id,
            kind="evidence_confirmed", code=ev.code, source=SourceType.USER_CONFIRMED,
            occurred_at=occurred_at or ev.occurred_at, source_ref=evidence_id,
            confirmed_by=confirmed_by, payload={"note": note},
        ))


# ─── Event appliers (pure functions on a working copy) ────────────────────────


def _apply_created(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    _merge_static(s, ev.payload)
    return ApplyResult(applied=True, verified=True)


def _apply_updated(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    exc = []
    p = ev.payload
    # Conflicting identifiers from an unverified source are flagged, not applied.
    if ev.source in UNVERIFIED_SOURCES:
        for key in ("bol_number", "booking_ref"):
            if p.get(key) and getattr(s, key) and _norm(p[key]) != _norm(getattr(s, key)):
                exc.append(_add_exception(
                    s, "IDENTIFIER_CONFLICT", "warning",
                    f"{key} {p[key]!r} from {ev.source.value} conflicts with {getattr(s, key)!r}",
                    None, ev,
                ))
        return ApplyResult(applied=True, exceptions=exc,
                           message="unverified update recorded as conflict only" if exc else "")
    _merge_static(s, p)
    return ApplyResult(applied=True, verified=True)


def _merge_static(s: Shipment, p: dict) -> None:
    simple = (
        "mode", "supply_chain_type", "supplier_id", "supplier_name", "carrier", "forwarder",
        "broker", "warehouse", "bol_number", "booking_ref", "origin", "destination",
        "po_sent_at", "requested_ship_date",
    )
    for key in simple:
        if p.get(key):
            setattr(s, key, p[key])
    for key in ("po_numbers", "container_numbers"):
        for v in p.get(key, []) or []:
            v = v.strip().upper() if key == "container_numbers" else v.strip()
            if v and v not in getattr(s, key):
                getattr(s, key).append(v)
    if p.get("lines"):
        s.lines = [ShipmentLine(**{k: v for k, v in l.items() if k in ShipmentLine.__dataclass_fields__})
                   for l in p["lines"]]
    if p.get("commercial"):
        s.commercial.update(p["commercial"])
    if p.get("parties"):
        s.parties.update(p["parties"])
    if p.get("expected_dates"):
        s.expected_dates.update(p["expected_dates"])
    if p.get("cancelled") is True:
        s.cancelled = True


def _apply_milestone(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    spec = MILESTONES.get(ev.code)
    if spec is None:
        raise LedgerError(f"unknown milestone code {ev.code!r}")
    exc: list[ShipmentException] = []

    if ev.source not in spec.authoritative:
        evid = _add_evidence(s, ev, "milestone")
        return ApplyResult(applied=True, evidence_id=evid.evidence_id,
                           message=f"{spec.label} recorded as UNVERIFIED ({ev.source.value})")

    if s.cancelled:
        exc.append(_add_exception(s, "EVENT_AFTER_CANCEL", "alert",
                                  f"{spec.label} reported on a cancelled shipment", spec.stage, ev))

    for req in PREREQUISITES.get(ev.code, ()):
        if s.is_import and req not in s.milestones:
            exc.append(_add_exception(
                s, "SEQUENCE_ANOMALY", "alert",
                f"{spec.label} recorded before verified '{MILESTONES[req].label}'",
                spec.stage, ev, {"missing_prerequisite": req},
            ))

    _record_milestone(s, spec, ev)

    # Milestone side-effects
    if ev.code == "customs_hold":
        exc.append(_add_exception(
            s, "CUSTOMS_HOLD", "critical",
            f"Customs hold: {ev.payload.get('reason') or 'reason not provided'}",
            JourneyStage.CUSTOMS, ev, {"reason": ev.payload.get("reason", "")},
        ))
    if ev.code == "customs_released":
        for e in s.exceptions:
            if e.code == "CUSTOMS_HOLD" and not e.resolved:
                e.resolved, e.resolved_by = True, ev.confirmed_by or ev.source.value
    if ev.code in ("delivered", "pod_received"):
        order_id = ev.payload.get("order_id")
        for a in s.allocations:
            if order_id and a.order_id != order_id:
                continue
            if ev.code == "delivered":
                a.status, a.delivered_at, a.delivery_source = "delivered", ev.occurred_at, ev.source.value
            if ev.code == "pod_received":
                a.pod_doc_id = ev.payload.get("doc_id", a.pod_doc_id) or ev.source_ref
    return ApplyResult(applied=True, verified=True, exceptions=exc)


def _record_milestone(s: Shipment, spec: MilestoneSpec, ev: LogisticsEvent) -> None:
    prev = s.milestones.get(spec.code)
    # Keep the earliest verified occurrence unless this event is an explicit correction.
    if prev and not ev.payload.get("correction") and (prev.occurred_at or "") <= (ev.occurred_at or ""):
        return
    s.milestones[spec.code] = Milestone(
        code=spec.code, stage=spec.stage, label=spec.label,
        occurred_at=ev.occurred_at or ev.recorded_at, source=ev.source,
        source_ref=ev.source_ref, confirmed_by=ev.confirmed_by, event_id=ev.event_id,
        responsible_party=s.responsible_party(spec.stage), notes=ev.payload.get("notes", ""),
    )


def _apply_eta(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    eta = ev.payload.get("eta", "")
    if not _parse_date(eta):
        raise LedgerError("eta event requires payload.eta as an ISO date")
    authoritative = {S.CARRIER_API, S.TERMINAL_API, S.FORWARDER_API, S.WMS} | HUMAN_VERIFIED_SOURCES
    verified = ev.source in authoritative
    rec = EtaRecord(eta=eta[:10], source=ev.source, recorded_at=ev.recorded_at,
                    source_ref=ev.source_ref, verified=verified,
                    location=ev.payload.get("location", "destination_port"))
    s.eta_history.append(rec)
    s.eta_history.sort(key=lambda r: r.recorded_at)
    if not verified:
        evid = _add_evidence(s, ev, "eta")
        return ApplyResult(applied=True, evidence_id=evid.evidence_id,
                           message="ETA recorded as reported (unverified); official ETA unchanged")
    return ApplyResult(applied=True, verified=True)


def _apply_plan(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    for code, d in (ev.payload.get("expected_dates") or {}).items():
        if code in MILESTONES and _parse_date(d):
            s.expected_dates[code] = d[:10]
    return ApplyResult(applied=True, verified=ev.source not in UNVERIFIED_SOURCES)


def _apply_document(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    p = ev.payload
    doc = DocumentRef(
        doc_id=p.get("doc_id") or ev.source_ref or ev.event_id,
        doc_type=p.get("doc_type", "unknown"),
        status=p.get("status", "extracted"),
        source_ref=ev.source_ref, reviewed_by=ev.confirmed_by,
        fields=p.get("fields", {}), received_at=ev.occurred_at or ev.recorded_at,
    )
    s.documents = [d for d in s.documents if d.doc_id != doc.doc_id] + [doc]
    exc = []
    if doc.status == "validation_failed":
        exc.append(_add_exception(
            s, "DOCUMENT_VALIDATION_FAILED", "warning",
            f"{doc.doc_type} {doc.doc_id} failed validation: "
            + "; ".join(p.get("failed_checks", [])[:3]),
            None, ev,
        ))
    # A document may *suggest* a milestone; that suggestion is evidence only,
    # unless a named person reviewed the validated document.
    suggested = p.get("suggests_milestone")
    if suggested in MILESTONES:
        sub = LogisticsEvent(
            company_id=ev.company_id, shipment_id=ev.shipment_id, kind="milestone",
            code=suggested, occurred_at=p.get("milestone_date") or ev.occurred_at,
            source=(S.DOCUMENT_VALIDATED if (doc.status in ("validated", "reviewed") and ev.confirmed_by)
                    else S.DOCUMENT_EXTRACTED),
            source_ref=doc.doc_id, confirmed_by=ev.confirmed_by,
            payload={"doc_id": doc.doc_id, "order_id": p.get("order_id", "")},
            recorded_at=ev.recorded_at, event_id=ev.event_id,
        )
        r = _apply_milestone(s, sub)
        r.exceptions = exc + r.exceptions
        return r
    return ApplyResult(applied=True, exceptions=exc)


def _apply_cost(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    p = ev.payload
    try:
        amount = float(p["amount_usd"])
    except (KeyError, TypeError, ValueError):
        raise LedgerError("cost event requires numeric payload.amount_usd")
    verified = ev.source in ({S.ERP, S.FORWARDER_API, S.BROKER_EDI} | HUMAN_VERIFIED_SOURCES)
    ref = ev.source_ref or p.get("invoice_number", "")
    # Same invoice line re-sent → replace rather than double-count.
    s.costs = [c for c in s.costs if not (ref and c.source_ref == ref and c.component == p.get("component"))]
    s.costs.append(CostEntry(component=p.get("component", "other"), amount_usd=amount,
                             basis=p.get("basis", "invoiced"), source=ev.source,
                             source_ref=ref, verified=verified))
    return ApplyResult(applied=True, verified=verified)


def _apply_allocation(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    p = ev.payload
    if ev.source in UNVERIFIED_SOURCES:
        evid = _add_evidence(s, ev, "allocation")
        return ApplyResult(applied=True, evidence_id=evid.evidence_id,
                           message="allocation suggestion recorded as unverified")
    alloc = CustomerAllocation(**{k: v for k, v in p.items() if k in CustomerAllocation.__dataclass_fields__})
    s.allocations = [a for a in s.allocations
                     if not (a.order_id == alloc.order_id and a.sku == alloc.sku)] + [alloc]
    if "customer_allocated" not in s.milestones:
        _record_milestone(s, MILESTONES["customer_allocated"], ev)
    return ApplyResult(applied=True, verified=True)


def _apply_allocation_update(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    """Delivery status for one customer order (e.g. parcel tracking)."""
    p = ev.payload
    order_id = p.get("order_id", "")
    targets = [a for a in s.allocations if a.order_id == order_id]
    if not targets:
        raise RecordNotFoundError(f"no allocation for order {order_id!r}")
    authoritative = {S.CARRIER_API, S.WMS, S.ERP} | HUMAN_VERIFIED_SOURCES
    if ev.source not in authoritative:
        evid = _add_evidence(s, ev, "allocation_update")
        return ApplyResult(applied=True, evidence_id=evid.evidence_id,
                           message="delivery update recorded as unverified")
    for a in targets:
        if p.get("status"):
            a.status = p["status"]
        if p.get("tracking_number"):
            a.tracking_number = p["tracking_number"]
        if p.get("status") == "delivered":
            a.delivered_at = ev.occurred_at or ev.recorded_at
            a.delivery_source = ev.source.value
        if p.get("pod_doc_id"):
            a.pod_doc_id = p["pod_doc_id"]
    return ApplyResult(applied=True, verified=True)


def _apply_appointment(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    p = ev.payload
    if ev.source in UNVERIFIED_SOURCES:
        evid = _add_evidence(s, ev, "appointment")
        return ApplyResult(applied=True, evidence_id=evid.evidence_id,
                           message="appointment recorded as unverified")
    s.appointment = {k: p.get(k, "") for k in ("start", "end", "dock", "warehouse", "reference")}
    _record_milestone(s, MILESTONES["warehouse_appointment_set"], ev)
    return ApplyResult(applied=True, verified=True)


def _apply_port_terms(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    if ev.source in UNVERIFIED_SOURCES:
        evid = _add_evidence(s, ev, "port_terms")
        return ApplyResult(applied=True, evidence_id=evid.evidence_id,
                           message="port terms recorded as unverified")
    for k in ("last_free_day", "demurrage_rate_per_day", "detention_free_days",
              "detention_rate_per_day", "terminal"):
        if k in ev.payload:
            s.port_terms[k] = ev.payload[k]
    return ApplyResult(applied=True, verified=True)


def _apply_exception(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    p = ev.payload
    stage = JourneyStage(p["stage"]) if p.get("stage") else None
    e = _add_exception(s, p.get("code", "MANUAL"), p.get("severity", "warning"),
                       p.get("message", ""), stage, ev, p.get("data", {}))
    return ApplyResult(applied=True, exceptions=[e])


def _apply_exception_resolved(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    xid = ev.payload.get("exception_id")
    for e in s.exceptions:
        if e.exception_id == xid:
            e.resolved, e.resolved_by = True, ev.confirmed_by or ev.source.value
            return ApplyResult(applied=True)
    raise RecordNotFoundError(f"exception {xid!r} not found")


def _apply_evidence_confirmed(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    target = next((e for e in s.evidence if e.evidence_id == ev.source_ref), None)
    if target is None:
        raise RecordNotFoundError("evidence not found on shipment")
    if target.status != "unverified":
        return ApplyResult(applied=True, message=f"evidence already {target.status}")
    target.status, target.reviewed_by = "confirmed", ev.confirmed_by
    replay = LogisticsEvent(
        company_id=ev.company_id, shipment_id=ev.shipment_id, kind=target.kind,
        code=target.code, occurred_at=ev.occurred_at or target.occurred_at,
        source=S.USER_CONFIRMED, source_ref=target.source_ref or target.evidence_id,
        payload=dict(target.payload), confirmed_by=ev.confirmed_by,
        recorded_at=ev.recorded_at, event_id=ev.event_id,
    )
    if target.kind == "milestone":
        return _apply_milestone(s, replay)
    if target.kind == "eta":
        return _apply_eta(s, replay)
    if target.kind in ("allocation", "allocation_update", "appointment", "port_terms"):
        return _APPLY[target.kind](s, replay)
    return ApplyResult(applied=True, verified=True)


def _apply_receipt(s: Shipment, ev: LogisticsEvent) -> ApplyResult:
    s.receipt = dict(ev.payload.get("summary", {}))
    exc = []
    for x in ev.payload.get("exceptions", []):
        exc.append(_add_exception(s, x["code"], x.get("severity", "alert"), x["message"],
                                  JourneyStage.INVENTORY_RECONCILIATION, ev, x.get("data", {})))
    return ApplyResult(applied=True, exceptions=exc)


_APPLY = {
    "shipment_created": _apply_created,
    "shipment_updated": _apply_updated,
    "milestone": _apply_milestone,
    "eta": _apply_eta,
    "plan": _apply_plan,
    "document": _apply_document,
    "cost": _apply_cost,
    "allocation": _apply_allocation,
    "allocation_update": _apply_allocation_update,
    "appointment": _apply_appointment,
    "port_terms": _apply_port_terms,
    "exception": _apply_exception,
    "exception_resolved": _apply_exception_resolved,
    "evidence_confirmed": _apply_evidence_confirmed,
    "receipt_reconciled": _apply_receipt,
}


def _add_evidence(s: Shipment, ev: LogisticsEvent, kind: str) -> Evidence:
    e = Evidence(
        evidence_id=f"evd-{ev.idempotency_key[:16]}", kind=kind, code=ev.code,
        occurred_at=ev.occurred_at, source=ev.source, source_ref=ev.source_ref,
        payload=dict(ev.payload), recorded_at=ev.recorded_at,
    )
    if not any(x.evidence_id == e.evidence_id for x in s.evidence):
        s.evidence.append(e)
    return e


def _add_exception(s: Shipment, code: str, severity: str, message: str,
                   stage: Optional[JourneyStage], ev: LogisticsEvent,
                   data: Optional[dict] = None) -> ShipmentException:
    xid = f"exc-{hashlib.sha256((ev.event_id + code + message).encode()).hexdigest()[:12]}"
    for e in s.exceptions:
        if e.exception_id == xid:
            return e
    e = ShipmentException(exception_id=xid, code=code, severity=severity, message=message,
                          stage=stage, detected_at=ev.recorded_at, data=data or {})
    s.exceptions.append(e)
    return e


# ─── In-memory event store (tests, local dev) ─────────────────────────────────


class InMemoryEventStore:
    def __init__(self):
        self._rows: list[dict] = []
        self.fail_next = False

    def append(self, event: LogisticsEvent) -> None:
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("simulated store outage")
        if any(r["company_id"] == event.company_id and r["idempotency_key"] == event.idempotency_key
               for r in self._rows):
            return
        self._rows.append(event.to_dict())

    def load(self, company_id: str) -> list[LogisticsEvent]:
        return [LogisticsEvent.from_dict(r) for r in self._rows if r["company_id"] == company_id]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(v: Any) -> str:
    return str(v or "").strip().upper().replace(" ", "")


def _is_non_human(actor: Any) -> bool:
    """True for empty ids and AI/system actors (case- and space-insensitive)."""
    return str(actor or "").strip().lower() in NON_HUMAN_ACTORS


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _days_between(a: Any, b: Any) -> Optional[int]:
    da, db = _parse_date(a), _parse_date(b)
    if not da or not db:
        return None
    return (db - da).days
