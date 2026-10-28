"""Supplier total cost of ownership and ESG comparison (§24).

Freight, duty, return rates, emissions and supplier ESG scores are not held
by the connected systems, so this is a what-if on figures the user enters:
POST /preview compares two to six suppliers for one product at the same
annual volume and unit, and optionally scores each supplier's ESG metrics.
Nothing is stored and no supplier is contacted or changed.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.insights._common import advisory_response
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.esg_tco import ESGTCOEngine

router = APIRouter()
PREFIX = "/supplier-tco"
TAG = "Insights: supplier TCO"

Amount = Annotated[float, Field(ge=0, le=10_000_000, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class SupplierESG(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carbon_per_case: float = Field(ge=0, le=100_000, allow_inf_nan=False)
    packaging_waste_kg_per_unit: float = Field(ge=0, le=100_000, allow_inf_nan=False)
    recyclable_packaging_pct: Percent
    water_intensity: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    safety_incidents: float = Field(ge=0, le=10_000, allow_inf_nan=False)
    supplier_audit_pass_rate: Percent
    local_sourcing_pct: Percent
    on_time_payment_rate: Percent
    import_compliance_rate: Percent


class SupplierCosts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    purchase_price: Amount
    inbound_freight_per_unit: Amount
    customs_duty_rate_pct: Percent
    brokerage_per_unit: Amount
    days_inventory_outstanding: float = Field(ge=0, le=3650, allow_inf_nan=False)
    holding_cost_rate_pct: Percent
    return_rate_pct: Percent
    return_handling_cost: Amount
    replacement_cost_per_return: Amount
    kg_co2e_per_unit: float = Field(ge=0, le=100_000, allow_inf_nan=False)
    volume_rebate_pct: Percent = 0.0
    esg: Optional[SupplierESG] = None


class SupplierTCOPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=120)
    annual_volume: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    unit_of_measure: str = Field(min_length=1, max_length=30)
    currency: Literal["USD"] = "USD"
    wacc_pct: Percent = 10.0
    current_supplier: str = Field(min_length=1, max_length=80)
    suppliers: list[SupplierCosts] = Field(min_length=2, max_length=6)


@router.post("/preview")
async def supplier_tco_preview(
    body: SupplierTCOPreview,
    user: dict = Depends(require_permission("view_financials")),
):
    """All-in cost per unit for each supplier, cheapest first, plus ESG scores."""
    engine = ESGTCOEngine(company_id=user["company_id"], wacc_pct=body.wacc_pct)
    shared = {
        "annual_volume": body.annual_volume,
        "unit_of_measure": body.unit_of_measure,
        "currency": body.currency,
    }
    try:
        comparison = engine.compare_suppliers(
            body.product,
            [
                {"supplier_id": s.name, **shared, **s.model_dump(exclude={"name", "esg"})}
                for s in body.suppliers
            ],
            current_supplier_id=body.current_supplier,
        ).to_dict()
        esg_scores = [
            engine.score_esg(entity_id=s.name, entity_type="supplier", **s.esg.model_dump()).to_dict()
            for s in body.suppliers if s.esg is not None
        ]
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Supplier names must be unique, include the current supplier, and every cost must be filled in",
        ) from exc
    return advisory_response({
        "company_id": user["company_id"],
        **comparison,
        "esg_scores": esg_scores,
        "assumptions": {
            "carbon_price_usd_per_ton": engine.INTERNAL_CARBON_PRICE_USD_PER_TON,
            "wacc_pct": body.wacc_pct,
            "esg_benchmarks": "illustrative reference values, not verified industry data",
        },
    }, source="user_supplied")
