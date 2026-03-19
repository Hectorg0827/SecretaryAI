"""Accounts (customers) API — real QB data."""
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_adapter
from app.auth.rbac import get_current_user, require_permission
from app.intelligence.account_health import score_account
from app.intelligence.sales_analytics import compute_account_velocity, compute_trend_comparison

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def list_accounts(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_permission("view_customers")),
    adapter=Depends(get_adapter),
):
    """List all customers with their latest health score."""
    customers = await adapter.get_all_customers()
    invoices = await adapter.get_orders_last_n_days(365)

    by_customer: dict[str, list] = {}
    for inv in invoices:
        cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
        by_customer.setdefault(cid, []).append({
            "date": str(getattr(inv, "date", None) or inv.get("order_date", "")),
            "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
        })

    results = []
    for customer in customers[:limit]:
        cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
        name = getattr(customer, "name", "") or customer.get("name", "")
        orders = by_customer.get(cid, [])
        dates = [o["date"] for o in orders if o["date"]]
        last_order_str = max(dates, default=None)
        last_order = None
        if last_order_str:
            try:
                last_order = date.fromisoformat(last_order_str)
            except ValueError:
                pass

        health = score_account(
            account_id=cid,
            account_name=name,
            last_order_date=last_order,
            avg_order_cycle_days=None,
            order_history=orders,
            current_balance=float(getattr(customer, "balance", 0)),
        )

        results.append({
            "qb_id": cid,
            "name": name,
            "email": getattr(customer, "email", None),
            "state": getattr(customer, "state", None),
            "balance": float(getattr(customer, "balance", 0)),
            "total_sales": float(getattr(customer, "total_sales", 0)),
            "health_status": health.status,
            "health_score": health.score,
            "last_order_date": last_order_str,
            "flags": health.flags,
        })

    return {"accounts": results, "total": len(results)}


@router.get("/{account_id}")
async def get_account(
    account_id: str,
    user: dict = Depends(require_permission("view_customers")),
    adapter=Depends(get_adapter),
):
    """Get a single account with full order history and velocity analysis."""
    customers = await adapter.get_all_customers()
    customer = next(
        (c for c in customers if getattr(c, "qb_id", "") == account_id),
        None,
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Account not found")

    invoices = await adapter.get_orders_last_n_days(365)
    account_invoices = [
        inv for inv in invoices
        if (getattr(inv, "customer_id", None) or inv.get("customer_id", "")) == account_id
    ]

    invoice_dicts = [
        {
            "order_date": str(getattr(inv, "date", "") or inv.get("order_date", "")),
            "total_amount": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
            "status": getattr(inv, "status", ""),
            "items": getattr(inv, "line_items", []) or inv.get("line_items", []),
        }
        for inv in account_invoices
    ]

    velocity = compute_account_velocity(invoice_dicts) if invoice_dicts else {}
    trend = compute_trend_comparison(invoice_dicts, days=30) if invoice_dicts else {}

    last_dates = [d["order_date"] for d in invoice_dicts if d["order_date"]]
    last_order_str = max(last_dates, default=None)
    last_order = None
    if last_order_str:
        try:
            last_order = date.fromisoformat(last_order_str)
        except ValueError:
            pass

    health = score_account(
        account_id=account_id,
        account_name=getattr(customer, "name", ""),
        last_order_date=last_order,
        avg_order_cycle_days=velocity.get("avg_days_between_orders"),
        order_history=[{"date": d["order_date"], "total": d["total_amount"]} for d in invoice_dicts],
        current_balance=float(getattr(customer, "balance", 0)),
    )

    return {
        "qb_id": account_id,
        "name": getattr(customer, "name", ""),
        "email": getattr(customer, "email", None),
        "phone": getattr(customer, "phone", None),
        "state": getattr(customer, "state", None),
        "balance": float(getattr(customer, "balance", 0)),
        "total_sales": float(getattr(customer, "total_sales", 0)),
        "health_status": health.status,
        "health_score": health.score,
        "flags": health.flags,
        "velocity": velocity,
        "trend_30d": trend,
        "recent_invoices": invoice_dicts[:20],
    }
