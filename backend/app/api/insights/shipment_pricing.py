"""Read-only, tenant-validated allocation previews from entered cost evidence.

This is NOT a source reconciliation: invoice references are user assertions.
No writes to the pricing book, warehouse ledger, QuickBooks or providers.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.insights._common import advisory_response
from app.auth.rbac import require_permission
from app.intelligence.pricing.shipment_allocation import (
    AllocationScenario, ExpenseLine, ProductLine, allocate_shipment,
)

router = APIRouter()
PREFIX = "/shipment-pricing"
TAG = "Insights: shipment cost allocation"

# Monetary entries MUST be decimal strings. JSON floats lose exactness.
MONEY = r"^[0-9]{1,12}(?:\.[0-9]{1,6})?$"
CENTS = r"^[0-9]{1,12}(?:\.[0-9]{1,2})?$"
CASES = r"^[0-9]{1,10}(?:\.[0-9]{1,3})?$"
METRIC = r"^[0-9]{1,12}(?:\.[0-9]{1,6})?$"
PERCENT = r"^[0-9]{1,2}(?:\.[0-9]{1,4})?$"


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sku: str = Field(min_length=1, max_length=120)
    accepted_cases: str = Field(pattern=CASES)
    product_cost_per_case: str = Field(pattern=MONEY)
    weight_kg: str = Field("0", pattern=METRIC)
    volume_cbm: str = Field("0", pattern=METRIC)
    pallet_days: str = Field("0", pattern=METRIC)
    picks: str = Field("0", pattern=METRIC)
    discount_per_case: str = Field("0", pattern=MONEY)
    returns_allowance_per_case: str = Field("0", pattern=MONEY)
    commission_per_case: str = Field("0", pattern=MONEY)
    marketing_per_case: str = Field("0", pattern=MONEY)


class ExpenseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expense_line_ref: str = Field(min_length=1, max_length=160)
    component: str = Field(min_length=1, max_length=40)
    amount: str = Field(pattern=CENTS)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    allocation_basis: str = Field(pattern=r"^(cases|weight_kg|volume_cbm|pallet_days|picks)$")
    evidence_ref: str = Field(min_length=1, max_length=160)
    evidence_version: str = Field(min_length=1, max_length=120)


class AllocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    shipment_ref: str = Field(min_length=1, max_length=120)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    target_contribution_margin_pct: str = Field("30", pattern=PERCENT)
    products: list[ProductInput] = Field(min_length=1, max_length=100)
    expenses: list[ExpenseInput] = Field(default_factory=list, max_length=200)


@router.post("/preview")
async def preview_shipment_pricing(
    body: AllocationInput,
    user: dict = Depends(require_permission("view_financials")),
):
    """Preview per-SKU costs; the source and company are never trusted from client."""
    try:
        allocation = allocate_shipment(AllocationScenario(
            company_id=user["company_id"],
            shipment_ref=body.shipment_ref,
            currency=body.currency,
            target_contribution_margin_pct=Decimal(body.target_contribution_margin_pct),
            products=tuple(ProductLine(
                sku=item.sku, accepted_cases=Decimal(item.accepted_cases),
                product_cost_per_case=Decimal(item.product_cost_per_case),
                weight_kg=Decimal(item.weight_kg), volume_cbm=Decimal(item.volume_cbm),
                pallet_days=Decimal(item.pallet_days), picks=Decimal(item.picks),
                discount_per_case=Decimal(item.discount_per_case),
                returns_allowance_per_case=Decimal(item.returns_allowance_per_case),
                commission_per_case=Decimal(item.commission_per_case),
                marketing_per_case=Decimal(item.marketing_per_case),
            ) for item in body.products),
            expenses=tuple(ExpenseLine(
                expense_line_ref=cost.expense_line_ref,
                component=cost.component, amount=Decimal(cost.amount),
                currency=cost.currency, allocation_basis=cost.allocation_basis,
                evidence_ref=cost.evidence_ref,
                evidence_version=cost.evidence_version,
            ) for cost in body.expenses),
        ))
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(status_code=422,
                            detail="Invalid shipment cost inputs or allocation basis") from exc
    return advisory_response(allocation, source="user_supplied")
