"""Dashboard summary endpoint — real QB data."""
import logging
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from app.api.deps import get_adapter, get_db
from app.auth.rbac import get_current_user
from app.intelligence.account_health import score_account
from app.intelligence.inventory_monitor import evaluate_inventory

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/summary")
async def get_dashboard_summary(
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """
    High-level dashboard: account health distribution, inventory alerts,
    recent 30-day sales total, and pending approval count.
    """
    company_id = user["company_id"]
    today = date.today()

    # ── Customers + health scores ──────────────────────────────────────────
    health_counts: Counter = Counter()
    try:
        customers = await adapter.get_all_customers()
        invoices_yr = await adapter.get_orders_last_n_days(365)

        by_customer: dict[str, list] = {}
        for inv in invoices_yr:
            cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
            by_customer.setdefault(cid, []).append({
                "date": getattr(inv, "date", None) or inv.get("order_date"),
                "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
            })

        for customer in customers:
            cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
            orders = by_customer.get(cid, [])
            dates = [o["date"] for o in orders if o["date"]]
            last_order = max(dates, default=None)
            if isinstance(last_order, str):
                try:
                    from datetime import date as _date
                    last_order = _date.fromisoformat(last_order)
                except ValueError:
                    last_order = None

            health = score_account(
                account_id=cid,
                account_name=getattr(customer, "name", ""),
                last_order_date=last_order,
                avg_order_cycle_days=None,
                order_history=orders,
                current_balance=float(getattr(customer, "balance", 0)),
            )
            health_counts[health.status] += 1
    except Exception as exc:
        log.error("Dashboard: health scoring failed: %s", exc)

    # ── Inventory alerts ───────────────────────────────────────────────────
    inventory_alerts: list[dict] = []
    try:
        inventory = await adapter.get_inventory_merged()
        for item in inventory:
            from decimal import Decimal
            status = evaluate_inventory(
                item_id=item.get("qb_id", ""),
                product_name=item.get("product_name", ""),
                warehouse_1_qty=int(item.get("warehouse_qty", 0)),
                warehouse_2_qty=int(item.get("qb_qty", 0)),
                weekly_sell_rate=Decimal(str(item.get("weekly_sell_rate", 0))),
            )
            if status.status in ("critical", "low"):
                inventory_alerts.append({
                    "product_name": item["product_name"],
                    "status": status.status,
                    "total_qty": item.get("total_qty", 0),
                    "weeks_remaining": status.weeks_of_stock,
                })
    except Exception as exc:
        log.error("Dashboard: inventory eval failed: %s", exc)

    # ── 30-day sales total ─────────────────────────────────────────────────
    sales_30d = 0.0
    try:
        invoices_30 = await adapter.get_orders_last_n_days(30)
        sales_30d = sum(
            float(getattr(inv, "total", 0) or inv.get("total_amount", 0))
            for inv in invoices_30
        )
    except Exception as exc:
        log.error("Dashboard: sales total failed: %s", exc)

    # ── Pending approvals count ────────────────────────────────────────────
    pending_count = 0
    try:
        result = (
            db.table("drafts")
            .select("id", count="exact")
            .eq("company_id", company_id)
            .eq("status", "pending")
            .execute()
        )
        pending_count = result.count or 0
    except Exception as exc:
        log.warning("Dashboard: pending count failed: %s", exc)

    return {
        "company_id": company_id,
        "accounts": {
            "healthy": health_counts.get("healthy", 0),
            "slowing": health_counts.get("slowing", 0),
            "at_risk": health_counts.get("at_risk", 0),
            "dormant": health_counts.get("dormant", 0),
            "total": sum(health_counts.values()),
        },
        "inventory_alerts": inventory_alerts,
        "sales_30d": round(sales_30d, 2),
        "pending_approvals": pending_count,
    }
