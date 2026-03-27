"""
Compliance Engine API.

Exposes the alcohol beverage compliance engine as REST endpoints.
All endpoints require authentication; write endpoints require elevated permissions.

Base path: /api/compliance
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_db
from app.auth.rbac import require_permission
from app.intelligence.compliance import ComplianceModule, ComplianceConfig
from app.intelligence.compliance.models import (
    AlcoholProduct,
    BrandRegistration,
    BrandRegStatus,
    COLARecord,
    DistributorRelationship,
    FederalPermit,
    FederalPermitType,
    LicenseStatus,
    ProductType,
    StateLicense,
    TerminationRestriction,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ─── Module factory ────────────────────────────────────────────────────────────


def _get_compliance_module(user: dict, db) -> ComplianceModule:
    """
    Instantiate a ComplianceModule for the current user's company.
    Config and entity registry are hydrated from the database.
    """
    company_id = user["company_id"]
    result = db.table("companies").select("*").eq("id", company_id).execute()
    company = result.data[0] if result.data else {}

    active_states_raw = company.get("compliance_active_states", "")
    active_states = [s.strip() for s in active_states_raw.split(",") if s.strip()] if active_states_raw else []

    config = ComplianceConfig(
        active_states=active_states,
        company_name=company.get("name", ""),
    )

    module = ComplianceModule(company_id=company_id, config=config)

    # Load persisted entities from DB into the in-memory registry
    _load_registry_from_db(module, company_id, db)

    return module


def _load_registry_from_db(module: ComplianceModule, company_id: str, db) -> None:
    """
    Hydrate the module's in-memory registry from Supabase DB tables.
    Uses try/except to handle missing tables gracefully.
    """

    # State licenses
    try:
        res = db.table("compliance_licenses").select("*").eq("company_id", company_id).execute()
        for row in (res.data or []):
            try:
                product_types = [ProductType(pt) for pt in (row.get("product_types") or [])]
                module.add_state_license(StateLicense(
                    id=row.get("id", ""),
                    state_code=row.get("state_code", ""),
                    license_type=row.get("license_type", ""),
                    product_types=product_types,
                    license_number=row.get("license_number", ""),
                    issue_date=_parse_date(row.get("issue_date")),
                    expiration_date=_parse_date(row.get("expiration_date")),
                    renewal_window_days=int(row.get("renewal_window_days") or 90),
                    annual_fee=float(row.get("annual_fee") or 0.0),
                    status=LicenseStatus(row.get("status", "active")),
                    notes=row.get("notes", ""),
                ))
            except Exception as exc:
                log.warning("Failed to load license row: %s", exc)
    except Exception as exc:
        log.debug("compliance_licenses table not available: %s", exc)

    # Products
    try:
        res = db.table("compliance_products").select("*").eq("company_id", company_id).execute()
        for row in (res.data or []):
            try:
                module.add_product(AlcoholProduct(
                    id=row.get("id", ""),
                    sku=row.get("sku", ""),
                    name=row.get("name", ""),
                    product_type=ProductType(row.get("product_type", "wine")),
                    abv_pct=float(row.get("abv_pct") or 0.0),
                    container_size_ml=float(row.get("container_size_ml") or 750.0),
                    cases_per_container=int(row.get("cases_per_container") or 56),
                    unit_cost_fob=float(row.get("unit_cost_fob") or 0.0),
                    country_of_origin=row.get("country_of_origin", ""),
                    foreign_producer_id=row.get("foreign_producer_id"),
                    requires_formula_approval=bool(row.get("requires_formula_approval", False)),
                ))
            except Exception as exc:
                log.warning("Failed to load product row: %s", exc)
    except Exception as exc:
        log.debug("compliance_products table not available: %s", exc)

    # Brand registrations
    try:
        res = db.table("compliance_brand_registrations").select("*").eq("company_id", company_id).execute()
        for row in (res.data or []):
            try:
                module.add_brand_registration(BrandRegistration(
                    id=row.get("id", ""),
                    product_id=row.get("product_id", ""),
                    state_code=row.get("state_code", ""),
                    registration_number=row.get("registration_number", ""),
                    registration_date=_parse_date(row.get("registration_date")),
                    expiration_date=_parse_date(row.get("expiration_date")),
                    registration_fee=float(row.get("registration_fee") or 0.0),
                    status=BrandRegStatus(row.get("status", "active")),
                    state_label_approval_number=row.get("state_label_approval_number", ""),
                ))
            except Exception as exc:
                log.warning("Failed to load brand registration row: %s", exc)
    except Exception as exc:
        log.debug("compliance_brand_registrations table not available: %s", exc)

    # Federal permits
    try:
        res = db.table("compliance_federal_permits").select("*").eq("company_id", company_id).execute()
        for row in (res.data or []):
            try:
                module.add_federal_permit(FederalPermit(
                    id=row.get("id", ""),
                    permit_type=FederalPermitType(row.get("permit_type", "importer")),
                    permit_number=row.get("permit_number", ""),
                    issue_date=_parse_date(row.get("issue_date")),
                    expiration_date=_parse_date(row.get("expiration_date")),
                    status=LicenseStatus(row.get("status", "active")),
                    notes=row.get("notes", ""),
                ))
            except Exception as exc:
                log.warning("Failed to load federal permit row: %s", exc)
    except Exception as exc:
        log.debug("compliance_federal_permits table not available: %s", exc)

    # Distributors
    try:
        res = db.table("compliance_distributors").select("*").eq("company_id", company_id).execute()
        for row in (res.data or []):
            try:
                product_types = [ProductType(pt) for pt in (row.get("product_types") or [])]
                module.add_distributor(DistributorRelationship(
                    id=row.get("id", ""),
                    state_code=row.get("state_code", ""),
                    distributor_name=row.get("distributor_name", ""),
                    territory=row.get("territory", "Statewide"),
                    product_types=product_types,
                    contract_start_date=_parse_date(row.get("contract_start_date")),
                    contract_end_date=_parse_date(row.get("contract_end_date")),
                    franchise_law_attached=bool(row.get("franchise_law_attached", False)),
                    franchise_attachment_date=_parse_date(row.get("franchise_attachment_date")),
                    termination_restriction=TerminationRestriction(
                        row.get("termination_restriction", "none")
                    ),
                    contract_document_ref=row.get("contract_document_ref", ""),
                    notes=row.get("notes", ""),
                ))
            except Exception as exc:
                log.warning("Failed to load distributor row: %s", exc)
    except Exception as exc:
        log.debug("compliance_distributors table not available: %s", exc)

    # COLAs
    try:
        res = db.table("compliance_colas").select("*").eq("company_id", company_id).execute()
        for row in (res.data or []):
            try:
                module.add_cola(COLARecord(
                    id=row.get("id", ""),
                    product_id=row.get("product_id", ""),
                    cola_number=row.get("cola_number", ""),
                    product_type=ProductType(row.get("product_type", "wine")),
                    issue_date=_parse_date(row.get("issue_date")),
                    expiration_date=_parse_date(row.get("expiration_date")),
                    status=LicenseStatus(row.get("status", "active")),
                    formula_approved=bool(row.get("formula_approved", False)),
                    lab_analysis_on_file=bool(row.get("lab_analysis_on_file", False)),
                ))
            except Exception as exc:
                log.warning("Failed to load COLA row: %s", exc)
    except Exception as exc:
        log.debug("compliance_colas table not available: %s", exc)


# ─── Serialization helpers ─────────────────────────────────────────────────────


def _parse_date(value) -> Optional[date]:
    """Parse a date string or return None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _serialize_date(d) -> Optional[str]:
    """Convert date to ISO string, or None."""
    if d is None:
        return None
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _serialize_dataclass(obj) -> dict:
    """Convert a dataclass to a JSON-serializable dict."""
    raw = dataclasses.asdict(obj)
    return _convert_dates(raw)


