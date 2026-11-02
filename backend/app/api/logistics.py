"""
Logistics & Procurement Intelligence API.

Exposes the 9-stage pipeline as REST endpoints.
All endpoints require authentication; sensitive endpoints require
elevated permissions (manage_inventory, approve_actions).

Base path: /api/logistics
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.deps import get_current_user, get_db
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.logistics import LogisticsModule
from app.intelligence.wholesale_distribution.logistics.logistics_module import LogisticsConfig
from app.intelligence.wholesale_distribution.logistics.vendor_tracker import VendorResponseParser
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
    try:
        result = db.table("companies").select("*").eq("id", company_id).execute()
        if (not isinstance(result.data, list) or len(result.data) != 1
                or not isinstance(result.data[0], dict)
                or str(result.data[0].get("id")) != str(company_id)):
            raise ValueError("Company configuration absent or ambiguous")
        company = result.data[0]
    except Exception as exc:
        log.warning("Supply-chain tenant config unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Company configuration unavailable") from exc

    # Build role→email map from the company settings if available
    role_email_map: dict[str, str] = {}
    if company.get("owner_email"):
        role_email_map["owner"] = company["owner_email"]
    if company.get("logistics_manager_email"):
        role_email_map["logistics_manager"] = company["logistics_manager_email"]

    try:
        config = LogisticsConfig(
            target_gross_margin_pct=float(company.get("target_gross_margin_pct", 30.0)),
            shipments_per_year=int(company.get("shipments_per_year", 12)),
            port_free_time_days=int(company.get("port_free_time_days", 5)),
            demurrage_rate_per_day=float(company.get("demurrage_rate_per_day", 150.0)),
            role_email_map=role_email_map,
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=503, detail="Company configuration unavailable") from exc

    return LogisticsModule(company_id=company_id, config=config)


# ─── Request / Response Models ────────────────────────────────────────────────


class SKUDataItem(BaseModel):
    """Input for one SKU in the reorder queue evaluation."""
    sku: str
    name: str
    supplier_id: str
    supplier_name: str = ""
    supply_chain_type: SupplyChainType = SupplyChainType.OVERSEAS
    origin_country: str = ""
    unit_cost_fob: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    cases_per_pallet: int = Field(default=50, strict=True, gt=0)
    cases_per_40hc_container: int = Field(default=1000, strict=True, gt=0)
    stock_on_hand: int = Field(default=0, strict=True, ge=0)
    stock_committed: int = Field(default=0, strict=True, ge=0)
    stock_in_transit: int = Field(default=0, strict=True, ge=0)
    minimum_order_cases: int = Field(default=0, strict=True, ge=0)
    # Demand is required: silently returning an empty queue for absent history
    # would incorrectly look like evidence that every SKU is sufficiently stocked.
    daily_sales: list[Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(min_length=1)
    # A missing lead history cannot safely produce a timing-based proposal.
    lead_time_days: list[Annotated[float, Field(gt=0, allow_inf_nan=False)]] = Field(min_length=1)
    seasonal_indices: Optional[list[float] | dict[int, float]] = None
    last_fob_price: Optional[float] = None
    current_fob_price: Optional[float] = None


class ReorderQueueRequest(BaseModel):
    skus: list[SKUDataItem] = Field(min_length=1, max_length=500)


def _weekly_seasonal_indices(
    indices: Optional[list[float] | dict[int, float]],
) -> Optional[list[float]]:
    """Map explicit 0- or 1-based API week keys to engine's 52-value array.

    Reject ambiguous/incomplete calendars rather than silently applying the
    wrong seasonal multiplier to a forecast.
    """
    if indices is None:
        return None
    if isinstance(indices, list):
        values = indices
    elif set(indices) == set(range(52)):
        values = [indices[i] for i in range(52)]
    elif set(indices) == set(range(1, 53)):
        values = [indices[i] for i in range(1, 53)]
    else:
        raise HTTPException(status_code=422, detail="Seasonal indices must cover exactly 52 weeks")
    if len(values) != 52 or any(not 0 <= v < float("inf") for v in values):
        raise HTTPException(status_code=422, detail="Seasonal indices must be 52 finite nonnegative multipliers")
    return values


class GeneratePORequest(BaseModel):
    approved_packages: list[dict]   # serialised ReorderDecisionPackage
    supplier_configs: dict[str, dict]


class VendorResponseRequest(BaseModel):
    po_number: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=200)
    email_subject: str = Field(max_length=500)
    email_body: str = Field(min_length=1, max_length=25000)


class FreightInvoiceRequest(BaseModel):
    booking_id: str = Field(min_length=1, max_length=150)
    invoice_text: str = Field(min_length=1, max_length=50000)
    total_cases: int = Field(gt=0, le=10000000)


class CustomsHoldRequest(BaseModel):
    booking_id: str = Field(min_length=1, max_length=150)
    hold_reason: str = Field(min_length=1, max_length=1000)
    days_at_port: int = Field(ge=0, le=3650)


class DemurrageRiskRequest(BaseModel):
    booking_id: str = Field(min_length=1, max_length=150)
    arrival_date: date
    today: Optional[date] = None


class BrokerEmailRequest(BaseModel):
    booking_id: str = Field(min_length=1, max_length=150)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=25000)


class LegacyReceiptLine(BaseModel):
    """Caller quantity preview; never a booked warehouse receipt line."""
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=120)
    qty_ordered: int = Field(strict=True, gt=0, le=10000000)
    qty_received: int = Field(strict=True, ge=0, le=10000000)
    unit: Literal["case"]
    condition_notes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def reject_unmatched_quantity(self):
        if self.qty_received > self.qty_ordered:
            raise ValueError("Received cases exceed order line; verify units and order")
        return self


class WarehouseReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(min_length=1, max_length=150)
    po_number: str = Field(min_length=1, max_length=100)
    line_items: list[LegacyReceiptLine] = Field(min_length=1, max_length=200)
    received_by: str = Field(default="system", max_length=150)
    notes: str = Field(default="", max_length=2000)
    po_sent_at: Optional[str] = None
    supplier_ship_date: Optional[str] = None

    @model_validator(mode="after")
    def reject_ambiguous_duplicate_skus(self):
        codes = [item.sku for item in self.line_items]
        if len(codes) != len(set(codes)):
            raise ValueError("Legacy receipt preview cannot disambiguate repeated SKU lines")
        for field in ("po_sent_at", "supplier_ship_date"):
            raw = getattr(self, field)
            if raw:
                try:
                    value = date.fromisoformat(raw)
                except ValueError as exc:
                    raise ValueError(f"{field} must be a valid ISO date") from exc
                if value > date.today():
                    raise ValueError(f"{field} cannot be in the future")
        if self.po_sent_at and self.supplier_ship_date and self.po_sent_at > self.supplier_ship_date:
            raise ValueError("Supplier shipment must not precede PO sent date")
        return self


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


class LandedCostComparisonInput(BaseModel):
    """Unverified per-case USD component observations, never invoice evidence."""
    po_number: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=120)
    cases: int = Field(gt=0, le=10000000)
    supplier_id: str = Field(min_length=1, max_length=200)
    shipment_date: str = ""
    fob_cost_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    ocean_freight_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    fuel_surcharge_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    origin_charges_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    insurance_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    customs_duty_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    broker_fees_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    port_charges_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    demurrage_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    drayage_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    exam_fees_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0


class CostReconcileRequest(BaseModel):
    current: LandedCostComparisonInput
    previous: LandedCostComparisonInput
    selling_price_per_case: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0


class SeasonalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    units: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @field_validator("units", mode="before")
    @classmethod
    def actual_numeric_quantity(cls, value):
        if type(value) not in (int, float):
            raise ValueError("Sales units require an explicit numeric observation")
        return value

    @field_validator("date")
    @classmethod
    def no_future_observation(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Sales observation cannot be future dated")
        return value


class SeasonalIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=120)
    daily_sales: list[SeasonalObservation] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def chronological_unique_observations(self):
        dates = [item.date for item in self.daily_sales]
        if dates != sorted(set(dates)):
            raise ValueError("Sales observations must have unique ascending dates")
        # The index builder buckets observations by position (day N of the
        # series), so a skipped calendar day would silently shift every later
        # week. Require one entry per day; a day without sales is an explicit 0.
        if (dates[-1] - dates[0]).days + 1 != len(dates):
            raise ValueError(
                "Sales history needs one entry for every calendar day with no gaps; "
                "enter 0 for days without sales"
            )
        return self



# ─── Durable purchasing & receiving (V6 schema) ──────────────────────────────
# These endpoints are distinct from the nine-stage *intelligence* pipeline.
# Drafts cannot book stock. Only separate authenticated approval may post a
# staged physical receipt. All writes are one atomic SQL RPC (no best-effort
# multi-table updates), and never write to QuickBooks implicitly.

from uuid import UUID


class DurablePOLine(BaseModel):
    sku: str = Field(min_length=1, max_length=120)
    qty_ordered: int = Field(gt=0, le=10000000)


class DurablePORequest(BaseModel):
    po_number: str = Field(min_length=1, max_length=100)
    supplier_ref: str = Field(min_length=1, max_length=200)
    warehouse_id: UUID
    lines: list[DurablePOLine] = Field(min_length=1, max_length=100)


from decimal import Decimal, InvalidOperation
class PurchasingEvidenceClaimRequest(BaseModel):
    evidence_key: str = Field(min_length=1, max_length=150)
    document_type: Literal[
        "purchase_invoice", "packing_list", "bill_of_lading",
        "receiving_photo", "damage_report", "freight_invoice",
        "customs_entry", "insurance_invoice", "broker_invoice",
        "drayage_invoice", "other",
    ]
    storage_object_key: str = Field(min_length=1, max_length=400)
    claimed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claimed_amount: Optional[Decimal] = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")


class DurableReceiptLine(BaseModel):
    sku: str = Field(min_length=1, max_length=120)
    lot_code: str = Field(default="", max_length=120)
    accepted_qty: int = Field(default=0, ge=0, le=10000000)
    damaged_qty: int = Field(default=0, ge=0, le=10000000)
    quarantined_qty: int = Field(default=0, ge=0, le=10000000)


class DurableReceiptRequest(BaseModel):
    receipt_key: str = Field(min_length=1, max_length=150)
    lines: list[DurableReceiptLine] = Field(min_length=1, max_length=200)


def _execute_purchasing_rpc(db, name: str, payload: dict) -> dict:
    """Safe PostgREST error handling; never disclose raw SQL, row or tokens."""
    try:
        data = db.rpc(name, payload).execute().data
    except Exception as exc:
        code = str(getattr(exc, "code", ""))
        if code == "42501":
            raise HTTPException(status_code=403, detail="Purchase approval not permitted") from exc
        if code == "P0002":
            raise HTTPException(status_code=404, detail="Purchase or receipt not found") from exc
        if code == "22023":
            raise HTTPException(status_code=422, detail="Invalid purchasing data") from exc
        if code in ("23503", "23505", "23514"):
            raise HTTPException(status_code=409,
                detail="Operation conflicts with approval status, quantity or idempotency rules") from exc
        log.error("Durable purchasing RPC %s unavailable (%s)", name, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Purchasing database unavailable") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=503, detail="Purchasing database result unavailable")
    return data


def _require_purchasing_employee(user: dict) -> None:
    # Never grant broader `manage_inventory` to back-office to enable one
    # capability. The SQL procedure additionally verifies current DB user.
    if user.get("role") not in ("owner", "manager", "back_office"):
        raise HTTPException(status_code=403, detail="Purchasing access denied")


def _read_purchasing_rows(query) -> list[dict]:
    try:
        result = query.execute()
        if not isinstance(result.data, list):
            raise ValueError("Missing query rows")
        return result.data
    except Exception as exc:
        log.error("Purchasing read temporarily unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Purchasing records unavailable") from exc


@router.get("/warehouses")
async def list_purchasing_warehouses(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Tenant warehouses a draft PO may target (read-only picker data)."""
    _require_purchasing_employee(user)
    rows = _read_purchasing_rows(
        db.table("stock_warehouses")
          .select("id,code,name")
          .eq("company_id", user["company_id"])
          .order("code")
          .limit(201)
    )
    if len(rows) > 200:
        raise HTTPException(status_code=503, detail="Warehouse list exceeds supported scope")
    return {"warehouses": rows, "count": len(rows)}


