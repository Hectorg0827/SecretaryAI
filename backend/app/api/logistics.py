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
from typing import Literal


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
    return {
        "evidence_id": str(evidence_id),
        "integrity": records[0] if records else None,
        "integrity_status": (
            records[0]["integrity_status"] if records else "not_checked"
        ),
        "malware_scan_status": "not_scanned",
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
    )

    result = lm.parse_vendor_response(po, request.email_subject, request.email_body)
    parsed = result["parsed"]

    _upsert_cycle(
        db, user["company_id"],
        po_number=request.po_number,
        stage=3, stage_name="vendor_response_received",
        supplier_id=request.supplier_id,
    )

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

    booking = FreightBooking(booking_ref=request.booking_id)
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

    booking = FreightBooking(booking_ref=request.booking_id)
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
