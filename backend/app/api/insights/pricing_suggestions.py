"""Advisory price suggestions (§23). Never changes a price anywhere.

GET reads the tenant's inventory (list price, purchase cost) and the last 52
weeks of invoice lines. Each 4-week period's average selling price is
compared with the one before; a move of 3% or more counts as an observed
price change for the elasticity estimate. The pricing engine holds the price
unless there are at least three such changes with consistent customer
response, so most items show "hold" until real evidence exists.
POST /preview and POST /supplier-increase are what-ifs on entered figures.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_adapter
from app.api.insights._common import advisory_response, read_source
from app.api.insights._supply_chain_sources import (
    ItemIndex, SaleLine, clean_invoices, finite_number, inventory_list, sales_by_item,
)
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.dynamic_pricing import (
    DynamicPricingEngine, PricingTier,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/pricing-suggestions"
TAG = "Insights: pricing suggestions"

PERIOD_DAYS = 28
PERIODS = 13                      # 52 weeks of history
MIN_PRICE_MOVE = 0.03
SEGMENT = "all_customers"


def _price_change_events(lines: list[SaleLine], today: date) -> list[dict]:
    """Observed price moves between consecutive 4-week periods, oldest first."""
    units = [0.0] * PERIODS
    priced_units = [0.0] * PERIODS
    revenue = [0.0] * PERIODS
    for line in lines:
        age = (today - line.day).days
        if not 0 <= age < PERIODS * PERIOD_DAYS:
            continue
        period = PERIODS - 1 - age // PERIOD_DAYS
        units[period] += line.quantity
        if line.unit_price is not None:
            priced_units[period] += line.quantity
            revenue[period] += line.quantity * line.unit_price
    prices = [revenue[p] / priced_units[p] if priced_units[p] > 0 else None for p in range(PERIODS)]
    events = []
    for p in range(1, PERIODS):
        before, after = prices[p - 1], prices[p]
        if before is None or after is None or units[p - 1] <= 0:
            continue
        if abs(after - before) / before >= MIN_PRICE_MOVE:
            events.append({
                "old_price": before, "new_price": after,
                "units_before": units[p - 1], "units_after": units[p],
                "period_days": PERIOD_DAYS,
            })
    return events


def _row_from_recommendation(rec: dict, target_margin_pct: float) -> dict:
    changed = abs(rec["price_change_pct"]) > 0
    return {
        "status": "suggested_change" if changed else "hold",
        "current_price": rec["current_price"],
        "suggested_price": rec["recommended_price"],
        "change_pct": rec["price_change_pct"],
        "current_margin_pct": rec["current_margin_pct"],
        "projected_margin_pct": rec["projected_margin_pct"],
        "below_target_margin": rec["current_margin_pct"] < target_margin_pct,
        "volume_impact_pct": rec["volume_impact_pct"],
        "revenue_impact_annual": rec["revenue_impact_estimate_annual"],
        "confidence": rec["confidence"],
        "competitive_position": rec["competitive_position"],
        "rationale": rec["rationale"],
    }


def _elasticity_fields(elasticity) -> dict:
    return {
        "price_changes_observed": elasticity.sample_size,
        "price_sensitivity": elasticity.elasticity_label if elasticity.sample_size else "unknown",
        "elasticity": round(elasticity.ped, 3) if elasticity.sample_size else None,
    }


@router.get("")
async def pricing_suggestions(
    target_margin_pct: float = Query(30.0, ge=0, le=95, allow_inf_nan=False),
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
):
    """Per-item price suggestion or hold, with margin against the target."""
    company_id = user["company_id"]
    inventory = inventory_list(
        await read_source(adapter.get_inventory_merged(), source_name="Inventory data")
    )
    raw_invoices = await read_source(
        adapter.get_orders_last_n_days(PERIODS * PERIOD_DAYS), source_name="Sales history",
    )
    today = date.today()
    try:
        invoices, invoices_skipped = clean_invoices(raw_invoices, today=today, days=PERIODS * PERIOD_DAYS)
    except ValueError as exc:
        log.warning("Pricing suggestions sales source malformed")
        raise HTTPException(status_code=503, detail="Sales history is unavailable; no analysis was produced") from exc
    sales, lines_skipped = sales_by_item(invoices, ItemIndex(inventory))
    engine = DynamicPricingEngine(company_id=company_id, target_gross_margin_pct=target_margin_pct)

    rows: list[dict] = []
    items_skipped = 0
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            items_skipped += 1
            continue
        base = {
            "item_id": item.get("qb_id"),
            "product_name": item.get("product_name") or "",
            "sku": item.get("sku"),
        }
        price = finite_number(item.get("unit_price"))
        cost = finite_number(item.get("purchase_cost"))
        lines = sales.get(index, [])
        annual_units = sum(line.quantity for line in lines)
        reason = None
        if price is None or price <= 0:
            reason = "No selling price recorded in QuickBooks."
        elif cost is None or cost <= 0:
            reason = "No purchase cost recorded, so margin can't be worked out."
        elif annual_units <= 0:
            reason = "No sales in the last 12 months."
        if reason:
            rows.append({**base, "status": "insufficient_data", "reason": reason})
            continue
        label = str(item.get("sku") or item.get("qb_id") or base["product_name"])
        try:
            elasticity = engine.estimate_elasticity(
                segment=SEGMENT, sku_category=label, price_change_events=_price_change_events(lines, today),
            )
            rec = engine.recommend_price(
                sku=label, segment=SEGMENT, pricing_tier=PricingTier.TIER_2,
                current_price=price, unit_cost=cost, annual_units=annual_units, elasticity=elasticity,
            ).to_dict()
        except ValueError:
            items_skipped += 1
            continue
        rows.append({
            **base, "reason": "", "units_last_12_months": round(annual_units, 2),
            **_row_from_recommendation(rec, target_margin_pct), **_elasticity_fields(elasticity),
        })

    rank = {"suggested_change": 0, "hold": 1, "insufficient_data": 2}
    rows.sort(key=lambda r: (rank[r["status"]], r.get("current_margin_pct", 0.0)))
    return advisory_response({
        "company_id": company_id,
        "target_margin_pct": target_margin_pct,
        "items": rows,
        "summary": {
            "items_total": len(rows),
            "suggested_changes": sum(1 for r in rows if r["status"] == "suggested_change"),
            "holds": sum(1 for r in rows if r["status"] == "hold"),
            "below_target_margin": sum(1 for r in rows if r.get("below_target_margin")),
            "items_insufficient_data": sum(1 for r in rows if r["status"] == "insufficient_data"),
            "rows_skipped": invoices_skipped + lines_skipped + items_skipped,
        },
        "method": {
            "period_days": PERIOD_DAYS,
            "periods": PERIODS,
            "min_price_move_pct": MIN_PRICE_MOVE * 100,
            "segment": "All customers together; customer-specific prices are averaged.",
        },
        "prices_changed": False,
    }, source="connected_data")


# ── What-ifs on entered figures ───────────────────────────────────────────────

Price = Annotated[float, Field(gt=0, le=10_000_000, allow_inf_nan=False)]


class PriceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_price: Price
    new_price: Price
    units_before: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    units_after: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    period_days: int = Field(30, ge=1, le=365)


class PriceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(min_length=1, max_length=120)
    current_price: Price
    unit_cost: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    annual_units: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    target_margin_pct: float = Field(30.0, ge=0, le=95, allow_inf_nan=False)
    competitor_prices: list[Price] = Field(default_factory=list, max_length=20)
    price_changes: list[PriceChange] = Field(default_factory=list, max_length=50)


@router.post("/preview")
async def pricing_preview(
    body: PriceCheck,
    user: dict = Depends(require_permission("view_financials")),
):
    """Suggested price for one product from entered prices, costs and history."""
    engine = DynamicPricingEngine(company_id=user["company_id"], target_gross_margin_pct=body.target_margin_pct)
    try:
        elasticity = engine.estimate_elasticity(
            segment=SEGMENT, sku_category=body.product_name,
            price_change_events=[change.model_dump() for change in body.price_changes],
        )
        rec = engine.recommend_price(
            sku=body.product_name, segment=SEGMENT, pricing_tier=PricingTier.TIER_2,
            current_price=body.current_price, unit_cost=body.unit_cost,
            annual_units=body.annual_units, elasticity=elasticity,
            competitive_prices=list(body.competitor_prices) or None,
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="These figures can't be used for a price check") from exc
    return advisory_response({
        "company_id": user["company_id"],
        "product_name": body.product_name,
        "target_margin_pct": body.target_margin_pct,
        **_row_from_recommendation(rec, body.target_margin_pct),
        **_elasticity_fields(elasticity),
        "prices_changed": False,
    }, source="user_supplied")


class SegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment: str = Field(min_length=1, max_length=60)
    pricing_tier: Literal["tier_1", "tier_2", "tier_3", "tier_4"]
    annual_units: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    elasticity_ped: Optional[float] = Field(None, ge=-10, le=0, allow_inf_nan=False)
    competitor_prices: list[Price] = Field(default_factory=list, max_length=20)


class SupplierIncrease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=120)
    cost_before: Price
    cost_after: Price
    current_price: Price
    segments: list[SegmentInput] = Field(min_length=1, max_length=4)


@router.post("/supplier-increase")
async def supplier_increase_preview(
    body: SupplierIncrease,
    user: dict = Depends(require_permission("view_financials")),
):
    """How much of a supplier cost increase to pass on, per customer group."""
    engine = DynamicPricingEngine(company_id=user["company_id"])
    try:
        plan = engine.supplier_increase_strategy(
            supplier=body.supplier, sku=body.product_name,
            unit_cost_before=body.cost_before, unit_cost_after=body.cost_after,
            current_price=body.current_price,
            segment_data=[
                {
                    "segment": seg.segment, "pricing_tier": seg.pricing_tier,
                    "annual_units": seg.annual_units, "elasticity_ped": seg.elasticity_ped,
                    "competitive_prices": list(seg.competitor_prices) or None,
                }
                for seg in body.segments
            ],
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="These figures can't be used for a cost increase plan") from exc
    return advisory_response({**plan, "prices_changed": False}, source="user_supplied")