@router.get("/orders")
async def list_durable_purchase_orders(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    _require_purchasing_employee(user)
    rows = _read_purchasing_rows(
        db.table("purchasing_orders")
          .select("id,po_number,supplier_ref,status,warehouse_id,created_at,approved_at")
          .eq("company_id", user["company_id"])
          .order("created_at", desc=True)
          .limit(100)
    )
    return {"orders": rows, "count": len(rows)}


# ─── Quarantined document-byte upload / integrity only ────────────────────────

from fastapi import File, UploadFile
from app.services.purchasing_evidence_bytes import (
    inspect_uploaded_evidence, store_quarantined_evidence,
)


from app.config import get_settings
from app.services.purchasing_evidence_malware import scan_quarantined_evidence


@router.post("/evidence/{evidence_id}/scan")
async def scan_purchasing_evidence_for_malware(
    evidence_id: UUID,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Invoke a real local ClamAV scanner on private, integrity-matched bytes.

    Clients cannot supply the scan verdict. Clean does not authorize a
    download, financial approval, or any QuickBooks write.
    """
    _require_purchasing_employee(user)
    company_id = user["company_id"]
    evidence_id_str = str(evidence_id)
    claim = _read_purchasing_rows(
        db.table("purchasing_evidence_claims")
        .select("id,storage_object_key,claimed_sha256")
        .eq("company_id", company_id).eq("id", evidence_id_str).limit(2)
    )
    if not claim:
        raise HTTPException(status_code=404, detail="Evidence claim not found")
    if len(claim) != 1:
        raise HTTPException(status_code=503, detail="Ambiguous evidence claim")
    checks = _read_purchasing_rows(
        db.table("purchasing_evidence_integrity_checks")
        .select("id,computed_sha256,byte_count,detected_mime")
        .eq("company_id", company_id).eq("evidence_id", evidence_id_str).limit(2)
    )
    if len(checks) != 1:
        raise HTTPException(status_code=409, detail="Verified upload integrity required")
    if checks[0].get("computed_sha256") != claim[0].get("claimed_sha256"):
        raise HTTPException(status_code=409, detail="Stored integrity does not match claim")
    scans = _read_purchasing_rows(
        db.table("purchasing_evidence_malware_scans")
        .select("id").eq("company_id", company_id)
        .eq("evidence_id", evidence_id_str).limit(2)
    )
    if scans:
        raise HTTPException(status_code=409, detail="Document already has a malware scan record")
    active = _read_purchasing_rows(
        db.table("users").select("id,role,is_active")
        .eq("company_id", company_id).eq("id", user["sub"]).limit(2)
    )
    if len(active) != 1 or active[0].get("is_active") is not True or (
        active[0].get("role") not in ("owner", "manager", "back_office")
    ):
        raise HTTPException(status_code=403, detail="Scanner operator not authorized")
    result = await scan_quarantined_evidence(
        db,
        company_id=company_id,
        storage_object_key=claim[0]["storage_object_key"],
        expected_sha256=checks[0]["computed_sha256"],
        expected_byte_count=checks[0]["byte_count"],
        expected_mime=checks[0]["detected_mime"],
        clamav_socket_path=get_settings().clamav_unix_socket,
    )
    attestation = _execute_purchasing_rpc(
        db, "attest_purchasing_evidence_malware_scan", {
            "p_company_id": company_id,
            "p_evidence_id": evidence_id_str,
            "p_computed_sha256": result.digest,
            "p_byte_count": result.byte_count,
            "p_scan_status": result.scan_status,
            "p_actor": user["sub"],
        },
    )
    return {
        **attestation, "scan_status": result.scan_status,
        "malware_scan_engine": "clamav",
        "approved_for_review": False,
        "safe_to_view": False, "accounting_reconciled": False,
        "landed_cost_verified": False,
    }


@router.post("/evidence/{evidence_id}/approve-review")
async def approve_purchasing_evidence_review(
    evidence_id: UUID,
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    """Independent reviewer can acknowledge a previously CLEAN scanned file.

    The SQL enforces current role, same tenant, clean scan, and that the
    reviewer did not submit or scan the document. No file is made public.
    """
    return {
        **_execute_purchasing_rpc(
            db, "approve_purchasing_evidence_for_review", {
                "p_company_id": user["company_id"],
                "p_evidence_id": str(evidence_id),
                "p_actor": user["sub"],
            },
        ),
        "safe_to_view": False,
        "accounting_reconciled": False,
        "landed_cost_verified": False,
    }


@router.get("/evidence/{evidence_id}/malware-status")
async def get_purchasing_evidence_malware_status(
    evidence_id: UUID,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    _require_purchasing_employee(user)
    company_id = user["company_id"]
    evidence_id_str = str(evidence_id)
    claims = _read_purchasing_rows(
        db.table("purchasing_evidence_claims").select("id")
        .eq("company_id", company_id).eq("id", evidence_id_str).limit(2)
    )
    if not claims:
        raise HTTPException(status_code=404, detail="Evidence claim not found")
    scans = _read_purchasing_rows(
        db.table("purchasing_evidence_malware_scans")
        .select("id,scan_status,engine,scanned_at")
        .eq("company_id", company_id).eq("evidence_id", evidence_id_str).limit(2)
    )
    approvals = _read_purchasing_rows(
        db.table("purchasing_evidence_review_approvals")
        .select("id,review_status,approved_at")
        .eq("company_id", company_id).eq("evidence_id", evidence_id_str).limit(2)
    )
    if len(scans) > 1 or len(approvals) > 1:
        raise HTTPException(status_code=503, detail="Ambiguous evidence review state")
    status = scans[0]["scan_status"] if scans else "not_scanned"
    if approvals and status != "clean":
        raise HTTPException(status_code=503, detail="Inconsistent malware review state")
    return {
        "evidence_id": evidence_id_str,
        "malware_scan": scans[0] if scans else None,
        "scan_status": status,
        "review_approval": approvals[0] if approvals else None,
        "approved_for_review": bool(approvals),
        "safe_to_view": False,
        "accounting_reconciled": False,
        "landed_cost_verified": False,
    }


@router.post("/evidence/{evidence_id}/upload", status_code=201)
async def upload_purchasing_evidence_bytes(
    evidence_id: UUID,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Check uploaded bytes against a prior claim and store in private quarantine.

    Hash match is *not* malware clearance, invoice approval, or a QuickBooks
    write. Partial failure may leave a private orphaned object; never
    overwrite it, retry blindly, or falsely confirm successful attestation.
    """
    _require_purchasing_employee(user)
    cid = user["company_id"]
    claim_rows = _read_purchasing_rows(
        db.table("purchasing_evidence_claims")
          .select("id,storage_object_key,claimed_sha256,verification_status")
          .eq("company_id", cid).eq("id", str(evidence_id)).limit(2)
    )
    if not claim_rows:
        raise HTTPException(status_code=404, detail="Evidence claim not found")
    if len(claim_rows) != 1 or claim_rows[0].get("verification_status") != "unverified":
        raise HTTPException(status_code=503, detail="Evidence claim invalid")
    claim = claim_rows[0]
    existing = _read_purchasing_rows(
        db.table("purchasing_evidence_integrity_checks")
          .select("id").eq("company_id", cid)
          .eq("evidence_id", str(evidence_id)).limit(2)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Evidence already has an integrity record")

    # Check actual DB user status before committing file bytes to storage.
    active = _read_purchasing_rows(
        db.table("users").select("id,role,is_active")
          .eq("company_id", cid).eq("id", user["sub"]).limit(2)
    )
    if len(active) != 1 or active[0].get("is_active") is not True or (
        active[0].get("role") not in ("owner", "manager", "back_office")
    ):
        raise HTTPException(status_code=403, detail="Evidence upload permission denied")

    inspected = await inspect_uploaded_evidence(
        file,
        expected_sha256=claim["claimed_sha256"],
        storage_key=claim["storage_object_key"],
        company_id=str(cid),
    )
    await store_quarantined_evidence(db, claim["storage_object_key"], inspected)
    response = _execute_purchasing_rpc(db, "attest_purchasing_evidence_integrity", {
        "p_company_id": cid,
        "p_evidence_id": str(evidence_id),
        "p_computed_sha256": inspected.digest,
        "p_byte_count": inspected.size,
        "p_detected_mime": inspected.mime,
        "p_actor": user["sub"],
    })
    return {
        **response,
        "bytes_received": inspected.size,
        "integrity_status": "sha256_matched_at_upload",
        "malware_scan_status": "not_scanned",
        "safe_to_view": False,
        "accounting_reconciled": False,
        "landed_cost_verified": False,
        "inventory_changed": False,
    }


@router.get("/evidence/{evidence_id}/integrity")
async def get_purchasing_evidence_integrity(
    evidence_id: UUID,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    _require_purchasing_employee(user)
    cid = user["company_id"]
    claim_rows = _read_purchasing_rows(
        db.table("purchasing_evidence_claims")
          .select("id").eq("company_id", cid)
          .eq("id", str(evidence_id)).limit(2)
    )
    if not claim_rows:
        raise HTTPException(status_code=404, detail="Evidence claim not found")
    records = _read_purchasing_rows(
        db.table("purchasing_evidence_integrity_checks")
          .select("id,computed_sha256,byte_count,detected_mime,"
                  "integrity_status,malware_scan_status,"
                  "financial_review_status,created_at")
          .eq("company_id", cid).eq("evidence_id", str(evidence_id))
          .limit(2)
    )
    if len(records) > 1:
        raise HTTPException(status_code=503, detail="Ambiguous integrity record")
    scans = _read_purchasing_rows(
        db.table("purchasing_evidence_malware_scans")
          .select("scan_status")
          .eq("company_id", cid).eq("evidence_id", str(evidence_id)).limit(2)
    )
    if len(scans) > 1 or (scans and scans[0].get("scan_status") not in ("clean", "infected")):
        raise HTTPException(status_code=503, detail="Invalid malware scan record")
    return {
        "evidence_id": str(evidence_id),
        "integrity": records[0] if records else None,
        "integrity_status": (
            records[0]["integrity_status"] if records else "not_checked"
        ),
        "malware_scan_status": (
            scans[0]["scan_status"] if scans else "not_scanned"
        ),
        "safe_to_view": False,
        "financial_review_status": "not_reviewed",
        "accounting_reconciled": False,
        "landed_cost_verified": False,
    }


@router.post("/orders/{po_number}/evidence", status_code=201)
async def record_purchasing_evidence_metadata(
    po_number: str,
    request: PurchasingEvidenceClaimRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Record unverified storage metadata, never file bytes or an AP expense."""
    _require_purchasing_employee(user)
    if (request.claimed_amount is None) != (request.currency is None):
        raise HTTPException(
            status_code=422,
            detail="Claimed amount and currency must be provided together",
        )
    return _execute_purchasing_rpc(db, "record_purchasing_evidence_claim", {
        "p_company_id": user["company_id"],
        "p_po_number": po_number,
        "p_evidence_key": request.evidence_key,
        "p_document_type": request.document_type,
        "p_storage_object_key": request.storage_object_key,
        "p_claimed_sha256": request.claimed_sha256,
        "p_claimed_amount": (
            str(request.claimed_amount)
            if request.claimed_amount is not None else None
        ),
        "p_currency": request.currency,
        "p_actor": user["sub"],
    })


@router.get("/orders/{po_number}/evidence")
async def list_purchasing_evidence_metadata(
    po_number: str,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Read cost claims grouped by currency without mistaking them for costs.

    Claimed SHA hashes are NOT proof that referenced bytes exist or match.
    No land-cost value or QB write should depend on an unverified claim.
    """
    _require_purchasing_employee(user)
    cid = user["company_id"]
    if not po_number or len(po_number) > 100:
        raise HTTPException(status_code=422, detail="Invalid PO number")
    orders = _read_purchasing_rows(
        db.table("purchasing_orders")
          .select("id,po_number,status")
          .eq("company_id", cid).eq("po_number", po_number).limit(2)
    )
    if not orders:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if len(orders) != 1:
        raise HTTPException(status_code=503, detail="Ambiguous purchase order")
    claims = _read_purchasing_rows(
        db.table("purchasing_evidence_claims")
          .select("id,evidence_key,document_type,storage_object_key,"
                  "claimed_sha256,claimed_amount,currency,"
                  "verification_status,submitted_by,created_at")
          .eq("company_id", cid).eq("po_id", orders[0]["id"])
          .order("created_at", desc=True).limit(501)
    )
    if len(claims) > 500:
        raise HTTPException(status_code=503, detail="Evidence list exceeds supported scope")
    totals: dict[str, Decimal] = {}
    try:
        for claim in claims:
            if claim.get("verification_status") != "unverified":
                raise ValueError("Unexpected evidence verification status")
            amount, currency = claim.get("claimed_amount"), claim.get("currency")
            if amount is None:
                if currency is not None:
                    raise ValueError("Currency without an amount")
                continue
            qty = Decimal(str(amount))
            if (not qty.is_finite() or qty < 0 or not isinstance(currency, str)
                    or len(currency) != 3 or not currency.isascii()
                    or not currency.isupper() or not currency.isalpha()):
                raise ValueError("Invalid evidence money")
            totals[currency] = totals.get(currency, Decimal("0")) + qty
    except (InvalidOperation, ValueError, TypeError) as exc:
        log.error("Evidence claims invalid for company %s (%s)", cid, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Purchasing evidence unavailable") from exc
    return {
        "po_number": po_number,
        "evidence_claims": claims,
        "claim_count": len(claims),
        "unverified_claim_totals_by_currency": {
            code: str(value) for code, value in sorted(totals.items())
        },
        "verification_status": "unverified",
        "landed_cost_verified": False,
        "accounting_reconciled": False,
        "inventory_changed": False,
        "evidence_status_message": (
            "Stored paths and digests are user-submitted claims; "
            "file existence, SHA-256, invoice validity, currency conversion "
            "and QuickBooks AP reconciliation have not been checked."
        ),
    }


@router.get("/orders/{po_number}")
async def get_durable_purchase_order_detail(
    po_number: str,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Trace a PO through approved lines, staged receipts and booked receipts.

    Each query is tenant-scoped, including all child records. Read timestamps
    can differ between queries during a concurrent post; this is not a DB
    transaction snapshot, and it does not certify QuickBooks reconciliation.
    """
    _require_purchasing_employee(user)
    if not po_number or len(po_number) > 100:
        raise HTTPException(status_code=422, detail="Invalid PO number")
    cid = user["company_id"]
    orders = _read_purchasing_rows(
        db.table("purchasing_orders")
          .select("id,po_number,supplier_ref,warehouse_id,status,"
                  "created_at,approved_at,created_by,approved_by")
          .eq("company_id", cid).eq("po_number", po_number).limit(2)
    )
    if not orders:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if len(orders) != 1:
        raise HTTPException(status_code=503, detail="Purchase order data ambiguous")
    order = orders[0]
    po_id = order["id"]
    lines = _read_purchasing_rows(
        db.table("purchasing_order_lines")
          .select("sku,qty_ordered,qty_received")
          .eq("company_id", cid).eq("po_id", po_id).limit(101)
    )
    staged = _read_purchasing_rows(
        db.table("staged_warehouse_receipts")
          .select("id,receipt_key,status,submitted_by,posted_receipt_id,"
                  "created_at,approved_at,payload")
          .eq("company_id", cid).eq("po_id", po_id)
          .order("created_at", desc=True).limit(501)
    )
    posted = _read_purchasing_rows(
        db.table("warehouse_receipt_documents")
          .select("id,receipt_key,received_by,approved_by,posted_at")
          .eq("company_id", cid).eq("po_id", po_id)
          .order("posted_at", desc=True).limit(501)
    )
    if len(lines) > 100 or len(staged) > 500 or len(posted) > 500:
        raise HTTPException(status_code=503, detail="Purchase record scope exceeds supported page")
    from decimal import Decimal, InvalidOperation
    try:
        ordered = sum((Decimal(str(x["qty_ordered"])) for x in lines), Decimal("0"))
        received = sum((Decimal(str(x["qty_received"])) for x in lines), Decimal("0"))
        if not lines or any(not Decimal(str(x["qty_ordered"])).is_finite() for x in lines):
            raise ValueError("Malformed purchase lines")
        if not ordered.is_finite() or not received.is_finite() or received < 0 or ordered < received:
            raise ValueError("Malformed purchase quantities")
    except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Purchase quantities unavailable") from exc
    return {
        "order": order, "lines": lines,
        "staged_receipts": staged, "posted_receipts": posted,
        "ordered_cases": str(ordered),
        "received_cases": str(received),
        "remaining_cases": str(ordered - received),
        "inventory_posted": bool(posted),
        "accounting_reconciled": False,
        "data_source": "secretaryai_purchasing_ledger",
        "read_consistency": "separate_tenant_scoped_queries",
    }


@router.get("/receipts/pending")
async def list_pending_warehouse_receipts(
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    # Approver identity is the JWT subject, and the actual approval SQL
    # verifies active role, tenant and separation of duties again.
    rows = _read_purchasing_rows(
        db.table("staged_warehouse_receipts")
          .select("id,po_id,receipt_key,payload,submitted_by,created_at")
          .eq("company_id", user["company_id"])
          .eq("status", "pending")
          .order("created_at", desc=False)
          .limit(100)
    )
    return {"pending_receipts": [
        {**x, "can_approve": str(x.get("submitted_by")) != str(user["sub"])}
        for x in rows
    ], "count": len(rows)}


@router.post("/orders", status_code=201)
async def create_durable_purchase_order(
    request: DurablePORequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    _require_purchasing_employee(user)
    return _execute_purchasing_rpc(db, "create_warehouse_purchase_order", {
        "p_company_id": user["company_id"],
        "p_po_number": request.po_number,
        "p_supplier_ref": request.supplier_ref,
        "p_warehouse_id": str(request.warehouse_id),
        "p_lines": [item.model_dump() for item in request.lines],
        "p_actor": user["sub"],
    })


@router.post("/orders/{po_number}/approve")
async def approve_durable_purchase_order(
    po_number: str,
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    return _execute_purchasing_rpc(db, "approve_warehouse_purchase_order", {
        "p_company_id": user["company_id"],
        "p_po_number": po_number,
        "p_actor": user["sub"],
    })


@router.post("/orders/{po_number}/receipts", status_code=201)
async def stage_durable_warehouse_receipt(
    po_number: str,
    request: DurableReceiptRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    _require_purchasing_employee(user)
    if any(not (x.accepted_qty or x.damaged_qty or x.quarantined_qty)
           for x in request.lines):
        raise HTTPException(status_code=422, detail="Each receipt line needs a received quantity")
    return _execute_purchasing_rpc(db, "stage_warehouse_receipt", {
        "p_company_id": user["company_id"],
        "p_po_number": po_number,
        "p_receipt_key": request.receipt_key,
        "p_lines": [item.model_dump() for item in request.lines],
        "p_actor": user["sub"],
    })


@router.post("/receipts/{staged_receipt_id}/approve")
async def approve_staged_receipt_and_post_inventory(
    staged_receipt_id: UUID,
    user: dict = Depends(require_permission("approve_actions")),
    db=Depends(get_db),
):
    """Second human approval, atomically books inspected units into stock."""
    return _execute_purchasing_rpc(db, "approve_staged_warehouse_receipt", {
        "p_company_id": user["company_id"],
        "p_staged_receipt_id": str(staged_receipt_id),
        "p_actor": user["sub"],
    })



# ─── Read-only stock versus QuickBooks comparison ────────────────────────────
# The in-app ledger starts from verified receipts; QuickBooks may include
# opening balances or warehouses that have not been onboarded. Their values
# MUST NOT be called reconciled until a separate coverage, scope, unit and
# item-mapping attestation exists. This endpoint never mutates either system.

@router.get("/reconciliation/stock/{sku}")
async def preview_stock_reconciliation(
    sku: str,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    _require_purchasing_employee(user)
    if not sku or len(sku) > 120:
        raise HTTPException(status_code=422, detail="Invalid SKU")
    company_id = user["company_id"]
    stock_rows = _read_purchasing_rows(
        db.table("stock_skus")
        .select("sku,product_name,base_unit,qb_item_id")
        .eq("company_id", company_id).eq("sku", sku).limit(2)
    )
    if not stock_rows:
        raise HTTPException(status_code=404, detail="Stock SKU not found")
    if len(stock_rows) > 1:
        raise HTTPException(status_code=503, detail="Ambiguous stock SKU record")
    item = stock_rows[0]
    balances = _read_purchasing_rows(
        db.table("stock_balances")
        .select("warehouse_id,lot_code,bucket,unit,quantity,updated_at")
        .eq("company_id", company_id).eq("sku", sku).limit(1001)
    )
    if len(balances) > 1000:
        raise HTTPException(status_code=503, detail="Stock ledger snapshot exceeds supported scope")

    from decimal import Decimal, InvalidOperation
    totals = {"available": Decimal("0"), "damaged": Decimal("0"),
              "quarantined": Decimal("0")}
    try:
        for row in balances:
            if row["bucket"] not in totals or row["unit"] != item["base_unit"]:
                raise ValueError("Unexpected ledger unit or bucket")
            qty = Decimal(str(row["quantity"]))
            if not qty.is_finite() or qty < 0:
                raise ValueError("Invalid ledger amount")
            totals[row["bucket"]] += qty
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        log.error("Stock-ledger consistency failure for tenant %s (%s)",
                  company_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Stock ledger snapshot invalid") from exc

    def _number(qty: Decimal) -> float:
        return float(qty)

    output = {
        "sku": item["sku"],
        "product_name": item["product_name"],
        "unit": item["base_unit"],
        "mapped_qb_item_id": item.get("qb_item_id"),
        "ledger_record_count": len(balances),
        "ledger_quantities": {k: _number(v) for k, v in totals.items()},
        "ledger_total": _number(sum(totals.values())),
        "latest_ledger_update": max(
            (str(row["updated_at"]) for row in balances
             if row.get("updated_at")), default=None
        ),
        "qb_quantity": None,
        "indicative_quantity_difference": None,
        "state": "unverified_scope",
        "reconciled": False,
        "inventory_posted_by_comparison": False,
        "accounting_reconciled": False,
        "scope_warning": (
            "Ledger includes only movements onboarded into SecretaryAI. "
            "QuickBooks may include other locations, opening balances or "
            "different sellable/damaged quantity semantics. Confirm scope, "
            "item mapping and units before interpreting a difference."
        ),
    }
    qb_id = str(item.get("qb_item_id") or "").strip()
    if not qb_id:
        output["state"] = "missing_explicit_qb_mapping"
        return output

    from app.api.deps import get_adapter
    # Resolve credentials from this tenant only; no global/shared QB client.
    adapter = get_adapter(user=user, db=db)
    try:
        qb_items = await adapter.get_inventory_qb()
    except Exception as exc:
        log.warning("QB reconciliation read failed for tenant %s: %s",
                    company_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="QuickBooks inventory unavailable") from exc
    matches = [row for row in qb_items if str(row.qb_id) == qb_id]
    if len(matches) != 1:
        output["state"] = (
            "qb_item_not_found" if not matches else "ambiguous_qb_mapping"
        )
        return output
    try:
        qb_qty = Decimal(str(matches[0].quantity_on_hand))
        if not qb_qty.is_finite():
            raise ValueError("Non-finite QuickBooks quantity")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Invalid QuickBooks quantity") from exc
    output["qb_quantity"] = _number(qb_qty)
    output["indicative_quantity_difference"] = _number(
        qb_qty - sum(totals.values())
    )
    # Even numerical equality is not proof of source parity.
    return output


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
        profile = SKUProfile(
            sku=item.sku,
            name=item.name,
            supplier_id=item.supplier_id,
            supplier_name=item.supplier_name or item.supplier_id,
            supply_chain_type=item.supply_chain_type,
            origin_country=item.origin_country,
            unit_cost_fob=item.unit_cost_fob,
            cases_per_pallet=item.cases_per_pallet,
            cases_per_40hc_container=item.cases_per_40hc_container,
            stock_on_hand=item.stock_on_hand,
            stock_committed=item.stock_committed,
            stock_in_transit=item.stock_in_transit,
            minimum_order_cases=item.minimum_order_cases,
        )
        sku_data.append({
            "profile": profile,
            "daily_sales_history": item.daily_sales,
            "lead_time_history": item.lead_time_days,
            "seasonal_indices": _weekly_seasonal_indices(item.seasonal_indices),
            "last_fob_price": item.last_fob_price,
            "current_fob_price": item.current_fob_price,
        })

    results = lm.evaluate_reorder_queue(sku_data)

    return {
        "status": "analysis_only",
        "source_data_verified": False,
        "inventory_snapshot_verified": False,
        "supplier_lead_history_verified": False,
        "po_creation_authorized": False,
        "requires_human_review": True,
        "count": len(results),
        "red_count": sum(1 for r in results if r["package"].urgency.value == "red"),
        "yellow_count": sum(1 for r in results if r["package"].urgency.value == "yellow"),
        "reorder_queue": [
            {
                "sku": r["package"].sku_profile.sku,
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
):
    """
    Fail closed until approved reorder proposals can be verified server-side.

    A client-provided `approved` flag is not a durable human approval. The
    separate /orders flow supports independently approved durable PO drafts;
    no unverified package may mint an order number or advance pipeline status.
    """
    raise HTTPException(
        status_code=409,
        detail=("Canonical human approval of a tenant-scoped reorder proposal "
                "is required before PO generation; use the separately "
                "approved durable purchasing workflow."),
    )


# ─── Stage 3 — Vendor Response Tracking ──────────────────────────────────────


@router.post("/vendor/parse-response")
async def parse_vendor_response(
    request: VendorResponseRequest,
    user: dict = Depends(require_permission("view_inventory")),
):
    """
    Preview a vendor email response without changing an order or pipeline state.

    The caller controls the subject/body, PO number and claimed supplier ID.
    Until Chat 2 supplies verified message identity and Chat 1 specifies the
    durable approval/event contract, these are *candidates*, not confirmations.
    """
    # The parser needs a PO object to read its line items. No authenticated PO
    # lines are available in this preview, so price/quantity changes cannot be
    # verified and no operational notification may be generated.
    po = PurchaseOrder(
        po_number=request.po_number,
        supplier_id=request.supplier_id,
        supplier_name=request.supplier_id,
        supplier_email="",
        ship_to_address="",
        payment_terms="",
        requested_ship_date="",
    )
    parsed = VendorResponseParser().parse(request.email_subject, request.email_body, po)

    return {
        "po_number": request.po_number,
        "status": "analysis_only",
        "candidate_confirmation": parsed.order_confirmed,
        "candidate_rejection": parsed.rejection,
        "possible_ship_date": parsed.ship_date,
        "possible_arrival_date": parsed.arrival_date,
        "possible_supplier_reference": parsed.supplier_ref or None,
        "issues_detected": parsed.issues,
        # With no authenticated PO lines, absence of a price/quantity mention
        # does not constitute a positive reconciliation result.
        "price_review_required": True,
        "quantity_review_required": True,
        "requires_human_review": True,
        "review_reason": (
            "Supplier sender and PO association are unverified; inspect the "
            "original message and independently approved PO before acting."
            + (f" Parser notes: {parsed.review_reason}" if parsed.review_reason else "")
        ),
        "message_verified": False,
        "po_link_verified": False,
        "supplier_action_permitted": False,
        "pipeline_status_changed": False,
        "inventory_posted": False,
    }


# ─── Stage 4 — Freight Cost Capture ──────────────────────────────────────────


@router.post("/freight/parse-invoice")
async def parse_freight_invoice(
    request: FreightInvoiceRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Parse caller-provided freight text into an unverified cost candidate.
    No invoice, booking or currency has been independently checked.
    """
    if re.search(r"[€£]|\b(?:EUR|GBP)\b", request.invoice_text, flags=re.IGNORECASE):
        raise HTTPException(
            status_code=422,
            detail="Non-USD invoice currency requires a reviewed FX conversion contract",
        )
    lm = _get_logistics_module(user, db)

    booking = FreightBooking(booking_ref=request.booking_id)
    updated = lm.capture_freight_invoice(booking, request.invoice_text, request.total_cases)
    summary = lm.freight_cost_summary(updated)

    return {
        "booking_id": request.booking_id,
        "status": "analysis_only",
        "invoice_verified": False,
        "booking_verified": False,
        "currency_verified": False,
        "accounting_posted": False,
        "pipeline_status_changed": False,
        "requires_human_review": True,
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
    Calculate a candidate customs hold from unverified caller observations.
    This does not confirm a hold, persist a milestone or fire an alert.
    """
    lm = _get_logistics_module(user, db)
    result = lm.log_customs_hold(
        booking_id=request.booking_id,
        hold_reason=request.hold_reason,
        days_at_port=request.days_at_port,
    )

    return {
        "booking_id": request.booking_id,
        "status": "analysis_only",
        "candidate_milestone": result["event"].milestone,
        "hold_reason": result["event"].hold_reason,
        "estimated_demurrage_accruing": result["event"].demurrage_accruing,
        "estimated_demurrage_total": result["event"].demurrage_total,
        "candidate_severity": result["alert"].severity,
        "customs_event_verified": False,
        "port_arrival_verified": False,
        "pipeline_status_changed": False,
        "requires_human_review": True,
        "alert": None,
    }


@router.post("/customs/demurrage-risk")
async def check_demurrage_risk(
    request: DemurrageRiskRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Estimate demurrage from caller dates, not carrier-confirmed port events.
    """
    evaluation_date = request.today or date.today()
    if request.arrival_date > evaluation_date:
        raise HTTPException(status_code=422, detail="Port arrival date cannot follow evaluation date")

    lm = _get_logistics_module(user, db)
    result = lm.check_demurrage_risk(
        booking_id=request.booking_id,
        arrival_date_str=request.arrival_date.isoformat(),
        today_str=evaluation_date.isoformat(),
    )

    return {
        **result["risk"],
        "status": "analysis_only",
        "port_arrival_verified": False,
        "invoiced_demurrage_verified": False,
        "pipeline_status_changed": False,
        "requires_human_review": True,
        "candidate_severity": result["risk"]["severity"],
        "alert": None,
    }


@router.post("/customs/parse-broker-email")
async def parse_broker_email(
    request: BrokerEmailRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Classify caller-provided broker text as a candidate milestone only.
    Sender identity, booking association and customs status are unverified.
    """
    lm = _get_logistics_module(user, db)
    result = lm.parse_broker_email(request.booking_id, request.subject, request.body)

    return {
        "booking_id": request.booking_id,
        "status": "analysis_only",
        "candidate_milestone": result["milestone"],
        "candidate_event": {
            "milestone": result["event"].milestone,
            "hold_reason": result["event"].hold_reason,
            "estimated_demurrage_accruing": result["event"].demurrage_accruing,
        } if result["event"] else None,
        "message_verified": False,
        "booking_verified": False,
        "customs_event_verified": False,
        "requires_human_review": True,
        "pipeline_status_changed": False,
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

    booking = FreightBooking(booking_ref=request.booking_id)
    result = lm.process_warehouse_receipt(
        booking=booking,
        po_number=request.po_number,
        line_items_received=[line.model_dump(exclude={"unit"}) for line in request.line_items],
        received_by=request.received_by,
        notes=request.notes,
        po_sent_at=request.po_sent_at,
        supplier_ship_date=request.supplier_ship_date,
    )

    receipt = result["receipt"]
    # IMPORTANT: this legacy stage computes exceptions and lead times only.
    # It does NOT persist an authoritative receiving document or book stock.
    # Do not promote it to a completed warehouse-received state until the
    # approved PO, inspected receipt and idempotent stock posting are wired.
    # Analysis is deliberately not persisted as a received stock event.
    # The separate durable receipt approval endpoint is authoritative.
    return {
        "po_number": request.po_number,
        "status": "analysis_only",
        "inventory_posted": False,
        "receipt_verified": False,
        "po_verified": False,
        "booking_verified": False,
        "received_by_verified": False,
        "supplier_lead_time_verified": False,
        "accounting_reconciled": False,
        "requires_verified_receipt": True,
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
        "alert": None,
        "suggested_alert": (
            {**_serialize_alert(result["alert"]), "requires_human_review": True}
            if result["alert"] else None
        ),
    }


# ─── Stage 8 — Cost Reconciliation ───────────────────────────────────────────


@router.post("/cost/reconcile")
async def reconcile_costs(
    request: CostReconcileRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Preview caller-supplied costs; never certify invoices or advance a PO.
    """
    if request.current.sku != request.previous.sku:
        raise HTTPException(status_code=422, detail="Cost comparison requires the same SKU")
    lm = _get_logistics_module(user, db)

    def _dict_to_lcd(d: LandedCostComparisonInput) -> LandedCostBreakdown:
        values = d.model_dump()
        return LandedCostBreakdown(
            **values,
        )

    current = _dict_to_lcd(request.current)
    previous = _dict_to_lcd(request.previous)

    result = lm.reconcile_costs(current, previous, request.selling_price_per_case)

    return {
        "status": "analysis_only",
        "landed_cost_verified": False,
        "accounting_reconciled": False,
        "pipeline_status_changed": False,
        "requires_independent_invoice_review": True,
        "source": "caller_supplied_unverified_components",
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
        "alerts": [],
        "suggested_alerts": [
            {**_serialize_alert(a), "requires_human_review": True}
            for a in result["alerts"]
        ],
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

    # The builder takes the gap-free daily quantities oldest first (validated
    # above); passing (date, units) pairs made every full-history request fail.
    first_day = request.daily_sales[0].date
    day_count = len(request.daily_sales)
    built = lm.build_seasonal_indices(request.sku, [d.units for d in request.daily_sales])
    quality = built.get("status")
    raw = built.get("indices")
    # Insufficient or all-zero history yields placeholder 1.0 multipliers from
    # the builder; never present those as a learned seasonal pattern.
    learned = (
        quality in ("provisional", "confirmatory", "confident")
        and isinstance(raw, list) and len(raw) == 52
    )
    indices = list(raw) if learned else []
    return {
        "sku": request.sku,
        "status": "analysis_only",
        "modeled_data_quality": quality,
        "sales_history_verified": False,
        "historical_learning_persisted": False,
        "days_available": day_count,
        "min_days_needed": 182,
        "data_months": round(day_count / 30.44, 1),
        "index_count": len(indices),
        "indices": indices,
        # Weeks are counted from the first sales date, not calendar week 1.
        "week_alignment": "counted_from_first_sales_date",
        "week_start_dates": [
            (first_day + timedelta(days=7 * week)).isoformat()
            for week in range(len(indices))
        ],
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
    # Per-request module has no durable source history: empty is not success.
    return {
        **report,
        "status": "insufficient_verified_history",
        "source_history_verified": False,
        "supplier_learning_persisted": False,
        "accounting_reconciled": False,
        "recommendation": (
            "No source-verified supplier lead times, costs or shipment history "
            "were loaded. Connect and reconcile durable observations before analysis."
        ),
    }


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
    # Same audience as the other purchasing reads: sales reps hold
    # view_inventory but must not read purchase-order pipelines.
    _require_purchasing_employee(user)
    if not po_number or len(po_number) > 100:
        raise HTTPException(status_code=422, detail="Invalid PO number")
    try:
        # postgrest maybe_single() returns None (not an empty response) when
        # no row matches; a missing cycle is a 404, never a server error.
        result = db.table("procurement_cycles") \
            .select("*") \
            .eq("company_id", user["company_id"]) \
            .eq("po_number", po_number) \
            .maybe_single().execute()
    except Exception as exc:
        log.error("Procurement cycle read unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Procurement cycle records unavailable") from exc

    cycle_data = getattr(result, "data", None) if result is not None else None
    if not cycle_data:
        raise HTTPException(status_code=404, detail="Procurement cycle not found")
    if not isinstance(cycle_data, dict):
        raise HTTPException(status_code=503, detail="Procurement cycle records unavailable")
    return {
        "po_number": cycle_data.get("po_number"),
        "current_stage": cycle_data.get("current_stage"),
        "state_source": "legacy_procurement_cycles_unverified",
        "stage_verified": False,
        "authoritative_purchasing_state": False,
        "verified_po_receipt_ledger_link": False,
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


# Owned warehouse preview router. The shared application loader already mounts
# logistics.router, so no cross-team route-registration edits are necessary.
from app.api import warehouse as warehouse_preview_routes

router.include_router(warehouse_preview_routes.router, prefix="/warehouse", tags=["warehouse"])

# Return reviews are preview-only until canonical tenant stock/credit approvals.
from app.api import returns as returns_preview_routes

router.include_router(returns_preview_routes.router, prefix="/returns", tags=["returns"])