def _convert_dates(obj: Any) -> Any:
    """Recursively convert date objects to ISO strings."""
    if isinstance(obj, dict):
        return {k: _convert_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_dates(item) for item in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _serialize_check_result(result) -> dict:
    """Serialize a ComplianceCheckResult to a dict."""
    return {
        "approved": result.approved,
        "product_id": result.product_id,
        "state_code": result.state_code,
        "quantity_cases": result.quantity_cases,
        "issues": [
            {
                "severity": issue.severity.value,
                "issue": issue.issue,
                "action": issue.action,
                "fee": issue.fee,
                "state_code": issue.state_code,
                "category": issue.category,
            }
            for issue in result.issues
        ],
        "blockers_count": len(result.blockers),
        "warnings_count": len(result.warnings),
        "fees": result.fees,
        "total_compliance_cost": result.total_compliance_cost,
        "checked_at": result.checked_at.isoformat(),
    }


def _serialize_alert(alert) -> dict:
    """Serialize a ComplianceAlert to a dict."""
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "priority": alert.priority.value,
        "state_code": alert.state_code,
        "item_name": alert.item_name,
        "item_id": alert.item_id,
        "days_until": alert.days_until,
        "expiry_date": _serialize_date(alert.expiry_date),
        "message": alert.message,
        "action_required": alert.action_required,
        "renewal_url": alert.renewal_url,
        "estimated_fee": alert.estimated_fee,
        "created_at": alert.created_at.isoformat(),
    }


