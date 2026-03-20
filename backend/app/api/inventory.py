"""Inventory API — real QB + warehouse data."""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_adapter
from app.auth.rbac import require_permission
from app.intelligence.inventory_monitor import evaluate_inventory
from app.intelligence.demand_forecasting import forecast_product

log = logging.getLogger(__name__)
router = APIRouter()


def _build_alert_dict(item: dict, status) -> dict:
    """
    Normalise a merged inventory item into the InventoryAlert shape
    expected by the frontend:
      item_id, product_name, total_qty, weeks_remaining, stock_status, needs_po
    """
    return {
        "item_id":        item.get("qb_id", ""),          # was missing
        "product_name":   item.get("product_name", ""),
        "total_qty":      item.get("total_qty", 0),
        "weeks_remaining": status.weeks_remaining,
        "stock_status":   status.status,                   # was `status`
        "needs_po":       status.status in ("critical", "out_of_stock"),
    }


@router.get("/")
async def list_inventory(
    limit: int = Query(200, ge=1, le=500),
    status_filter: str = Query(None, alias="status"),
    user: dict = Depends(require_permission("view_inventory")),
    adapter=Depends(get_adapter),
):
    """
    List all inventory items.
    Response key is `items` (not `inventory`) to match frontend interface.
    """
    inventory = await adapter.get_inventory_merged()
    results = []

    for item in inventory:
        sell_rate = Decimal(str(item.get("weekly_sell_rate", 0)))
        status = evaluate_inventory(
            item_id=item.get("qb_id", ""),
            product_name=item.get("product_name", ""),
            warehouse_1_qty=int(item.get("warehouse_qty", 0)),
            warehouse_2_qty=int(item.get("qb_qty", 0)),
            weekly_sell_rate=sell_rate,
        )

        entry = {
            "item_id":        item.get("qb_id"),            # frontend expects item_id
            "product_name":   item.get("product_name"),
            "sku":            item.get("sku"),
            "qb_qty":         item.get("qb_qty", 0),
            "warehouse_qty":  item.get("warehouse_qty", 0),
            "total_qty":      item.get("total_qty", 0),
            "reorder_point":  item.get("reorder_point"),
            "unit_price":     item.get("unit_price", 0),
            "purchase_cost":  item.get("purchase_cost", 0),
            "stock_status":   status.status,               # was `status`
            "weeks_remaining": status.weeks_remaining,
            "needs_po":       status.status in ("critical", "out_of_stock"),
            "source":         item.get("source", "qb"),
        }

        if status_filter and entry["stock_status"] != status_filter:
            continue
        results.append(entry)

    return {"items": results[:limit], "total": len(results)}  # `items`, not `inventory`


@router.get("/alerts")
async def get_inventory_alerts(
    user: dict = Depends(require_permission("view_inventory")),
    adapter=Depends(get_adapter),
):
    """Returns only items with low or critical stock status."""
    inventory = await adapter.get_inventory_merged()
    alerts = []

    for item in inventory:
        sell_rate = Decimal(str(item.get("weekly_sell_rate", 0)))
        status = evaluate_inventory(
            item_id=item.get("qb_id", ""),
            product_name=item.get("product_name", ""),
            warehouse_1_qty=int(item.get("warehouse_qty", 0)),
            warehouse_2_qty=int(item.get("qb_qty", 0)),
            weekly_sell_rate=sell_rate,
        )

        if status.status in ("critical", "low", "out_of_stock"):
            alerts.append(_build_alert_dict(item, status))

    return {"alerts": alerts, "count": len(alerts)}


@router.get("/{item_id}/forecast")
async def get_item_forecast(
    item_id: str,
    user: dict = Depends(require_permission("view_inventory")),
    adapter=Depends(get_adapter),
):
    """Demand forecast and recommended reorder date for a specific item."""
    inventory = await adapter.get_inventory_merged()
    item = next((i for i in inventory if i.get("qb_id") == item_id), None)

    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    sell_rate = item.get("weekly_sell_rate", 0)
    historical_weeks = [
        {"week": f"W-{i}", "quantity_sold": sell_rate}
        for i in range(8, 0, -1)
    ]

    forecast = forecast_product(
        product_id=item_id,
        product_name=item.get("product_name", ""),
        current_stock=float(item.get("total_qty", 0)),
        historical_weekly_sales=historical_weeks,
        lead_time_weeks=4,
        reorder_point=item.get("reorder_point"),
    )

    return {
        "product_name":               item["product_name"],
        "current_stock":              item.get("total_qty", 0),
        "projected_stockout_date":    forecast.projected_stockout_date,
        "recommended_reorder_date":   forecast.recommended_reorder_date,
        "recommended_order_qty":      forecast.recommended_order_qty,
        "weekly_forecast":            forecast.weekly_forecast,
        "confidence":                 forecast.confidence,
    }
