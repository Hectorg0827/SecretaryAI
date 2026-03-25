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
    seasonal_indices: Optional[dict[int, float]] = None
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
            supply_chain_type=sct,
            origin_country=item.origin_country,
            unit_cost_fob=item.unit_cost_fob,
            cases_per_pallet=item.cases_per_pallet,
            cases_per_40hc_container=item.cases_per_40hc_container,
            stock_on_hand=item.stock_on_hand,
            stock_committed=item.stock_committed,
            stock_in_transit=item.stock_in_transit,
        )
        sku_data.append({
            "profile": profile,
            "daily_sales": item.daily_sales,
            "lead_time_days": item.lead_time_days,
            "seasonal_indices": item.seasonal_indices,
            "last_fob_price": item.last_fob_price,
            "current_fob_price": item.current_fob_price,
        })

    results = lm.evaluate_reorder_queue(sku_data)

    return {
        "count": len(results),
        "red_count": sum(1 for r in results if r["package"].urgency.value == "red"),
        "yellow_count": sum(1 for r in results if r["package"].urgency.value == "yellow"),
        "reorder_queue": [
            {
                "sku": r["package"].sku,
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
    )

    result = lm.parse_vendor_response(po, request.email_subject, request.email_body)
    parsed = result["parsed"]

    return {
        "po_number": request.po_number,
        "is_confirmation": parsed.is_confirmation,
        "promised_ship_date": parsed.promised_ship_date,
        "price_change_detected": parsed.price_change_detected,
        "issues_detected": parsed.issues_detected,
        "raw_summary": parsed.raw_summary,
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

    booking = FreightBooking(booking_id=request.booking_id)
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

    return {
        "booking_id": request.booking_id,
        "milestone_detected": result["milestone"],
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

    booking = FreightBooking(booking_id=request.booking_id)
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

    # Convert [{date, units}] → [(date_str, float)]
    daily_sales = [(d["date"], float(d["units"])) for d in request.daily_sales]
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
