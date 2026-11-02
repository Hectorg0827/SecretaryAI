"""Product concept preview: whitespace, launch economics and cannibalization.

Category demand, capture and cannibalization assumptions are not held by the
connected systems, so this is a what-if over values the user enters. Nothing
is stored and no product is created.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.insights._common import advisory_response
from app.api.insights._product_sales import from_input, money
from app.auth.rbac import require_permission
from app.intelligence.products.concepts import ConceptScenario, assess_concept

router = APIRouter()
PREFIX = "/product-concepts"
TAG = "insights: product concepts"

# Request numbers are bounded finite floats, converted to exact Decimals for
# the calculation (Decimal constraint context breaks the app's 422 serializer).
_Cases = Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False)]
_Money = Annotated[float, Field(ge=0, le=10_000_000_000, allow_inf_nan=False)]
_Pct = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class ConceptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_name: str = Field(min_length=1, max_length=120)
    observed_category_demand_cases: _Cases
    existing_served_category_cases: _Cases
    proposed_capture_pct: _Pct
    wholesale_price_per_case: _Money
    landed_cost_per_case: _Money
    variable_launch_cost_per_case: _Money
    one_time_launch_cost: _Money
    cannibalization_pct: _Pct
    incumbent_contribution_per_case: _Money
    # Pointers to the user's own evidence (e.g. "Category study, Sept 2026").
    demand_source_reference: str = Field("", max_length=120)
    assumption_version: str = Field("", max_length=60)


@router.post("/preview")
async def product_concept_preview(
    body: ConceptPreviewRequest,
    user: dict = Depends(require_permission("view_financials")),
):
    """Estimate first-year volume, profit and payback for one product idea."""
    scenario = ConceptScenario(
        company_id=user["company_id"],
        concept_id=body.concept_name.strip() or "concept",
        observed_category_demand_cases=from_input(body.observed_category_demand_cases),
        existing_served_category_cases=from_input(body.existing_served_category_cases),
        proposed_capture_pct=from_input(body.proposed_capture_pct),
        wholesale_price_per_case=from_input(body.wholesale_price_per_case),
        landed_cost_per_case=from_input(body.landed_cost_per_case),
        variable_launch_cost_per_case=from_input(body.variable_launch_cost_per_case),
        one_time_launch_cost=from_input(body.one_time_launch_cost),
        cannibalization_pct=from_input(body.cannibalization_pct),
        incumbent_contribution_per_case=from_input(body.incumbent_contribution_per_case),
        assumption_revision=body.assumption_version.strip(),
        demand_source_record_id=body.demand_source_reference.strip(),
    )
    try:
        result = assess_concept(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return advisory_response({
        "concept_name": body.concept_name.strip(),
        "addressable_whitespace_cases": money(result.addressable_whitespace_cases),
        "projected_new_product_cases": money(result.projected_new_product_cases),
        "cannibalized_incumbent_cases": money(result.cannibalized_incumbent_cases),
        "gross_new_product_contribution": money(result.gross_new_product_contribution),
        "incumbent_contribution_lost": money(result.incumbent_contribution_lost),
        "incremental_first_year_profit": money(result.incremental_first_year_profit_after_launch),
        "estimated_payback_months": money(result.estimated_payback_months, 1),
        "documented_assumptions": result.documented_assumptions,
        "risk_flags": list(result.risk_flags),
        "review_status": result.action_status,
    }, source="user_supplied")