def _serialize_deadline(dl) -> dict:
    """Serialize a ComplianceDeadline to a dict."""
    return {
        "deadline_type": dl.deadline_type.value,
        "state_code": dl.state_code,
        "due_date": _serialize_date(dl.due_date),
        "frequency": dl.frequency,
        "action": dl.action,
        "notes": dl.notes,
    }


def _serialize_digest(digest) -> dict:
    """Serialize a DailyDigest to a dict."""
    return {
        "date": _serialize_date(digest.date),
        "critical_alerts": [_serialize_alert(a) for a in digest.critical_alerts],
        "warning_alerts": [_serialize_alert(a) for a in digest.warning_alerts],
        "upcoming_deadlines": [_serialize_deadline(d) for d in digest.upcoming_deadlines],
        "state_status": digest.state_status,
        "cost_estimate": _serialize_dataclass(digest.cost_estimate_q),
    }


# ─── Request body models ───────────────────────────────────────────────────────


class CheckShipmentRequest(BaseModel):
    product_id: str
    state_code: str
    quantity_cases: int = 1


class AddProductRequest(BaseModel):
    id: Optional[str] = None
    sku: str
    name: str
    product_type: str = "wine"
    abv_pct: float = 0.0
    container_size_ml: float = 750.0
    cases_per_container: int = 56
    unit_cost_fob: float = 0.0
    country_of_origin: str = ""
    foreign_producer_id: Optional[str] = None
    requires_formula_approval: bool = False


class AddStateLicenseRequest(BaseModel):
    id: Optional[str] = None
    state_code: str
    license_type: str
    product_types: List[str] = Field(default_factory=list)
    license_number: str
    issue_date: Optional[str] = None
    expiration_date: Optional[str] = None
    renewal_window_days: int = 90
    annual_fee: float = 0.0
    status: str = "active"
    notes: str = ""


class AddBrandRegistrationRequest(BaseModel):
    id: Optional[str] = None
    product_id: str
    state_code: str
    registration_number: str = ""
    registration_date: Optional[str] = None
    expiration_date: Optional[str] = None
    registration_fee: float = 0.0
    status: str = "active"
    state_label_approval_number: str = ""


class AddFederalPermitRequest(BaseModel):
    id: Optional[str] = None
    permit_type: str = "importer"
    permit_number: str
    issue_date: Optional[str] = None
    expiration_date: Optional[str] = None
    status: str = "active"
    notes: str = ""


class AddDistributorRequest(BaseModel):
    id: Optional[str] = None
    state_code: str
    distributor_name: str
    territory: str = "Statewide"
    product_types: List[str] = Field(default_factory=list)
    contract_start_date: Optional[str] = None
    contract_end_date: Optional[str] = None
    franchise_law_attached: bool = False
    franchise_attachment_date: Optional[str] = None
    termination_restriction: str = "none"
    contract_document_ref: str = ""
    notes: str = ""


class AddCOLARequest(BaseModel):
    id: Optional[str] = None
    product_id: str
    cola_number: str
    product_type: str = "wine"
    issue_date: Optional[str] = None
    expiration_date: Optional[str] = None
    status: str = "active"
    formula_approved: bool = False
    lab_analysis_on_file: bool = False


# ─── Status & describe ─────────────────────────────────────────────────────────


