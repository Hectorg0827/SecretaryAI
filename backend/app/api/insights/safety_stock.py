"""Safety stock and reorder points under demand and lead-time uncertainty (§19).

GET reads the tenant's inventory, the last 90 days of invoice lines and the
SecretaryAI purchasing ledger (supplier lead times, units already on order).
An item without sales, a purchase cost or an observed lead time is reported
as "insufficient data" rather than given an invented history. POST /preview
runs the same calculation on figures the user enters. Nothing is ordered.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_adapter, get_db
from app.api.insights._common import advisory_response, read_source
from app.api.insights._supply_chain_sources import (
    ItemIndex, clean_invoices, daily_series, finite_number, inventory_list,
    norm, read_purchasing_history, sales_by_item,
)
from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.stochastic_inv import (
    DemandDistribution, LeadTimeDistribution, StochasticInventoryEngine,
)

log = logging.getLogger(__name__)
router = APIRouter()
PREFIX = "/safety-stock"
TAG = "Insights: safety stock"

DEMAND_WINDOW_DAYS = 90
SERVICE_LEVELS = (0.90, 0.95, 0.98, 0.99)


def _engine(service_level: float, ordering_cost: float, holding_rate_pct: float) -> StochasticInventoryEngine:
    # No revenue figure is passed, so the engine stays on its Phase 1 normal
    # approximation; the response says so in "assumptions".
    return StochasticInventoryEngine(
        annual_revenue=0.0,
        ordering_cost_per_po=ordering_cost,
        holding_cost_rate=holding_rate_pct / 100,
        service_level=service_level,
    )


def _plain_units(value: float) -> float | int:
    return int(value) if float(value).is_integer() else round(value, 2)


def _recommendation_fields(rec, stock_position: float, engine: StochasticInventoryEngine) -> dict:
    data = rec.to_dict()
    reorder_now, reason = engine.reorder_needed(_plain_units(stock_position), rec)
    return {
        "safety_stock_units": data["safety_stock_units"],
        "reorder_point_units": data["reorder_point_units"],
        "suggested_order_qty": data["economic_order_qty"],
        "days_of_supply_at_reorder_point": data["days_of_supply_at_rop"],
        "stockout_risk_without_buffer": data["stockout_probability_without_safety_stock"],
        "avg_daily_sales": data["demand_mean_daily"],
        "daily_sales_variability": data["demand_std_daily"],
        "lead_time_days_avg": data["lead_time_mean_days"],
        "lead_time_days_variability": data["lead_time_std_days"],
        "demand_days_observed": data["demand_observations"],
        "lead_time_observations": data["supplier_lead_time_observations"],
        "data_quality": data["data_quality"],
        "notes": data["notes"],
        "reorder_now": reorder_now,
        "reorder_reason": reason,
    }


def _insufficient(base: dict, reason: str) -> dict:
    return {**base, "status": "insufficient_data", "reason": reason, "reorder_now": False}


@router.get("")
async def safety_stock_overview(
    service_level: float = Query(0.95),
    ordering_cost_per_po: float = Query(50.0, ge=0, le=100_000, allow_inf_nan=False),
    holding_cost_rate_pct: float = Query(25.0, gt=0, le=100, allow_inf_nan=False),
    user: dict = Depends(require_permission("view_inventory")),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """Per-item safety stock, reorder point and order size from connected data."""
    if service_level not in SERVICE_LEVELS:
        raise HTTPException(status_code=422, detail="service_level must be 0.90, 0.95, 0.98 or 0.99")
    company_id = user["company_id"]
    inventory = inventory_list(
        await read_source(adapter.get_inventory_merged(), source_name="Inventory data")
    )
    raw_invoices = await read_source(
        adapter.get_orders_last_n_days(DEMAND_WINDOW_DAYS), source_name="Sales history",
    )
    purchasing = await read_source(
        read_purchasing_history(db, company_id), source_name="Purchasing records",
    )

    today = date.today()
    try:
        invoices, invoices_skipped = clean_invoices(raw_invoices, today=today, days=DEMAND_WINDOW_DAYS)
    except ValueError as exc:
        log.warning("Safety stock sales source malformed")
        raise HTTPException(status_code=503, detail="Sales history is unavailable; no analysis was produced") from exc
    sales, lines_skipped = sales_by_item(invoices, ItemIndex(inventory))
    engine = _engine(service_level, ordering_cost_per_po, holding_cost_rate_pct)

    rows: list[dict] = []
    items_skipped = 0
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            items_skipped += 1
            continue
        sku_key = norm(item.get("sku"))
        on_hand = finite_number(item.get("total_qty"))
        on_order = purchasing.open_units_by_sku.get(sku_key, 0.0) if sku_key else 0.0
        base = {
            "item_id": item.get("qb_id"),
            "product_name": item.get("product_name") or "",
            "sku": item.get("sku"),
            "on_hand": on_hand,
            "on_order": on_order,
        }
        unit_cost = finite_number(item.get("purchase_cost"))
        item_sales = sales.get(index, [])
        lead_times = purchasing.lead_times_by_sku.get(sku_key, []) if sku_key else []
        if on_hand is None or on_hand < 0:
            rows.append(_insufficient(base, "The stock count is missing or negative; correct it before planning."))
            continue
        if not item_sales:
            rows.append(_insufficient(base, f"No sales recorded in the last {DEMAND_WINDOW_DAYS} days."))
            continue
        if not lead_times:
            rows.append(_insufficient(
                base, "No received purchase orders for this SKU yet, so the supplier's delivery time is unknown.",
            ))
            continue
        if unit_cost is None or unit_cost <= 0:
            rows.append(_insufficient(base, "No purchase cost recorded, so an order size can't be worked out."))
            continue
        stock_position = on_hand + on_order
        try:
            rec = engine.calculate(
                DemandDistribution(
                    sku=str(item.get("sku") or item.get("qb_id") or ""),
                    daily_demand_history=daily_series(item_sales, today=today, days=DEMAND_WINDOW_DAYS),
                ),
                LeadTimeDistribution(
                    supplier_id="purchasing_ledger", sku=sku_key, lead_time_history=lead_times,
                ),
                unit_cost,
                stock_position,
            )
        except ValueError:
            items_skipped += 1
            continue
        rows.append({
            **base, "stock_position": stock_position, "status": "calculated", "reason": "",
            **_recommendation_fields(rec, stock_position, engine),
        })

    order = {"calculated": 0, "insufficient_data": 1}
    rows.sort(key=lambda r: (not r["reorder_now"], order[r["status"]], r["product_name"].casefold()))
    calculated = [r for r in rows if r["status"] == "calculated"]
    return advisory_response({
        "company_id": company_id,
        "items": rows,
        "summary": {
            "items_total": len(rows),
            "items_calculated": len(calculated),
            "items_insufficient_data": len(rows) - len(calculated),
            "items_to_reorder": sum(1 for r in calculated if r["reorder_now"]),
            "rows_skipped": invoices_skipped + lines_skipped + purchasing.rows_skipped + items_skipped,
        },
        "assumptions": {
            "service_level": service_level,
            "ordering_cost_per_po": ordering_cost_per_po,
            "holding_cost_rate_pct": holding_cost_rate_pct,
            "demand_window_days": DEMAND_WINDOW_DAYS,
            "method": "normal_approximation",
            "lead_time_source": "SecretaryAI purchasing ledger: approval to first posted receipt",
            "on_order_source": "SecretaryAI purchasing ledger: approved, not yet fully received",
        },
    }, source="connected_data")


# ── What-if on figures the user enters ────────────────────────────────────────

NonNegative = Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)]
LeadDays = Annotated[float, Field(gt=0, le=730, allow_inf_nan=False)]


class SafetyStockPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(min_length=1, max_length=120)
    daily_sales: list[NonNegative] = Field(min_length=7, max_length=730)
    lead_times_days: list[LeadDays] = Field(min_length=1, max_length=100)
    unit_cost: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    current_stock: float = Field(ge=0, le=1_000_000_000, allow_inf_nan=False)
    service_level: Literal[0.90, 0.95, 0.98, 0.99] = 0.95
    ordering_cost_per_po: float = Field(50.0, ge=0, le=100_000, allow_inf_nan=False)
    holding_cost_rate_pct: float = Field(25.0, gt=0, le=100, allow_inf_nan=False)


@router.post("/preview")
async def safety_stock_preview(
    body: SafetyStockPreview,
    user: dict = Depends(require_permission("view_inventory")),
):
    """Safety stock for one product from user-entered sales and lead times."""
    engine = _engine(body.service_level, body.ordering_cost_per_po, body.holding_cost_rate_pct)
    try:
        rec = engine.calculate(
            DemandDistribution(sku=body.product_name, daily_demand_history=list(body.daily_sales)),
            LeadTimeDistribution(
                supplier_id="user_supplied", sku=body.product_name,
                lead_time_history=list(body.lead_times_days),
            ),
            body.unit_cost,
            body.current_stock,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="These figures can't be used for a safety stock calculation") from exc
    return advisory_response({
        "company_id": user["company_id"],
        "product_name": body.product_name,
        "current_stock": body.current_stock,
        **_recommendation_fields(rec, body.current_stock, engine),
        "assumptions": {
            "service_level": body.service_level,
            "ordering_cost_per_po": body.ordering_cost_per_po,
            "holding_cost_rate_pct": body.holding_cost_rate_pct,
            "method": "normal_approximation",
        },
    }, source="user_supplied")
