"""
Logistics & Procurement Intelligence API.

Exposes the 9-stage pipeline as REST endpoints.
All endpoints require authentication; sensitive endpoints require
elevated permissions (manage_inventory, approve_actions).

Base path: /api/logistics
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _upsert_cycle(db, company_id: str, po_number: str, stage: int,
                  stage_name: str, supplier_id: str = "", total_cases: int = 0) -> None:
    """Persist the current pipeline stage to procurement_cycles. Best-effort."""
    try:
        db.table("procurement_cycles").upsert({
            "company_id":    company_id,
            "po_number":     po_number,
            "current_stage": stage,
            "stage_name":    stage_name,
            **({"supplier_id":   supplier_id}   if supplier_id   else {}),
            **({"total_cases":   total_cases}    if total_cases   else {}),
        }, on_conflict="company_id,po_number").execute()
    except Exception as exc:
        log.warning("procurement_cycles upsert failed for %s: %s", po_number, exc)
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_db
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.logistics import LogisticsModule
from app.intelligence.wholesale_distribution.logistics.logistics_module import LogisticsConfig
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    FreightBooking,
    LandedCostBreakdown,
    ProcurementCycle,
    PurchaseOrder,
    SKUProfile,
    SupplyChainType,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ─── Module factory (one per request — stateless for API use) ─────────────────


def _get_logistics_module(user: dict, db) -> LogisticsModule:
    """
    Instantiate a LogisticsModule for the current user's company.
    Config is loaded from the companies table.
    """
    company_id = user["company_id"]

    # Load company config
    result = db.table("companies").select("*").eq("id", company_id).execute()
    company = result.data[0] if result.data else {}

    # Build role→email map from the company settings if available
    role_email_map: dict[str, str] = {}
    if company.get("owner_email"):
        role_email_map["owner"] = company["owner_email"]
    if company.get("logistics_manager_email"):
        role_email_map["logistics_manager"] = company["logistics_manager_email"]

    config = LogisticsConfig(
        target_gross_margin_pct=float(company.get("target_gross_margin_pct", 30.0)),
        shipments_per_year=int(company.get("shipments_per_year", 12)),
        port_free_time_days=int(company.get("port_free_time_days", 5)),
        demurrage_rate_per_day=float(company.get("demurrage_rate_per_day", 150.0)),
        role_email_map=role_email_map,
    )

    return LogisticsModule(company_id=company_id, config=config)


# ─── Request / Response Models ────────────────────────────────────────────────


class SKUDataItem(BaseModel):
    """Input for one SKU in the reorder queue evaluation."""
    sku: str
    name: str
    supplier_id: str
    supplier_name: str = ""
    supply_chain_type: str = "overseas"
    origin_country: str = ""
    unit_cost_fob: float = 0.0
    cases_per_pallet: int = 50
    cases_per_40hc_container: int = 1000
    stock_on_hand: int = 0
    stock_committed: int = 0
    stock_in_transit: int = 0
    daily_sales: list[float] = Field(default_factory=list)
    lead_time_days: list[float] = Field(default_factory=list)
    seasonal_indices: Optional[dict[int, float] | list[float]] = None
    last_fob_price: Optional[float] = None
    current_fob_price: Optional[float] = None


class ReorderQueueRequest(BaseModel):
    skus: list[SKUDataItem]


class GeneratePORequest(BaseModel):
    approved_packages: list[dict]   # serialised ReorderDecisionPackage
    supplier_configs: dict[str, dict]


class VendorResponseRequest(BaseModel):
    po_number: str
    supplier_id: str
    email_subject: str
    email_body: str
    shipment_id: Optional[str] = None     # if set, store result as unverified ledger evidence
    message_id: Optional[str] = None


class FreightInvoiceRequest(BaseModel):
    booking_id: str
    invoice_text: str
    total_cases: int


class CustomsHoldRequest(BaseModel):
    booking_id: str
    hold_reason: str
    days_at_port: int


class DemurrageRiskRequest(BaseModel):
    booking_id: str
    arrival_date: str               # ISO date string
    today: Optional[str] = None


class BrokerEmailRequest(BaseModel):
    booking_id: str
    subject: str
    body: str
    shipment_id: Optional[str] = None     # if set, store result as unverified ledger evidence
    message_id: Optional[str] = None


class WarehouseReceiptRequest(BaseModel):
    booking_id: str
    po_number: str
    line_items: list[dict]          # [{sku, qty_ordered, qty_received, condition_notes}]
    received_by: str = "system"
    notes: str = ""
    po_sent_at: Optional[str] = None
    supplier_ship_date: Optional[str] = None


class LandedCostRequest(BaseModel):
    po_number: str
    sku: str
    cases: int
    supplier_id: str
    fob_cost_per_case: float
    duty_rate_pct: float = 0.0
    shipment_date: Optional[str] = None
    # Freight components (optional — pass if no full FreightBooking available)
    ocean_freight_total: Optional[float] = None
    fuel_surcharge_total: Optional[float] = None
    origin_charges_total: Optional[float] = None
    insurance_total: Optional[float] = None
    broker_fees_total: Optional[float] = None
    port_charges_total: Optional[float] = None
    drayage_total: Optional[float] = None


class CostReconcileRequest(BaseModel):
    current: dict       # LandedCostBreakdown fields (serialised)
    previous: dict      # LandedCostBreakdown fields (serialised)
    selling_price_per_case: float = 0.0


class SeasonalIndexRequest(BaseModel):
    sku: str
    daily_sales: list[dict]   # [{date: "2024-01-05", units: 12.5}, ...]


# ─── Stage 1 — Demand Monitoring & Reorder Intelligence ──────────────────────


@router.post("/reorder/evaluate")
async def evaluate_reorder_queue(
    request: ReorderQueueRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Evaluate the full SKU portfolio for reorder needs.

    Returns a prioritised list of reorder decisions (RED first) with
    recommended quantities, container fill analysis, and any alerts.
    """
    lm = _get_logistics_module(user, db)

    sku_data = []
    for item in request.skus:
        try:
            sct = SupplyChainType(item.supply_chain_type)
        except ValueError:
            sct = SupplyChainType.OVERSEAS

        profile = SKUProfile(
            sku=item.sku,
            name=item.name,
            supplier_id=item.supplier_id,
            supplier_name=item.supplier_name or item.supplier_id,   # required by SKUProfile
            supply_chain_type=sct,
            origin_country=item.origin_country,
            unit_cost_fob=item.unit_cost_fob,
            cases_per_pallet=item.cases_per_pallet,
            cases_per_40hc_container=item.cases_per_40hc_container,
            stock_on_hand=item.stock_on_hand,
            stock_committed=item.stock_committed,
            stock_in_transit=item.stock_in_transit,
        )
        # Keys must match ReorderEngine.build_reorder_queue (previously sent
        # "daily_sales"/"lead_time_days", so every SKU was silently skipped).
        seasonal = item.seasonal_indices
        if isinstance(seasonal, dict):
            seasonal = [float(seasonal.get(w, seasonal.get(str(w), 1.0))) for w in range(1, 53)]
        sku_data.append({
            "profile": profile,
            "daily_sales_history": item.daily_sales,
            "lead_time_history": item.lead_time_days,
            "seasonal_indices": seasonal,
            "last_fob_price": item.last_fob_price,
        })

    results = lm.evaluate_reorder_queue(sku_data)

    return {
        "count": len(results),
        "red_count": sum(1 for r in results if r["package"].urgency.value == "red"),
        "yellow_count": sum(1 for r in results if r["package"].urgency.value == "yellow"),
        "reorder_queue": [
            {
                "sku": r["package"].sku_profile.sku if r["package"].sku_profile else None,
                "urgency": r["package"].urgency.value,
                "days_of_supply": round(r["package"].days_of_supply, 1),
                "recommended_qty": r["package"].recommended_qty,
                "projected_stockout_date": r["package"].projected_stockout_date,
                "container_fill_pct": r["package"].container_fill_pct,
                "container_suggestion": r["package"].container_suggestion,
                "lead_time_p80_days": r["package"].lead_time_p80_days,
                "price_change_flag": r["package"].price_change_flag,
                "alert": _serialize_alert(r["alert"]),
            }
            for r in results
        ],
    }