@router.get("/status")
async def get_compliance_status(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Overall compliance health for the company dashboard."""
    module = _get_compliance_module(user, db)
    return module.get_overall_status()


@router.get("/describe")
async def describe_compliance_module(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Return the module's capabilities and current config."""
    module = _get_compliance_module(user, db)
    return module.describe()


# ─── Alerts & deadlines ────────────────────────────────────────────────────────


@router.get("/alerts")
async def get_compliance_alerts(
    days: int = Query(default=90, ge=1, le=730),
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Return expiration alerts for the next `days` days."""
    module = _get_compliance_module(user, db)
    alerts = module.get_alerts(lookahead_days=days)
    return {
        "count": len(alerts),
        "critical_count": sum(1 for a in alerts if a.priority.value == "critical"),
        "warning_count": sum(1 for a in alerts if a.priority.value == "warning"),
        "alerts": [_serialize_alert(a) for a in alerts],
    }


@router.get("/deadlines")
async def get_compliance_deadlines(
    days: int = Query(default=30, ge=1, le=365),
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Return upcoming filing/reporting deadlines."""
    module = _get_compliance_module(user, db)
    deadlines = module.get_deadlines(days_ahead=days)
    return {
        "count": len(deadlines),
        "deadlines": [_serialize_deadline(d) for d in deadlines],
    }


@router.get("/digest")
async def get_daily_digest(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Generate and return the daily compliance digest."""
    module = _get_compliance_module(user, db)
    digest = module.get_daily_digest()
    return _serialize_digest(digest)


# ─── Shipment checks ──────────────────────────────────────────────────────────


@router.post("/check-shipment")
async def check_shipment(
    request: CheckShipmentRequest,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Pre-shipment compliance check for a single product/state.
    Returns approved flag, issues, and fee breakdown.
    """
    module = _get_compliance_module(user, db)
    result = module.check_shipment(
        product_id=request.product_id,
        state_code=request.state_code,
        quantity_cases=request.quantity_cases,
    )

    # Log audit trail
    try:
        db.table("compliance_check_log").insert({
            "company_id": user["company_id"],
            "product_id": request.product_id,
            "state_code": request.state_code,
            "quantity_cases": request.quantity_cases,
            "approved": result.approved,
            "issues_count": len(result.issues),
            "blockers_count": len(result.blockers),
            "total_fees": result.total_compliance_cost,
        }).execute()
    except Exception as exc:
        log.debug("Could not write compliance_check_log: %s", exc)

    return _serialize_check_result(result)


@router.post("/check-shipment-batch")
async def check_shipment_batch(
    shipments: List[CheckShipmentRequest],
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Pre-shipment compliance check for multiple shipments at once.
    """
    module = _get_compliance_module(user, db)
    raw_shipments = [
        {"product_id": s.product_id, "state_code": s.state_code, "quantity_cases": s.quantity_cases}
        for s in shipments
    ]
    results = module.check_shipment_batch(raw_shipments)
    serialized = [_serialize_check_result(r) for r in results]
    return {
        "count": len(serialized),
        "approved_count": sum(1 for r in results if r.approved),
        "blocked_count": sum(1 for r in results if not r.approved),
        "results": serialized,
    }


# ─── State rules ──────────────────────────────────────────────────────────────


@router.get("/states")
async def list_states(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all 51 states/DC from the compliance matrix."""
    module = _get_compliance_module(user, db)
    states = module.list_all_states()
    return {"count": len(states), "states": states}


@router.get("/states/{state_code}")
async def get_state_rules(
    state_code: str,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Return the full compliance rules row for a specific state."""
    module = _get_compliance_module(user, db)
    rules = module.get_state_rules(state_code.upper())
    if rules is None:
        raise HTTPException(status_code=404, detail=f"State '{state_code}' not found in matrix")
    return rules


# ─── Fee estimate ─────────────────────────────────────────────────────────────


@router.get("/fee-estimate")
async def get_fee_estimate(
    states: str = Query(default="", description="Comma-separated state codes, e.g. NY,CA,TX"),
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Estimate annual compliance costs for one or more states.
    If `states` is empty, uses the company's active_states config.
    """
    module = _get_compliance_module(user, db)
    state_list = [s.strip().upper() for s in states.split(",") if s.strip()] if states else None
    estimate = module.estimate_annual_costs(states_active=state_list)
    return estimate


# ─── Distributor franchise risk ───────────────────────────────────────────────


@router.get("/distributor-risk/{state_code}")
async def get_distributor_risk(
    state_code: str,
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """Return franchise law risk summary for a state before appointing a distributor."""
    module = _get_compliance_module(user, db)
    return module.check_distributor_risk(state_code.upper())


# ─── Products ─────────────────────────────────────────────────────────────────


@router.get("/products")
async def list_products(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all products in the compliance registry."""
    module = _get_compliance_module(user, db)
    products = module.list_products()
    return {
        "count": len(products),
        "products": [_serialize_dataclass(p) for p in products],
    }


@router.post("/products")
async def add_product(
    request: AddProductRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """Add a product to the compliance registry and persist to DB."""
    company_id = user["company_id"]

    kwargs: dict = {
        "sku": request.sku,
        "name": request.name,
        "product_type": ProductType(request.product_type),
        "abv_pct": request.abv_pct,
        "container_size_ml": request.container_size_ml,
        "cases_per_container": request.cases_per_container,
        "unit_cost_fob": request.unit_cost_fob,
        "country_of_origin": request.country_of_origin,
        "foreign_producer_id": request.foreign_producer_id,
        "requires_formula_approval": request.requires_formula_approval,
    }
    if request.id:
        kwargs["id"] = request.id

    product = AlcoholProduct(**kwargs)

    try:
        db.table("compliance_products").upsert({
            "id": product.id,
            "company_id": company_id,
            "sku": product.sku,
            "name": product.name,
            "product_type": product.product_type.value,
            "abv_pct": product.abv_pct,
            "container_size_ml": product.container_size_ml,
            "cases_per_container": product.cases_per_container,
            "unit_cost_fob": product.unit_cost_fob,
            "country_of_origin": product.country_of_origin,
            "foreign_producer_id": product.foreign_producer_id,
            "requires_formula_approval": product.requires_formula_approval,
        }).execute()
    except Exception as exc:
        log.warning("Could not persist product to DB: %s", exc)

    return _serialize_dataclass(product)


# ─── State licenses ───────────────────────────────────────────────────────────


@router.get("/licenses")
async def list_licenses(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all state licenses in the compliance registry."""
    module = _get_compliance_module(user, db)
    licenses = module.list_state_licenses()
    return {
        "count": len(licenses),
        "licenses": [_serialize_dataclass(lic) for lic in licenses],
    }


@router.post("/licenses")
async def add_license(
    request: AddStateLicenseRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """Add a state license and persist to DB."""
    company_id = user["company_id"]

    kwargs: dict = {
        "state_code": request.state_code,
        "license_type": request.license_type,
        "product_types": [ProductType(pt) for pt in request.product_types],
        "license_number": request.license_number,
        "issue_date": _parse_date(request.issue_date),
        "expiration_date": _parse_date(request.expiration_date),
        "renewal_window_days": request.renewal_window_days,
        "annual_fee": request.annual_fee,
        "status": LicenseStatus(request.status),
        "notes": request.notes,
    }
    if request.id:
        kwargs["id"] = request.id

    license_ = StateLicense(**kwargs)

    try:
        db.table("compliance_licenses").upsert({
            "id": license_.id,
            "company_id": company_id,
            "state_code": license_.state_code,
            "license_type": license_.license_type,
            "product_types": [pt.value for pt in license_.product_types],
            "license_number": license_.license_number,
            "issue_date": _serialize_date(license_.issue_date),
            "expiration_date": _serialize_date(license_.expiration_date),
            "renewal_window_days": license_.renewal_window_days,
            "annual_fee": license_.annual_fee,
            "status": license_.status.value,
            "notes": license_.notes,
        }).execute()
    except Exception as exc:
        log.warning("Could not persist license to DB: %s", exc)

    return _serialize_dataclass(license_)


# ─── Brand registrations ──────────────────────────────────────────────────────


@router.get("/brand-registrations")
async def list_brand_registrations(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all brand registrations in the compliance registry."""
    module = _get_compliance_module(user, db)
    regs = module.list_brand_registrations()
    return {
        "count": len(regs),
        "brand_registrations": [_serialize_dataclass(r) for r in regs],
    }


@router.post("/brand-registrations")
async def add_brand_registration(
    request: AddBrandRegistrationRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """Add a brand registration and persist to DB."""
    company_id = user["company_id"]

    kwargs: dict = {
        "product_id": request.product_id,
        "state_code": request.state_code,
        "registration_number": request.registration_number,
        "registration_date": _parse_date(request.registration_date),
        "expiration_date": _parse_date(request.expiration_date),
        "registration_fee": request.registration_fee,
        "status": BrandRegStatus(request.status),
        "state_label_approval_number": request.state_label_approval_number,
    }
    if request.id:
        kwargs["id"] = request.id

    reg = BrandRegistration(**kwargs)

    try:
        db.table("compliance_brand_registrations").upsert({
            "id": reg.id,
            "company_id": company_id,
            "product_id": reg.product_id,
            "state_code": reg.state_code,
            "registration_number": reg.registration_number,
            "registration_date": _serialize_date(reg.registration_date),
            "expiration_date": _serialize_date(reg.expiration_date),
            "registration_fee": reg.registration_fee,
            "status": reg.status.value,
            "state_label_approval_number": reg.state_label_approval_number,
        }).execute()
    except Exception as exc:
        log.warning("Could not persist brand registration to DB: %s", exc)

    return _serialize_dataclass(reg)


# ─── Federal permits ──────────────────────────────────────────────────────────


@router.get("/federal-permits")
async def list_federal_permits(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all federal permits in the compliance registry."""
    module = _get_compliance_module(user, db)
    permits = module.list_federal_permits()
    return {
        "count": len(permits),
        "federal_permits": [_serialize_dataclass(p) for p in permits],
    }


@router.post("/federal-permits")
async def add_federal_permit(
    request: AddFederalPermitRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """Add a federal permit and persist to DB."""
    company_id = user["company_id"]

    kwargs: dict = {
        "permit_type": FederalPermitType(request.permit_type),
        "permit_number": request.permit_number,
        "issue_date": _parse_date(request.issue_date),
        "expiration_date": _parse_date(request.expiration_date),
        "status": LicenseStatus(request.status),
        "notes": request.notes,
    }
    if request.id:
        kwargs["id"] = request.id

    permit = FederalPermit(**kwargs)

    try:
        db.table("compliance_federal_permits").upsert({
            "id": permit.id,
            "company_id": company_id,
            "permit_type": permit.permit_type.value,
            "permit_number": permit.permit_number,
            "issue_date": _serialize_date(permit.issue_date),
            "expiration_date": _serialize_date(permit.expiration_date),
            "status": permit.status.value,
            "notes": permit.notes,
        }).execute()
    except Exception as exc:
        log.warning("Could not persist federal permit to DB: %s", exc)

    return _serialize_dataclass(permit)


# ─── Distributors ─────────────────────────────────────────────────────────────


@router.get("/distributors")
async def list_distributors(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all distributor relationships in the compliance registry."""
    module = _get_compliance_module(user, db)
    distributors = module.list_distributors()
    return {
        "count": len(distributors),
        "distributors": [_serialize_dataclass(d) for d in distributors],
    }


@router.post("/distributors")
async def add_distributor(
    request: AddDistributorRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """Add a distributor relationship and persist to DB."""
    company_id = user["company_id"]

    kwargs: dict = {
        "state_code": request.state_code,
        "distributor_name": request.distributor_name,
        "territory": request.territory,
        "product_types": [ProductType(pt) for pt in request.product_types],
        "contract_start_date": _parse_date(request.contract_start_date),
        "contract_end_date": _parse_date(request.contract_end_date),
        "franchise_law_attached": request.franchise_law_attached,
        "franchise_attachment_date": _parse_date(request.franchise_attachment_date),
        "termination_restriction": TerminationRestriction(request.termination_restriction),
        "contract_document_ref": request.contract_document_ref,
        "notes": request.notes,
    }
    if request.id:
        kwargs["id"] = request.id

    dist = DistributorRelationship(**kwargs)

    try:
        db.table("compliance_distributors").upsert({
            "id": dist.id,
            "company_id": company_id,
            "state_code": dist.state_code,
            "distributor_name": dist.distributor_name,
            "territory": dist.territory,
            "product_types": [pt.value for pt in dist.product_types],
            "contract_start_date": _serialize_date(dist.contract_start_date),
            "contract_end_date": _serialize_date(dist.contract_end_date),
            "franchise_law_attached": dist.franchise_law_attached,
            "franchise_attachment_date": _serialize_date(dist.franchise_attachment_date),
            "termination_restriction": dist.termination_restriction.value,
            "contract_document_ref": dist.contract_document_ref,
            "notes": dist.notes,
        }).execute()
    except Exception as exc:
        log.warning("Could not persist distributor to DB: %s", exc)

    return _serialize_dataclass(dist)


# ─── COLAs ────────────────────────────────────────────────────────────────────


@router.get("/colas")
async def list_colas(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """List all COLA records in the compliance registry."""
    module = _get_compliance_module(user, db)
    colas = module.list_colas()
    return {
        "count": len(colas),
        "colas": [_serialize_dataclass(c) for c in colas],
    }


@router.post("/colas")
async def add_cola(
    request: AddCOLARequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """Add a COLA record and persist to DB."""
    company_id = user["company_id"]

    kwargs: dict = {
        "product_id": request.product_id,
        "cola_number": request.cola_number,
        "product_type": ProductType(request.product_type),
        "issue_date": _parse_date(request.issue_date),
        "expiration_date": _parse_date(request.expiration_date),
        "status": LicenseStatus(request.status),
        "formula_approved": request.formula_approved,
        "lab_analysis_on_file": request.lab_analysis_on_file,
    }
    if request.id:
        kwargs["id"] = request.id

    cola = COLARecord(**kwargs)

    try:
        db.table("compliance_colas").upsert({
            "id": cola.id,
            "company_id": company_id,
            "product_id": cola.product_id,
            "cola_number": cola.cola_number,
            "product_type": cola.product_type.value,
            "issue_date": _serialize_date(cola.issue_date),
            "expiration_date": _serialize_date(cola.expiration_date),
            "status": cola.status.value,
            "formula_approved": cola.formula_approved,
            "lab_analysis_on_file": cola.lab_analysis_on_file,
        }).execute()
    except Exception as exc:
        log.warning("Could not persist COLA to DB: %s", exc)

    return _serialize_dataclass(cola)


# ─── DELETE endpoints ──────────────────────────────────────────────────────────


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: str,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    db.table("compliance_products").delete().eq("id", product_id).eq("company_id", company_id).execute()
    return {"deleted": True, "id": product_id}


@router.delete("/licenses/{license_id}")
async def delete_license(
    license_id: str,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    db.table("compliance_licenses").delete().eq("id", license_id).eq("company_id", company_id).execute()
    return {"deleted": True, "id": license_id}


@router.delete("/brand-registrations/{reg_id}")
async def delete_brand_registration(
    reg_id: str,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    db.table("compliance_brand_registrations").delete().eq("id", reg_id).eq("company_id", company_id).execute()
    return {"deleted": True, "id": reg_id}


@router.delete("/federal-permits/{permit_id}")
async def delete_federal_permit(
    permit_id: str,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    db.table("compliance_federal_permits").delete().eq("id", permit_id).eq("company_id", company_id).execute()
    return {"deleted": True, "id": permit_id}


@router.delete("/distributors/{distributor_id}")
async def delete_distributor(
    distributor_id: str,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    db.table("compliance_distributors").delete().eq("id", distributor_id).eq("company_id", company_id).execute()
    return {"deleted": True, "id": distributor_id}


@router.delete("/colas/{cola_id}")
async def delete_cola(
    cola_id: str,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    db.table("compliance_colas").delete().eq("id", cola_id).eq("company_id", company_id).execute()
    return {"deleted": True, "id": cola_id}


# ─── PUT (update) endpoints ───────────────────────────────────────────────────


@router.put("/products/{product_id}")
async def update_product(
    product_id: str,
    request: AddProductRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    request.id = product_id
    return await add_product(request, user, db)


@router.put("/licenses/{license_id}")
async def update_license(
    license_id: str,
    request: AddStateLicenseRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    request.id = license_id
    return await add_license(request, user, db)


@router.put("/brand-registrations/{reg_id}")
async def update_brand_registration(
    reg_id: str,
    request: AddBrandRegistrationRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    request.id = reg_id
    return await add_brand_registration(request, user, db)


@router.put("/federal-permits/{permit_id}")
async def update_federal_permit(
    permit_id: str,
    request: AddFederalPermitRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    request.id = permit_id
    return await add_federal_permit(request, user, db)


@router.put("/distributors/{distributor_id}")
async def update_distributor(
    distributor_id: str,
    request: AddDistributorRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    request.id = distributor_id
    return await add_distributor(request, user, db)


@router.put("/colas/{cola_id}")
async def update_cola(
    cola_id: str,
    request: AddCOLARequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    request.id = cola_id
    return await add_cola(request, user, db)


# ─── Setup status ─────────────────────────────────────────────────────────────


@router.get("/setup-status")
async def get_setup_status(
    user: dict = Depends(require_permission("view_inventory")),
    db=Depends(get_db),
):
    """
    Returns whether this company has completed compliance setup.
    Used by the onboarding wizard to determine if first-time setup is needed.
    """
    company_id = user["company_id"]
    missing = []

    def _count(table: str) -> int:
        try:
            res = db.table(table).select("id", count="exact").eq("company_id", company_id).execute()
            return res.count or len(res.data or [])
        except Exception:
            return 0

    products_count = _count("compliance_products")
    licenses_count = _count("compliance_licenses")
    permits_count = _count("compliance_federal_permits")
    distributors_count = _count("compliance_distributors")
    brand_regs_count = _count("compliance_brand_registrations")

    if permits_count == 0:
        missing.append("federal_permits")
    if products_count == 0:
        missing.append("products")
    if licenses_count == 0:
        missing.append("licenses")

    is_setup = len(missing) == 0

    return {
        "is_setup": is_setup,
        "products_count": products_count,
        "licenses_count": licenses_count,
        "federal_permits_count": permits_count,
        "distributors_count": distributors_count,
        "brand_registrations_count": brand_regs_count,
        "missing": missing,
    }


# ─── CSV import ───────────────────────────────────────────────────────────────


class ImportRowsRequest(BaseModel):
    entity_type: str  # "licenses" | "products" | "brand_registrations" | "federal_permits" | "distributors" | "colas"
    rows: List[dict]


@router.post("/import")
async def import_rows(
    request: ImportRowsRequest,
    user: dict = Depends(require_permission("manage_inventory")),
    db=Depends(get_db),
):
    """
    Bulk import compliance entities from CSV rows.
    Each row dict should use column names matching the Add*Request fields.
    Returns count of imported rows and any per-row errors.
    """
    imported = 0
    errors: list[dict] = []

    for i, row in enumerate(request.rows):
        try:
            if request.entity_type == "licenses":
                req = AddStateLicenseRequest(
                    state_code=row.get("state_code", row.get("State", "")),
                    license_type=row.get("license_type", row.get("License Type", "importer")),
                    product_types=[pt.strip() for pt in str(row.get("product_types", row.get("Product Types", ""))).split(",") if pt.strip()],
                    license_number=row.get("license_number", row.get("License Number", row.get("Number", ""))),
                    issue_date=row.get("issue_date", row.get("Issue Date")),
                    expiration_date=row.get("expiration_date", row.get("Expiration Date", row.get("Expiry", ""))),
                    annual_fee=float(str(row.get("annual_fee", row.get("Annual Fee", 0))).replace("$", "").replace(",", "") or 0),
                    status=row.get("status", "active"),
                    notes=row.get("notes", row.get("Notes", "")),
                )
                await add_license(req, user, db)

            elif request.entity_type == "products":
                req = AddProductRequest(
                    sku=row.get("sku", row.get("SKU", "")),
                    name=row.get("name", row.get("Name", row.get("Product Name", ""))),
                    product_type=row.get("product_type", row.get("Type", "wine")).lower(),
                    abv_pct=float(row.get("abv_pct", row.get("ABV", row.get("ABV %", 0))) or 0),
                    container_size_ml=float(row.get("container_size_ml", row.get("Size (ml)", 750)) or 750),
                    cases_per_container=int(float(row.get("cases_per_container", row.get("Cases/Container", 56)) or 56)),
                    unit_cost_fob=float(str(row.get("unit_cost_fob", row.get("FOB Cost", 0))).replace("$", "").replace(",", "") or 0),
                    country_of_origin=row.get("country_of_origin", row.get("Country", "")),
                )
                await add_product(req, user, db)

            elif request.entity_type == "federal_permits":
                req = AddFederalPermitRequest(
                    permit_type=row.get("permit_type", row.get("Permit Type", "importer")).lower(),
                    permit_number=row.get("permit_number", row.get("Permit Number", row.get("Number", ""))),
                    issue_date=row.get("issue_date", row.get("Issue Date")),
                    expiration_date=row.get("expiration_date", row.get("Expiration Date", row.get("Expiry", ""))),
                    status=row.get("status", "active"),
                    notes=row.get("notes", row.get("Notes", "")),
                )
                await add_federal_permit(req, user, db)

            elif request.entity_type == "brand_registrations":
                req = AddBrandRegistrationRequest(
                    product_id=row.get("product_id", row.get("Product ID", "")),
                    state_code=row.get("state_code", row.get("State", "")),
                    registration_number=row.get("registration_number", row.get("Reg Number", "")),
                    registration_date=row.get("registration_date", row.get("Registration Date")),
                    expiration_date=row.get("expiration_date", row.get("Expiration Date")),
                    registration_fee=float(str(row.get("registration_fee", row.get("Fee", 0))).replace("$", "").replace(",", "") or 0),
                    status=row.get("status", "active"),
                    state_label_approval_number=row.get("state_label_approval_number", row.get("Label Approval #", "")),
                )
                await add_brand_registration(req, user, db)

            elif request.entity_type == "distributors":
                req = AddDistributorRequest(
                    state_code=row.get("state_code", row.get("State", "")),
                    distributor_name=row.get("distributor_name", row.get("Distributor", row.get("Name", ""))),
                    territory=row.get("territory", row.get("Territory", "Statewide")),
                    product_types=[pt.strip() for pt in str(row.get("product_types", row.get("Product Types", ""))).split(",") if pt.strip()],
                    contract_start_date=row.get("contract_start_date", row.get("Contract Start")),
                    contract_end_date=row.get("contract_end_date", row.get("Contract End")),
                    franchise_law_attached=str(row.get("franchise_law_attached", row.get("Franchise Law", "false"))).lower() in ("true", "yes", "1"),
                    termination_restriction=row.get("termination_restriction", row.get("Termination", "none")).lower(),
                    notes=row.get("notes", row.get("Notes", "")),
                )
                await add_distributor(req, user, db)

            elif request.entity_type == "colas":
                req = AddCOLARequest(
                    product_id=row.get("product_id", row.get("Product ID", "")),
                    cola_number=row.get("cola_number", row.get("COLA Number", row.get("Number", ""))),
                    product_type=row.get("product_type", row.get("Type", "wine")).lower(),
                    issue_date=row.get("issue_date", row.get("Issue Date")),
                    expiration_date=row.get("expiration_date", row.get("Expiration Date")),
                    status=row.get("status", "active"),
                    formula_approved=str(row.get("formula_approved", row.get("Formula Approved", "false"))).lower() in ("true", "yes", "1"),
                    lab_analysis_on_file=str(row.get("lab_analysis_on_file", row.get("Lab Analysis", "false"))).lower() in ("true", "yes", "1"),
                )
                await add_cola(req, user, db)

            else:
                raise ValueError(f"Unknown entity_type: {request.entity_type!r}")

            imported += 1

        except Exception as exc:
            errors.append({"row": i + 1, "data": row, "error": str(exc)})

    return {
        "entity_type": request.entity_type,
        "imported": imported,
        "errors": errors,
        "total_rows": len(request.rows),
    }
