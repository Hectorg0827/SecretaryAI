"""
Logistics & Procurement Intelligence Module.

The 9-Stage Pipeline (calculation engines): from demand signal to warehouse receipt.

  Stage 1  — Demand Monitoring & Reorder Intelligence   (reorder_engine.py)
  Stage 2  — Purchase Order Generation                  (po_generator.py)
  Stage 3  — PO Transmission & Vendor Response Tracking (vendor_tracker.py)
  Stage 4  — Freight Booking & Cost Capture             (freight.py)
  Stage 5  — Ocean / Ground Transit Tracking            (freight.py — TransitTracker)
  Stage 6  — Port Arrival & Customs Clearance           (customs.py — CustomsMonitor)
  Stage 7  — Domestic Drayage & Warehouse Receipt       (customs.py — ReceiptProcessor)
  Stage 8  — Cost Reconciliation & Variance Detection   (cost_reconciliation.py)
  Stage 9  — Learning & Refinement                      (learning.py)

The 14-stage product journey (system of record, Workstream 3):

  journey.py      — provenance-tracked, event-sourced shipment ledger
  documents.py    — document identify / extract / validate / link
  receiving.py    — receipt reconciliation, damage segregation, claim drafts
  landed_cost.py  — landed cost per case with final/estimated label
  monitoring.py   — proactive, prioritized alerts with reviewable drafts
  visibility.py   — role-scoped projections (uses app.auth.rbac)
  assistant.py    — Lola's logistics question answering
  store.py        — Supabase event store + parcel-carrier adapter

Shared data models for the 9 stages live in pipeline.py.
Email notifications live in notifications.py.
Interface documentation: docs/logistics/INTERFACES.md
"""

from .pipeline import (
    ProcurementCycle,
    ProcurementStage,
    SKUProfile,
    SupplyChainType,
    UrgencyLevel,
)
from .logistics_module import LogisticsModule
from .journey import (
    JourneyStage,
    LogisticsEvent,
    Shipment,
    ShipmentLedger,
    SourceType,
)
from .assistant import LogisticsAssistant
from .documents import DocumentIntelligence
from .monitoring import LogisticsMonitor
from .receiving import ReceivingReconciler
from .visibility import LogisticsViewer

__all__ = [
    "LogisticsModule",
    "ProcurementCycle",
    "ProcurementStage",
    "SKUProfile",
    "SupplyChainType",
    "UrgencyLevel",
    "JourneyStage",
    "LogisticsEvent",
    "Shipment",
    "ShipmentLedger",
    "SourceType",
    "LogisticsAssistant",
    "DocumentIntelligence",
    "LogisticsMonitor",
    "ReceivingReconciler",
    "LogisticsViewer",
]
