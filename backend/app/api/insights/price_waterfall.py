"""Price waterfall preview: from list price down to pocket price and margin.

Discounts, allowances, commissions, marketing support and landed-cost
components (freight, duty, excise and so on) are not held by the connected
systems, so this is a what-if over values the user enters. Nothing is stored
and no price is changed.
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.insights._common import advisory_response
from app.api.insights._product_sales import from_input, money
from app.auth.rbac import require_permission
from app.intelligence.pricing.waterfall import (
    REQUIRED_COMPONENTS, LandedCostLine, PriceScenario, calculate_price_waterfall,
)

router = APIRouter()
PREFIX = "/price-waterfall"
TAG = "insights: price waterfall"

# Request numbers are bounded finite floats, converted to exact Decimals for
# the calculation (Decimal constraint context breaks the app's 422 serializer).
_Money = Annotated[float, Field(ge=0, le=10_000_000, allow_inf_nan=False)]
_Pct = Annotated[float, Field(ge=0, lt=100, allow_inf_nan=False)]


class CostLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z_]*$")
    amount_per_case: _Money


class WaterfallPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_label: str = Field("", max_length=120)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    price_per_case: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)
    approved_price_per_case: Optional[_Money] = None
    discount_per_case: _Money = 0
    returns_allowance_per_case: _Money = 0
    commission_per_case: _Money = 0
    marketing_per_case: _Money = 0
    cost_lines: list[CostLineInput] = Field(default_factory=list, max_length=20)
    target_margin_pct: _Pct = 30
    distributor_margin_pct: _Pct = 25
    retailer_margin_pct: _Pct = 30
    # Pointer to the user's cost evidence (e.g. "Broker invoice 2026-10").
    cost_evidence_reference: str = Field("", max_length=120)
    cost_evidence_version: str = Field("", max_length=60)


@router.post("/preview")
async def price_waterfall_preview(
    body: WaterfallPreviewRequest,
    user: dict = Depends(require_permission("view_financials")),
):
    """Walk one case from list price down to pocket price and margin."""
    price = from_input(body.price_per_case)
    discount = from_input(body.discount_per_case)
    returns_allowance = from_input(body.returns_allowance_per_case)
    commission = from_input(body.commission_per_case)
    marketing = from_input(body.marketing_per_case)
    reference = body.cost_evidence_reference.strip()
    version = body.cost_evidence_version.strip()
    scenario = PriceScenario(
        company_id=user["company_id"],
        sku=body.product_label.strip() or "preview",
        currency=body.currency,
        price_to_distributor=price,
        cost_lines=tuple(
            LandedCostLine(line.component, from_input(line.amount_per_case),
                           source_record_id=reference, source_revision=version)
            for line in body.cost_lines
        ),
        discount_per_case=discount,
        returns_allowance_per_case=returns_allowance,
        commission_per_case=commission,
        marketing_per_case=marketing,
        target_margin_pct=from_input(body.target_margin_pct),
        distributor_margin_pct=from_input(body.distributor_margin_pct),
        retailer_margin_pct=from_input(body.retailer_margin_pct),
        approved_list_price=(
            from_input(body.approved_price_per_case) if body.approved_price_per_case is not None else None
        ),
    )
    try:
        result = calculate_price_waterfall(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Pocket price: what is left of the price after every per-case deduction
    # paid to customers, reps and promotions, before product costs.
    pocket = result.net_revenue_per_case - commission - marketing
    steps = [
        {"key": "list_price", "label": "List price", "amount": money(price), "running": money(price)},
        {"key": "discount", "label": "Discount", "amount": money(-discount),
         "running": money(price - discount)},
        {"key": "returns_allowance", "label": "Returns allowance", "amount": money(-returns_allowance),
         "running": money(result.net_revenue_per_case)},
        {"key": "commission", "label": "Sales commission", "amount": money(-commission),
         "running": money(result.net_revenue_per_case - commission)},
        {"key": "marketing", "label": "Marketing support", "amount": money(-marketing),
         "running": money(pocket)},
        {"key": "landed_cost", "label": "Landed product cost", "amount": money(-result.landed_cost_per_case),
         "running": money(result.contribution_per_case)},
    ]
    entered = {line.component for line in body.cost_lines}
    return advisory_response({
        "product_label": body.product_label.strip(),
        "currency": body.currency,
        "steps": steps,
        "net_revenue_per_case": money(result.net_revenue_per_case),
        "pocket_price_per_case": money(pocket),
        "landed_cost_per_case": money(result.landed_cost_per_case),
        "gross_profit_per_case": money(result.gross_profit_per_case),
        "gross_margin_pct": money(result.gross_margin_pct, 1),
        "contribution_per_case": money(result.contribution_per_case),
        "contribution_margin_pct": money(result.contribution_margin_pct, 1),
        "estimated_distributor_to_retailer": money(result.estimated_distributor_to_retailer),
        "estimated_retailer_to_consumer": money(result.estimated_retailer_to_consumer),
        "complete_cost_components": result.complete_cost_components,
        "missing_cost_components": sorted(REQUIRED_COMPONENTS - entered),
        "documented_cost_components": result.documented_cost_components,
        "exception_reasons": list(result.exception_reasons),
        "review_status": result.status,
    }, source="user_supplied")
