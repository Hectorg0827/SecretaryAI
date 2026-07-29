"""Accounts (customers) API — real QB data."""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_adapter
from app.auth.rbac import get_current_user, require_permission
from app.intelligence.account_health import score_account
from app.intelligence.sales_analytics import compute_account_velocity, compute_trend_comparison

log = logging.getLogger(__name__)
router = APIRouter()


def _to_account_dict(customer, orders: list[dict], last_order_str: str | None) -> dict:
    """
    Normalise a QB Customer object + order history into the shape expected
    by the frontend Account interface:
      id, name, email, phone, state, health_status, health_score,
      last_order_date, current_balance, avg_order_value, assigned_rep
    """
    last_order: date | None = None
    if last_order_str:
        try:
            last_order = date.fromisoformat(last_order_str)
        except ValueError:
            pass

    cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
    name = getattr(customer, "name", "") or customer.get("name", "")

    health = score_account(
        account_id=cid,
        account_name=name,
        last_order_date=last_order,
        avg_order_cycle_days=None,
        order_history=orders,
        current_balance=float(getattr(customer, "balance", 0)),
    )

    total_revenue = sum(o.get("total", 0) for o in orders)
    avg_order_value = round(total_revenue / len(orders), 2) if orders else 0.0

    return {
        "id":              cid,           # frontend uses `id`, not `qb_id`
        "name":            name,
        "email":           getattr(customer, "email", None) or "",
        "phone":           getattr(customer, "phone", None) or "",
        "state":           getattr(customer, "state", None) or "",
        "health_status":   health.status,
        "health_score":    health.score,
        "last_order_date": last_order_str,
        "current_balance": float(getattr(customer, "balance", 0)),  # was `balance`
        "avg_order_value": avg_order_value,
        "assigned_rep":    None,           # not available from QB yet
    }


@router.get("/")
async def list_accounts(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
):
    """
    List customers with their latest health score.
    Access:
      owner / manager  → all accounts
      back_office      → all accounts (read-only enforced at UI)
      sales_rep        → own accounts only (filtered by assigned_rep_id)
      viewer and below → 403
    """
    from fastapi import HTTPException
    role = user.get("role", "viewer")
    if role not in ("owner", "manager", "sales_rep", "back_office"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to view accounts")

    customers = await adapter.get_all_customers()
    invoices = await adapter.get_orders_last_n_days(365)

    by_customer: dict[str, list] = {}
    for inv in invoices:
        cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
        by_customer.setdefault(cid, []).append({
            "date":  str(getattr(inv, "date", None) or inv.get("order_date", "")),
            "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
        })

    results = []
    for customer in customers[:limit]:
        cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")

        # sales_rep: filter to their assigned accounts only
        # NOTE: assigned_rep_id is not yet synced from QB — once available this
        # will automatically restrict the list.
        if role == "sales_rep":
            assigned = (
                getattr(customer, "assigned_rep_id", None)
                or customer.get("assigned_rep_id")
            )
            if assigned and assigned != user["sub"]:
                continue

        orders = by_customer.get(cid, [])
        dates = [o["date"] for o in orders if o["date"]]
        last_order_str = max(dates, default=None)
        results.append(_to_account_dict(customer, orders, last_order_str))

    return {"accounts": results, "total": len(results)}


@router.get("/{account_id}")
async def get_account(
    account_id: str,
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
):
    """Get a single account with full order history and velocity analysis."""
    # Same role gate as list_accounts — viewers cannot read account detail.
    role = user.get("role", "viewer")
    if role not in ("owner", "manager", "sales_rep", "back_office"):
        raise HTTPException(status_code=403, detail="Not authorized to view accounts")

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
            "order_date":   str(getattr(inv, "date", "") or inv.get("order_date", "")),
            "total_amount": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
            "total":        float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
            "status":       getattr(inv, "status", ""),
            "items":        getattr(inv, "line_items", []) or inv.get("line_items", []),
        }
        for inv in account_invoices
    ]

    velocity = compute_account_velocity(invoice_dicts) if invoice_dicts else {}
    trend    = compute_trend_comparison(invoice_dicts, days=30) if invoice_dicts else {}

    dates = [d["order_date"] for d in invoice_dicts if d["order_date"]]
    last_order_str = max(dates, default=None)

    base = _to_account_dict(customer, invoice_dicts, last_order_str)

    return {
        **base,
        "velocity":       velocity,
        "trend_30d":      trend,
        "recent_invoices": invoice_dicts[:20],
    }
