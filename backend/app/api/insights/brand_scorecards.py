"""Brand (product) vitality from connected sales, and supplier scorecard previews.

QuickBooks does not record brands, so each product is scored on its own from
billed invoices and the merged inventory. Supplier delivery performance is not
held by any connected system; the preview scores figures the user enters.
Analysis only: nothing is written, ordered or sent.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response
from app.api.insights._product_sales import (
    aggregate_sales, from_input, load_catalog, money, periods, product_vitals, read_sales,
)
from app.auth.rbac import require_permission
from app.intelligence.brands.scorecards import (
    SupplierObservation, SupplierScoreWeights, supplier_scorecard,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/brand-scorecards"
TAG = "insights: brand scorecards"

# Request numbers are finite floats (converted to Decimal for the scorecard);
# Decimal constraint context breaks the app's 422 error serializer.
_Units = Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False)]
_Weight = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


@router.get("")
async def brand_scorecards(
    weeks: int = Query(4, ge=1, le=26),
    limit: int = Query(200, ge=1, le=500),
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
):
    """Score each product's last ``weeks`` against the equal period before it."""
    inventory, invoices = await read_sales(adapter, days=weeks * 14)
    catalog, catalog_skipped = load_catalog(inventory)
    current, previous = periods(date.today(), weeks, with_previous=True)
    window = aggregate_sales(invoices, catalog, current, previous)

    products = []
    rows_skipped = catalog_skipped + window.rows_skipped
    inactive = 0
    for product in catalog.values():
        now = current.products.get(product.item_id)
        before = previous.products.get(product.item_id)
        if now is None and before is None and not product.stock:
            inactive += 1
            continue
        try:
            vitals = product_vitals(user["company_id"], product, window, weeks)
        except ValueError:
            # Returns larger than sales or negative net sales: unscoreable.
            log.warning("Brand scorecard row skipped")
            rows_skipped += 1
            continue
        products.append({
            "item_id": product.item_id,
            "name": product.name,
            "sku": product.sku,
            "net_sales": money(now.net_sales if now else Decimal("0")),
            "net_cases": money(vitals.net_cases),
            "net_case_growth_pct": money(vitals.net_case_growth_pct, 1),
            "account_penetration_pct": money(vitals.account_penetration_pct, 1),
            "repeat_account_pct": money(vitals.repeat_account_pct, 1),
            "return_rate_pct": money(vitals.return_rate_pct, 1),
            "gross_margin_pct": money(vitals.gross_margin_pct, 1),
            "weeks_of_supply": money(vitals.weeks_of_supply, 1),
            "gaps": list(vitals.gaps),
            "review_status": vitals.action_status,
        })

    products.sort(key=lambda row: (-(row["net_sales"] or 0), row["name"]))
    return advisory_response({
        "weeks": weeks,
        "current_period": current.as_dict(),
        "previous_period": previous.as_dict(),
        "products": products[:limit],
        "products_total": len(products),
        "products_without_activity": inactive,
        "rows_skipped": rows_skipped,
        "lines_not_in_catalog": window.lines_not_in_catalog,
        "basis": "QuickBooks invoices and inventory; each product is scored on its own.",
    }, source="connected_data")


class SupplierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str = Field(min_length=1, max_length=120)
    units_ordered: _Units
    units_filled: _Units
    units_delivered: _Units
    units_damaged: _Units
    on_time_shipments: int = Field(ge=0, le=1_000_000)
    received_shipments: int = Field(ge=0, le=1_000_000)
    # Pointer to the user's own record (e.g. "Receiving log, October"); a
    # supplier without one gets rates but no rankable composite score.
    evidence_reference: str = Field("", max_length=120)
    evidence_version: str = Field("", max_length=60)


class SupplierWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fill: _Weight
    on_time: _Weight
    undamaged: _Weight

    @model_validator(mode="after")
    def _sum_to_one(self):
        if from_input(self.fill) + from_input(self.on_time) + from_input(self.undamaged) != Decimal("1"):
            raise PydanticCustomError("weights_sum", "Score weights must add up to 1")
        return self


class SupplierPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suppliers: list[SupplierInput] = Field(min_length=1, max_length=25)
    weights: Optional[SupplierWeights] = None


@router.post("/supplier-preview")
async def supplier_preview(
    body: SupplierPreviewRequest,
    user: dict = Depends(require_permission("view_financials")),
):
    """Score supplier fill rate, on-time delivery and damage from entered figures."""
    weights = SupplierScoreWeights(
        fill=from_input(body.weights.fill), on_time=from_input(body.weights.on_time),
        undamaged=from_input(body.weights.undamaged),
    ) if body.weights else SupplierScoreWeights()
    results = []
    for index, item in enumerate(body.suppliers, start=1):
        observation = SupplierObservation(
            company_id=user["company_id"],
            supplier_id=item.supplier_name.strip() or f"supplier-{index}",
            units_ordered=from_input(item.units_ordered),
            units_filled=from_input(item.units_filled),
            units_delivered=from_input(item.units_delivered),
            units_damaged=from_input(item.units_damaged),
            on_time_shipments=item.on_time_shipments,
            received_shipments=item.received_shipments,
            source_record_id=item.evidence_reference.strip(),
            source_revision=item.evidence_version.strip(),
        )
        try:
            card = supplier_scorecard(observation, weights)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Supplier {index}: {exc}") from exc
        results.append({
            "supplier_name": item.supplier_name.strip(),
            "fill_rate_pct": money(card.fill_rate_pct, 1),
            "on_time_pct": money(card.on_time_pct, 1),
            "damage_rate_pct": money(card.damage_rate_pct, 1),
            "composite_score": money(card.composite_score, 1),
            "documented_inputs": card.documented_inputs,
            "gaps": list(card.gaps),
            "review_status": card.action_status,
        })
    return advisory_response({
        "suppliers": results,
        "weights": {
            "fill": float(weights.fill), "on_time": float(weights.on_time),
            "undamaged": float(weights.undamaged),
        },
    }, source="user_supplied")
