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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
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

    return {"items": results[offset:offset+limit], "total": len(results), "offset": offset, "limit": limit}  # `items`, not `inventory`


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

    # Derive sales history from actual QuickBooks invoices. Never manufacture
    # eight weeks of history by repeating the current weekly average.
    from datetime import date, datetime
    from app.intelligence.demand_forecasting import DEFAULT_LEAD_TIME_WEEKS

    try:
        invoices = await adapter.get_orders_last_n_days(84)
    except Exception as exc:
        log.warning("Inventory forecast source unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Historical sales data unavailable") from exc

    today = date.today()
    history = [0.0] * 12  # Oldest week first; backed by a complete invoice query.
    matching_invoices = 0
    sku = str(item.get("sku") or "").strip().casefold()
    product = str(item.get("product_name") or "").strip().casefold()

    for invoice in invoices:
        raw_date = invoice.get("date", invoice.get("order_date")) if isinstance(invoice, dict) else invoice.date
        if isinstance(raw_date, datetime):
            raw_date = raw_date.date()
        if not isinstance(raw_date, date):
            try:
                raw_date = date.fromisoformat(str(raw_date))
            except (TypeError, ValueError):
                continue
        age = (today - raw_date).days
        if age < 0 or age >= 84:
            continue
        status = str(invoice.get("status", "") if isinstance(invoice, dict) else invoice.status).lower()
        if status in ("void", "voided", "cancelled", "canceled", "refunded"):
            continue
        lines = invoice.get("line_items", invoice.get("items", [])) if isinstance(invoice, dict) else invoice.line_items
        found = False
        for line in lines or []:
            if not isinstance(line, dict):
                continue
            line_sku = str(line.get("sku") or "").strip().casefold()
            line_name = str(line.get("name") or "").strip().casefold()
            if (sku and line_sku == sku) or (product and line_name == product):
                try:
                    qty = float(line.get("quantity", 0))
                except (TypeError, ValueError):
                    continue
                if 0 < qty < float("inf"):
                    history[11 - age // 7] += qty
                    found = True
        if found:
            matching_invoices += 1

    raw_lead = item.get("lead_time_weeks")
    try:
        lead_time = int(raw_lead)
        if not 1 <= lead_time <= 104:
            raise ValueError("lead time out of range")
        lead_time_source = "product"
    except (TypeError, ValueError):
        lead_time = DEFAULT_LEAD_TIME_WEEKS
        lead_time_source = "industry_default"

    forecast = forecast_product(
        product_name=item.get("product_name", ""),
        current_qty=float(item.get("total_qty", 0)),
        weekly_sell_history=history if matching_invoices else [],
        lead_time_weeks=lead_time,
        today=today,
    )
    observed_weeks = sum(1 for qty in history if qty > 0)
    confidence = (
        "high" if observed_weeks >= 8 else
        "medium" if observed_weeks >= 4 else "low"
    )

    return {
        "product_name": item.get("product_name", ""),
        "current_stock": item.get("total_qty", 0),
        "projected_stockout_date": forecast.projected_stockout_date,
        "recommended_reorder_date": forecast.recommended_reorder_date,
        "recommended_order_qty": forecast.recommended_order_qty,
        "weekly_forecast": [],
        "weekly_sell_history": history if matching_invoices else [],
        "historical_invoice_count": matching_invoices,
        "lead_time_weeks": lead_time,
        "lead_time_source": lead_time_source,
        "data_source": "quickbooks_invoices",
        "confidence": confidence,
    }