# ─── Stage 2 — Purchase Order Generation ─────────────────────────────────────


@router.post("/po/generate")
async def generate_purchase_orders(
    request: GeneratePORequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Generate purchase orders from a list of approved reorder packages.
    Groups by supplier; suggests fill additions for containers <90% full.
    Returns POs with rendered email bodies ready to send.
    """
    lm = _get_logistics_module(user, db)

    # Deserialize packages (from JSON dict to simple namespace for PO generator)
    from types import SimpleNamespace

    def _dict_to_package(d: dict):
        ns = SimpleNamespace(**d)
        # Ensure urgency is the right type
        from app.intelligence.wholesale_distribution.logistics.pipeline import UrgencyLevel
        if isinstance(ns.urgency, str):
            ns.urgency = UrgencyLevel(ns.urgency)
        return ns

    approved = [_dict_to_package(p) for p in request.approved_packages]
    results = lm.generate_purchase_orders(approved, request.supplier_configs)

    # Persist stage 2 for each generated PO
    for r in results:
        _upsert_cycle(
            db, user["company_id"],
            po_number=r["po"].po_number,
            stage=2, stage_name="po_generated",
            supplier_id=str(r["po"].supplier_id),
            total_cases=r["po"].total_cases,
        )

    return {
        "po_count": len(results),
        "purchase_orders": [
            {
                "po_number": r["po"].po_number,
                "supplier_id": r["po"].supplier_id,
                "total_cases": r["po"].total_cases,
                "total_value": round(r["po"].total_value, 2),
                "status": r["po"].status,
                "line_item_count": len(r["po"].line_items),
                "email_body": r["email_body"],
            }
            for r in results
        ],
    }


# ─── Stage 3 — Vendor Response Tracking ──────────────────────────────────────


@router.post("/vendor/parse-response")
async def parse_vendor_response(
    request: VendorResponseRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Parse a vendor email response for a PO.
    Detects confirmation, rejection, ship date, price changes.
    """
    lm = _get_logistics_module(user, db)

    # Build a minimal PO stub for the parser
    po = PurchaseOrder(
        po_number=request.po_number,
        supplier_id=request.supplier_id,
        supplier_name=request.supplier_id,
        supplier_email="",
        ship_to_address="",
        payment_terms="",
        requested_ship_date="",
    )

    result = lm.parse_vendor_response(po, request.email_subject, request.email_body)
    parsed = result["parsed"]

    _upsert_cycle(
        db, user["company_id"],
        po_number=request.po_number,
        stage=3, stage_name="vendor_response_received",
        supplier_id=request.supplier_id,
    )

    # The parse is an interpretation of an email: if the PO is on a tracked
    # shipment, record it as UNVERIFIED evidence (never an official milestone).
    evidence = None
    if request.shipment_id and parsed.order_confirmed:
        evidence = _record_email_evidence(
            user, db, request.shipment_id, "supplier_confirmed",
            occurred_at=parsed.ship_date or "", source_ref=request.message_id or request.email_subject[:80],
            payload={"ship_date": parsed.ship_date, "po_number": request.po_number},
        )

    return {
        "po_number": request.po_number,
        "is_confirmation": bool(parsed.order_confirmed),
        "full_confirmation": parsed.full_confirmation,
        "partial_confirmation": parsed.partial_confirmation,
        "rejection": parsed.rejection,
        "promised_ship_date": parsed.ship_date,
        "price_change_detected": bool(parsed.price_changes),
        "price_changes": parsed.price_changes,
        "qty_changes": parsed.qty_changes,
        "issues_detected": parsed.issues,
        "requires_human_review": parsed.requires_human_review,
        "raw_summary": parsed.review_reason,
        "verification": "unverified — parsed from email",
        "ledger_evidence": evidence,
        "alert": _serialize_alert(result["alert"]),
    }


# ─── Stage 4 — Freight Cost Capture ──────────────────────────────────────────


@router.post("/freight/parse-invoice")
async def parse_freight_invoice(
    request: FreightInvoiceRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Parse a forwarder invoice and extract cost components.
    Returns the extracted components and total cost.
    """
    lm = _get_logistics_module(user, db)

    booking = FreightBooking(id=request.booking_id)
    updated = lm.capture_freight_invoice(booking, request.invoice_text, request.total_cases)
    summary = lm.freight_cost_summary(updated)

    return {
        "booking_id": request.booking_id,
        "total_cost": summary["total_cost"],
        "components": summary["by_component"],
        "missing_components": summary["missing_components"],
        "component_count": len(summary["by_component"]),
    }


# ─── Stage 6 — Customs Clearance ─────────────────────────────────────────────


@router.post("/customs/hold")
async def log_customs_hold(
    request: CustomsHoldRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Log a customs hold for a container. Fires a high-priority alert.
    """
    lm = _get_logistics_module(user, db)
    result = lm.log_customs_hold(
        booking_id=request.booking_id,
        hold_reason=request.hold_reason,
        days_at_port=request.days_at_port,
    )

    return {
        "booking_id": request.booking_id,
        "milestone": result["event"].milestone,
        "hold_reason": result["event"].hold_reason,
        "demurrage_accruing": result["event"].demurrage_accruing,
        "demurrage_total": result["event"].demurrage_total,
        "alert": _serialize_alert(result["alert"]),
    }


@router.post("/customs/demurrage-risk")
async def check_demurrage_risk(
    request: DemurrageRiskRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Compute current demurrage exposure for a container at port.
    """
    lm = _get_logistics_module(user, db)
    result = lm.check_demurrage_risk(
        booking_id=request.booking_id,
        arrival_date_str=request.arrival_date,
        today_str=request.today,
    )

    return {
        **result["risk"],
        "alert": _serialize_alert(result["alert"]),
    }


@router.post("/customs/parse-broker-email")
async def parse_broker_email(
    request: BrokerEmailRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Parse a customs broker email for clearance milestones.
    Returns detected milestone (entry_filed, duties_paid, released, hold_placed, etc.)
    """
    lm = _get_logistics_module(user, db)
    result = lm.parse_broker_email(request.booking_id, request.subject, request.body)

    _MAP = {"released": "customs_released", "hold_placed": "customs_hold",
            "entry_filed": "entry_filed", "duties_paid": "duties_paid"}
    evidence = None
    if request.shipment_id and result["milestone"] in _MAP:
        evidence = _record_email_evidence(
            user, db, request.shipment_id, _MAP[result["milestone"]],
            source_ref=request.message_id or request.subject[:80],
            payload={"reason": result["event"].hold_reason} if result["event"].hold_reason else {},
        )

    return {
        "booking_id": request.booking_id,
        "milestone_detected": result["milestone"],
        "verification": "unverified — parsed from broker email; confirm before relying on it",
        "ledger_evidence": evidence,
        "event": {
            "milestone": result["event"].milestone,
            "hold_reason": result["event"].hold_reason,
            "demurrage_accruing": result["event"].demurrage_accruing,
        } if result["event"] else None,
    }


# ─── Stage 7 — Warehouse Receipt ─────────────────────────────────────────────


@router.post("/receipt/process")
async def process_warehouse_receipt(
    request: WarehouseReceiptRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Record a warehouse receipt and compute stage-by-stage lead time.
    Returns shortages (if any) and a summary notification.
    """
    lm = _get_logistics_module(user, db)

    booking = FreightBooking(id=request.booking_id)
    result = lm.process_warehouse_receipt(
        booking=booking,
        po_number=request.po_number,
        line_items_received=request.line_items,
        received_by=request.received_by,
        notes=request.notes,
        po_sent_at=request.po_sent_at,
        supplier_ship_date=request.supplier_ship_date,
    )

    receipt = result["receipt"]
    _upsert_cycle(
        db, user["company_id"],
        po_number=request.po_number,
        stage=7, stage_name="warehouse_received",
        total_cases=receipt.total_cases_received,
    )

    return {
        "po_number": request.po_number,
        "total_cases_received": receipt.total_cases_received,
        "shortages": result["shortages"],
        "shortage_count": len(result["shortages"]),
        "lead_time": {
            "po_to_ship_days": receipt.po_to_ship_days,
            "ship_to_arrival_days": receipt.ship_to_arrival_days,
            "arrival_to_customs_days": receipt.arrival_to_customs_days,
            "customs_to_warehouse_days": receipt.customs_to_warehouse_days,
            "total_lead_time_days": receipt.total_lead_time_days,
        },
        "alert": _serialize_alert(result["alert"]),
    }


# ─── Stage 8 — Cost Reconciliation ───────────────────────────────────────────


@router.post("/cost/reconcile")
async def reconcile_costs(
    request: CostReconcileRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Compare current vs. previous shipment landed costs.
    Returns any significant variances with per-case and annualized impact.
    """
    lm = _get_logistics_module(user, db)

    def _dict_to_lcd(d: dict) -> LandedCostBreakdown:
        return LandedCostBreakdown(
            po_number=d.get("po_number", ""),
            sku=d.get("sku", ""),
            cases=d.get("cases", 0),
            supplier_id=d.get("supplier_id", ""),
            fob_cost_per_case=d.get("fob_cost_per_case", 0.0),
            shipment_date=d.get("shipment_date", ""),
            ocean_freight_per_case=d.get("ocean_freight_per_case", 0.0),
            fuel_surcharge_per_case=d.get("fuel_surcharge_per_case", 0.0),
            origin_charges_per_case=d.get("origin_charges_per_case", 0.0),
            insurance_per_case=d.get("insurance_per_case", 0.0),
            customs_duty_per_case=d.get("customs_duty_per_case", 0.0),
            broker_fees_per_case=d.get("broker_fees_per_case", 0.0),
            port_charges_per_case=d.get("port_charges_per_case", 0.0),
            demurrage_per_case=d.get("demurrage_per_case", 0.0),
            drayage_per_case=d.get("drayage_per_case", 0.0),
            exam_fees_per_case=d.get("exam_fees_per_case", 0.0),
        )

    current = _dict_to_lcd(request.current)
    previous = _dict_to_lcd(request.previous)

    result = lm.reconcile_costs(current, previous, request.selling_price_per_case)

    # Persist stage 8 using current shipment's po_number
    _upsert_cycle(
        db, user["company_id"],
        po_number=request.current.get("po_number", ""),
        stage=8, stage_name="cost_reconciled",
        supplier_id=str(request.current.get("supplier_id", "")),
        total_cases=int(request.current.get("cases", 0)),
    )

    return {
        "variance_count": len(result["variances"]),
        "critical_count": sum(1 for v in result["variances"] if v.severity == "critical"),
        "variances": [
            {
                "component": v.component,
                "previous_value": round(v.previous_value, 4),
                "current_value": round(v.current_value, 4),
                "change_pct": round(v.change_pct, 2),
                "per_case_impact": round(v.per_case_impact, 4),
                "annualized_impact": round(v.annualized_impact, 2),
                "severity": v.severity,
                "message": msg,
            }
            for v, msg in zip(result["variances"], result["formatted"])
        ],
        "alerts": [_serialize_alert(a) for a in result["alerts"]],
    }


# ─── Stage 9 — Learning & Analytics ──────────────────────────────────────────


@router.post("/learning/seasonal-indices")
async def build_seasonal_indices(
    request: SeasonalIndexRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Build 52-week seasonal demand indices for a SKU.
    Requires at least 12 months of daily sales data for a confident result.
    """
    lm = _get_logistics_module(user, db)

    # Engine expects daily values oldest-first (previously passed tuples → TypeError)
    ordered = sorted(request.daily_sales, key=lambda d: str(d.get("date", "")))
    daily_sales = [float(d["units"]) for d in ordered]
    indices = lm.build_seasonal_indices(request.sku, daily_sales)

    return {
        "sku": request.sku,
        "status": indices.get("status"),
        "data_months": indices.get("data_months"),
        "index_count": len(indices.get("indices", {})),
        "indices": indices.get("indices", {}),
    }


@router.get("/report/monthly")
async def get_monthly_logistics_report(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Generate the monthly Logistics Intelligence Report.
    Uses stored lead time records and supplier scores from the learning engine.
    """
    lm = _get_logistics_module(user, db)
    report = lm.monthly_logistics_report()

    return report


# ─── Pipeline Status ──────────────────────────────────────────────────────────


@router.get("/pipeline/{po_number}")
async def get_pipeline_status(
    po_number: str,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Get the current stage and summary for a procurement cycle.
    Data is fetched from the procurement_cycles table.
    """
    result = db.table("procurement_cycles") \
        .select("*") \
        .eq("company_id", user["company_id"]) \
        .eq("po_number", po_number) \
        .maybe_single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail=f"Procurement cycle {po_number!r} not found")

    cycle_data = result.data
    return {
        "po_number": cycle_data.get("po_number"),
        "current_stage": cycle_data.get("current_stage"),
        "supplier_id": cycle_data.get("supplier_id"),
        "total_cases": cycle_data.get("total_cases"),
        "started_at": cycle_data.get("started_at"),
        "updated_at": cycle_data.get("updated_at"),
    }


@router.get("/describe")
async def describe_logistics_module(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Return the module's capabilities and current config."""
    lm = _get_logistics_module(user, db)
    return lm.describe()


# ═══════════════════════════════════════════════════════════════════════════
#  Product journey (Workstream 3) — provenance ledger, documents, receiving,
#  monitoring, role-scoped Q&A.  See docs/logistics/INTERFACES.md.
#
#  Nothing below books freight, files customs entries, posts stock or money,
#  or sends external messages.  Stock changes and claims go to the existing
#  PolicyEngine + ApprovalQueue as pending drafts.
# ═══════════════════════════════════════════════════════════════════════════

from datetime import date as _date

from app.intelligence.wholesale_distribution.logistics.assistant import LogisticsAssistant
from app.intelligence.wholesale_distribution.logistics.documents import DocumentIntelligence
from app.intelligence.wholesale_distribution.logistics.journey import (
    HUMAN_VERIFIED_SOURCES,
    MACHINE_SOURCES,
    ConfirmerRequiredError,
    LedgerError,
    RecordNotFoundError,
    TenantMismatchError,
    LogisticsEvent,
    ShipmentLedger,
    SourceType,
    StoreUnavailableError,
)
from app.intelligence.wholesale_distribution.logistics.landed_cost import landed_cost
from app.intelligence.wholesale_distribution.logistics.monitoring import LogisticsMonitor
from app.intelligence.wholesale_distribution.logistics.receiving import (
    ReceivedLine,
    ReceivingEvidence,
    ReceivingReconciler,
    submit_for_approval,
)
from app.intelligence.wholesale_distribution.logistics.store import SupabaseEventStore
from app.intelligence.wholesale_distribution.logistics.visibility import (
    LogisticsAccessDenied,
    LogisticsViewer,
)


def _viewer(user: dict) -> LogisticsViewer:
    v = LogisticsViewer.from_user(user)
    try:
        v.require_access()
    except LogisticsAccessDenied:
        raise HTTPException(status_code=403, detail="Your role has no access to logistics data")
    return v


def _operator(user: dict) -> LogisticsViewer:
    v = _viewer(user)
    if not v.can_operate:
        raise HTTPException(status_code=403, detail="Logistics operations require logistics or management access")
    return v


def _load_ledger(user: dict, db) -> ShipmentLedger:
    """Rebuild this company's ledger from its event log (company from the JWT only)."""
    try:
        return ShipmentLedger.load(user["company_id"], SupabaseEventStore(db))
    except Exception as exc:  # noqa: BLE001
        log.error("logistics ledger load failed for %s: %s", user.get("company_id"), exc)
        raise HTTPException(status_code=503, detail="Logistics data temporarily unavailable")


def _ledger_http_error(exc: LedgerError) -> HTTPException:
    """
    Map ledger errors to FIXED client messages.  Exception text is logged
    server-side only (never echoed to the caller).
    """
    log.info("logistics ledger rejected request: %s", type(exc).__name__)
    if isinstance(exc, RecordNotFoundError):
        return HTTPException(status_code=404, detail="Shipment, order, evidence or exception not found")
    if isinstance(exc, ConfirmerRequiredError):
        return HTTPException(status_code=422, detail="This must be confirmed by a named person")
    if isinstance(exc, TenantMismatchError):
        return HTTPException(status_code=403, detail="Record belongs to another company")
    return HTTPException(status_code=422, detail=(
        "Invalid logistics event — check kind, milestone code and payload (see docs/logistics/INTERFACES.md)"))


def _ingest(ledger: ShipmentLedger, event: LogisticsEvent) -> dict:
    try:
        return ledger.ingest(event).to_dict()
    except LedgerError as exc:
        raise _ledger_http_error(exc)
    except StoreUnavailableError as exc:
        log.error("logistics event store write failed: %s", exc)
        raise HTTPException(status_code=503, detail="Event not saved — store unavailable; nothing was changed")


def _record_email_evidence(user: dict, db, shipment_id: str, code: str, occurred_at: str = "",
                           source_ref: str = "", payload: Optional[dict] = None) -> Optional[dict]:
    """Store an email interpretation as UNVERIFIED evidence. Best-effort."""
    try:
        ledger = ShipmentLedger.load(user["company_id"], SupabaseEventStore(db))
        if ledger.get(shipment_id) is None:
            return None
        return ledger.ingest(LogisticsEvent(
            company_id=user["company_id"], shipment_id=shipment_id, kind="milestone", code=code,
            source=SourceType.EMAIL_PARSED, occurred_at=occurred_at, source_ref=source_ref,
            payload=payload or {},
        )).to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record email evidence on %s: %s", shipment_id, exc)
        return None


def ingest_integration_events(db, company_id: str, events: list[LogisticsEvent]) -> list[dict]:
    """
    Server-side entry point for connectors / webhooks / Celery workers that hold
    authoritative feeds (carrier, terminal, customs, broker EDI, WMS, ERP).
    Not exposed over HTTP: a person cannot claim a machine source.
    """
    ledger = ShipmentLedger.load(company_id, SupabaseEventStore(db))
    out = []
    for ev in events:
        if ev.company_id != company_id:
            out.append({"applied": False, "message": "company mismatch — rejected"})
            continue
        try:
            out.append(ledger.ingest(ev).to_dict())
        except (LedgerError, StoreUnavailableError) as exc:
            log.warning("integration event %s rejected: %s", ev.event_id, exc)
            out.append({"applied": False, "message": type(exc).__name__})
    return out


class JourneyEventRequest(BaseModel):
    shipment_id: str
    kind: str
    source: str = "user_confirmed"
    code: str = ""
    occurred_at: str = ""
    source_ref: str = ""
    payload: dict = Field(default_factory=dict)


class EvidenceConfirmRequest(BaseModel):
    occurred_at: str = ""
    note: str = ""


class DocumentIngestRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    doc_id: str = ""
    hint: str = ""                       # e.g. file name or email subject
    reviewed: bool = False               # True = I (the caller) reviewed this document
    received_at: str = ""


class ReceiveLineModel(BaseModel):
    sku: str
    good_cases: int = Field(ge=0)
    damaged_cases: int = Field(default=0, ge=0)
    lot: str = ""
    damage_description: str = ""


class ReceivingEvidenceModel(BaseModel):
    damage_noted_on_delivery_receipt: Optional[bool] = None
    seal_intact: Optional[bool] = None
    seal_number_matches: Optional[bool] = None
    damage_cause: str = "unknown"
    photos_attached: bool = False
    damage_report_doc_id: str = ""
    delivering_carrier: str = ""


class ReceivingRequest(BaseModel):
    shipment_id: str
    lines: list[ReceiveLineModel] = Field(min_length=1)
    evidence: ReceivingEvidenceModel = Field(default_factory=ReceivingEvidenceModel)
    received_at: str = ""
    landed_cost_per_case: Optional[dict[str, float]] = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    as_of: Optional[str] = None


def _as_of(v: Optional[str]) -> Optional[_date]:
    if not v:
        return None
    try:
        return _date.fromisoformat(v[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail="as_of must be YYYY-MM-DD")


@router.post("/journey/events")
async def record_journey_event(
    request: JourneyEventRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Record a fact about a shipment from a person.  Only human sources are
    accepted here: ``user_confirmed`` (you vouch for it — recorded under your
    user id) or unverified sources (``email_parsed``, ``ai_inferred``,
    ``manual_note``).  Machine sources come only through connectors.
    """
    _operator(user)
    try:
        source = SourceType(request.source)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown source {request.source!r}")
    if source in MACHINE_SOURCES or source == SourceType.DOCUMENT_VALIDATED:
        raise HTTPException(status_code=403, detail=(
            f"source '{source.value}' can only be recorded by an integration "
            "(or via /documents/ingest for reviewed documents)"))
    ledger = _load_ledger(user, db)
    event = LogisticsEvent(
        company_id=user["company_id"], shipment_id=request.shipment_id, kind=request.kind,
        source=source, code=request.code, occurred_at=request.occurred_at,
        source_ref=request.source_ref, payload=request.payload,
        confirmed_by=user["sub"] if source in HUMAN_VERIFIED_SOURCES else "",
    )
    return _ingest(ledger, event)


@router.get("/journey/shipments")
async def list_journey_shipments(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Shipments this user may see, summarised for their role."""
    viewer = _viewer(user)
    ledger = _load_ledger(user, db)
    out = []
    for s in viewer.visible(ledger.shipments()):
        d = viewer.project(s)
        out.append({
            "shipment_id": d["shipment_id"], "current_stage": d["current_stage"],
            "current_stage_label": d["current_stage_label"],
            "container_numbers": d["container_numbers"], "bol_number": d["bol_number"],
            "po_numbers": d.get("po_numbers", []), "destination": d["destination"],
            "eta": d["eta"], "open_exceptions": len(d["exceptions"]),
            "pending_evidence": len(d.get("pending_evidence", [])),
        })
    return {"count": len(out), "shipments": out}


@router.get("/journey/shipments/{shipment_id}")
async def get_journey_shipment(
    shipment_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Full 14-stage view of one shipment, projected for the caller's role."""
    viewer = _viewer(user)
    ledger = _load_ledger(user, db)
    s = ledger.get(shipment_id)
    if s is None or not viewer.can_see_shipment(s):
        raise HTTPException(status_code=404, detail="Shipment not found")
    return viewer.project(s)


@router.get("/journey/shipments/{shipment_id}/landed-cost")
async def get_landed_cost(
    shipment_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    viewer = _viewer(user)
    if not viewer.can_view_commercial:
        raise HTTPException(status_code=403, detail="Landed cost requires financial access")
    ledger = _load_ledger(user, db)
    s = ledger.get(shipment_id)
    if s is None or not viewer.can_see_shipment(s):
        raise HTTPException(status_code=404, detail="Shipment not found")
    return landed_cost(s)


@router.post("/journey/evidence/{evidence_id}/confirm")
async def confirm_journey_evidence(
    evidence_id: str,
    request: EvidenceConfirmRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """A named person promotes an unverified report (email / AI / document) to a verified fact."""
    _operator(user)
    ledger = _load_ledger(user, db)
    try:
        return ledger.confirm_evidence(evidence_id, confirmed_by=user["sub"],
                                       occurred_at=request.occurred_at, note=request.note).to_dict()
    except LedgerError as exc:
        raise _ledger_http_error(exc)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail="Event not saved — store unavailable; nothing was changed")


@router.post("/documents/ingest")
async def ingest_document(
    request: DocumentIngestRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Identify, extract, validate and link a logistics document.  Extracted
    values are evidence: unless ``reviewed`` is true (you vouch for the
    document) any milestone it suggests stays unverified.
    """
    _operator(user)
    ledger = _load_ledger(user, db)
    di = DocumentIntelligence()
    doc, events = di.process(request.text, ledger, doc_id=request.doc_id, hint=request.hint,
                             reviewed_by=user["sub"] if request.reviewed else "",
                             received_at=request.received_at)
    results = [_ingest(ledger, ev) for ev in events]
    return {"document": doc.to_dict(), "ledger_results": results,
            "linked": bool(doc.linked_shipment_id)}


@router.post("/receiving/reconcile")
async def reconcile_receipt(
    request: ReceivingRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Reconcile a warehouse count against the PO.  Damaged cases are proposed
    to quarantine; every stock posting and claim is queued for approval via
    the PolicyEngine + ApprovalQueue.  Nothing is posted or sent.
    """
    viewer = _operator(user)
    ledger = _load_ledger(user, db)
    shipment = ledger.get(request.shipment_id)
    if shipment is None or not viewer.can_see_shipment(shipment):
        raise HTTPException(status_code=404, detail="Shipment not found")

    reconciler = ReceivingReconciler(user["company_id"])
    try:
        rec = reconciler.reconcile(
            shipment,
            [ReceivedLine(**l.model_dump()) for l in request.lines],
            received_by=user["sub"],
            evidence=ReceivingEvidence(**request.evidence.model_dump()),
            received_at=request.received_at,
            landed_cost_per_case=request.landed_cost_per_case,
        )
    except PermissionError:
        raise HTTPException(status_code=404, detail="Shipment not found")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid receiving counts (case quantities cannot be negative)")

    # The count was entered by a named person → verified receipt milestone.
    milestone = _ingest(ledger, LogisticsEvent(
        company_id=user["company_id"], shipment_id=shipment.shipment_id, kind="milestone",
        code="warehouse_received", source=SourceType.USER_CONFIRMED, occurred_at=rec.received_at,
        source_ref=rec.reconciliation_id, confirmed_by=user["sub"],
    ))
    summary = _ingest(ledger, reconciler.to_ledger_event(rec, SourceType.USER_CONFIRMED, user["sub"]))

    from app.actions.approval_queue import ApprovalQueue
    from app.domain.policy import PolicyEngine
    try:
        approvals = await submit_for_approval(
            rec.proposals, PolicyEngine(user["company_id"], db), ApprovalQueue(db))
    except Exception as exc:  # noqa: BLE001
        log.error("approval queue submission failed for %s: %s", rec.reconciliation_id, exc)
        approvals = [{"status": "not_queued", "error": "approval queue unavailable — resubmit"}]

    body = rec.to_dict()
    if not viewer.can_view_commercial:
        body.pop("financial_implications", None)
        for c in body["claims"]:
            c["claimed_amount_estimate_usd"] = None
            c["lines"] = [{k: v for k, v in l.items() if k not in ("unit_cost", "extended_cost")}
                          for l in c["lines"]]
            c["narrative"] = "[amounts restricted] " + c["narrative"].split(" This draft covers")[0]
        for l in body["lines"]:
            l.pop("unit_cost", None)
            l.pop("landed_cost_per_case", None)
        for pr in body["proposals"]:
            pr["amount"] = None
            pr["proposed_changes"].pop("claim", None)
        for a in approvals:
            a.pop("amount", None)
    body["ledger"] = {"milestone": milestone, "summary": summary}
    body["approvals"] = approvals
    return body


@router.get("/monitor")
async def logistics_monitor(
    as_of: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Prioritized logistics alerts with recommended actions and reviewable drafts (never sent)."""
    viewer = _viewer(user)
    ledger = _load_ledger(user, db)
    alerts = LogisticsMonitor(ledger).scan(_as_of(as_of))
    visible = viewer.filter_alerts(alerts, {s.shipment_id: s for s in ledger.shipments()})
    return {"count": len(visible), "alerts": visible}


@router.post("/ask")
async def ask_lola_logistics(
    request: AskRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Answer a logistics question from verified ledger data, scoped to the
    caller's role.  Callable as a tool by the chat orchestrator.
    """
    viewer = _viewer(user)
    ledger = _load_ledger(user, db)
    return LogisticsAssistant(ledger, viewer).ask(request.question, as_of=_as_of(request.as_of)).to_dict()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _serialize_alert(alert) -> Optional[dict]:
    """Convert a LogisticsAlert to a JSON-serialisable dict, or None."""
    if alert is None:
        return None
    return {
        "alert_type": alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
        "severity": alert.severity,
        "subject": alert.subject,
        "body": alert.body,
        "recipients": alert.recipients,
        "financial_impact_usd": alert.financial_impact_usd,
    }
