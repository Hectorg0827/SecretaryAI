"""Accounts (customers) API — real QB data."""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from pydantic import BaseModel

from app.api.deps import get_adapter, get_db
from app.auth.account_scope import can_see_customer, visible_customer_ids
from app.auth.rbac import get_current_user, has_permission, require_permission
from app.utils.audit import write_audit_event
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
        "assigned_rep":    None,           # filled in from account_assignments
        "assigned_rep_id": None,
    }


def _load_assignments(db, company_id: str) -> dict[str, dict]:
    """{qb_customer_id: {"user_id", "name"}} for a company (empty on error)."""
    try:
        rows = (
            db.table("account_assignments").select("qb_customer_id, user_id")
            .eq("company_id", company_id).execute()
        ).data
        if not isinstance(rows, list) or not rows:
            return {}
        users = (
            db.table("users").select("id, name, email").eq("company_id", company_id).execute()
        ).data
        names = {str(u["id"]): (u.get("name") or u.get("email") or "") for u in (users if isinstance(users, list) else [])}
        return {
            str(r["qb_customer_id"]): {"user_id": str(r["user_id"]), "name": names.get(str(r["user_id"]), "")}
            for r in rows
        }
    except Exception as exc:
        log.warning("Could not load account assignments: %s", type(exc).__name__)
        return {}


# ── Assigning accounts to sales reps ──────────────────────────────────────────

@router.get("/reps")
async def list_assignable_reps(
    user: dict = Depends(require_permission("assign_accounts")),
    db=Depends(get_db),
):
    """Active people in this company an account can be assigned to."""
    try:
        rows = (
            db.table("users").select("id, name, email, role, is_active")
            .eq("company_id", user["company_id"]).eq("is_active", True).execute()
        ).data or []
    except Exception as exc:
        log.error("Rep list failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    reps = [
        {"id": str(r["id"]), "name": r.get("name") or r.get("email") or "", "role": r.get("role")}
        for r in rows if r.get("role") in ("sales_rep", "manager", "owner")
    ]
    return {"reps": sorted(reps, key=lambda r: (r["role"] != "sales_rep", r["name"].lower()))}


class AssignRepRequest(BaseModel):
    user_id: str | None = None   # null = remove the assignment


@router.put("/{account_id}/rep")
async def assign_rep(
    account_id: str,
    body: AssignRepRequest,
    user: dict = Depends(require_permission("assign_accounts")),
    db=Depends(get_db),
    adapter=Depends(get_adapter),
):
    """Assign a customer account to a rep (or clear it). Owner / manager only."""
    company_id = user["company_id"]

    # The account must be a real customer of THIS company's QuickBooks.
    customers = await adapter.get_all_customers()
    if not any(str(getattr(c, "qb_id", "")) == str(account_id) for c in customers):
        raise HTTPException(status_code=404, detail="Account not found")

    previous = _load_assignments(db, company_id).get(str(account_id))
    rep_name = None
    try:
        if body.user_id:
            # The rep must be an active user of the SAME company.
            target = (
                db.table("users").select("id, name, email, role, is_active")
                .eq("id", body.user_id).eq("company_id", company_id).execute()
            ).data
            if not target or not target[0].get("is_active", True):
                raise HTTPException(status_code=404, detail="That person was not found on your team.")
            if target[0].get("role") not in ("sales_rep", "manager", "owner"):
                raise HTTPException(status_code=422, detail="Accounts can only be assigned to a sales rep, manager or owner.")
            rep_name = target[0].get("name") or target[0].get("email") or ""
            db.table("account_assignments").upsert(
                {
                    "company_id": company_id,
                    "qb_customer_id": str(account_id),
                    "user_id": str(target[0]["id"]),
                    "assigned_by": user["sub"],
                    "assigned_at": date.today().isoformat(),
                },
                on_conflict="company_id,qb_customer_id",
            ).execute()
        else:
            db.table("account_assignments").delete().eq("company_id", company_id).eq(
                "qb_customer_id", str(account_id)
            ).execute()
    except HTTPException:
        raise
    except Exception as exc:
        log.error("Account assignment failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    write_audit_event(
        db, company_id=company_id, event_type="account_assigned" if body.user_id else "account_unassigned",
        actor_id=user["sub"], capability="customers",
        before_snapshot={"user_id": (previous or {}).get("user_id")},
        after_snapshot={"user_id": body.user_id},
        metadata={"qb_customer_id": str(account_id)},
    )
    return {"account_id": str(account_id), "assigned_rep_id": body.user_id, "assigned_rep": rep_name}


@router.get("/")
async def list_accounts(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """
    List customers with their latest health score.
    Access:
      owner / manager  → all accounts
      back_office      → all accounts (read-only enforced at UI)
      sales_rep        → ONLY accounts assigned to them (see account_scope.py)
      viewer and below → 403
    """
    role = user.get("role", "viewer")
    if role not in ("owner", "manager", "sales_rep", "back_office"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to view accounts")

    allowed = visible_customer_ids(db, user)
    if allowed is not None and not allowed:
        # A rep with no accounts yet: nothing to fetch from QuickBooks.
        return {"accounts": [], "total": 0}

    customers = await adapter.get_all_customers()
    invoices = await adapter.get_orders_last_n_days(365)

    by_customer: dict[str, list] = {}
    for inv in invoices:
        cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
        by_customer.setdefault(cid, []).append({
            "date":  str(getattr(inv, "date", None) or inv.get("order_date", "")),
            "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
        })

    assignments = _load_assignments(db, user["company_id"])
    show_money = has_permission(role, "view_financials")

    results = []
    for customer in customers:
        cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
        if not can_see_customer(allowed, cid):
            continue
        if len(results) >= limit:
            break

        orders = by_customer.get(cid, [])
        dates = [o["date"] for o in orders if o["date"]]
        last_order_str = max(dates, default=None)
        row = _to_account_dict(customer, orders, last_order_str)
        rep = assignments.get(str(cid))
        if rep:
            row["assigned_rep"] = rep["name"]
            row["assigned_rep_id"] = rep["user_id"]
        results.append(row)

    return {"accounts": results, "total": len(results), "can_assign": has_permission(role, "assign_accounts"),
            "shows_financials": show_money}


@router.get("/{account_id}")
async def get_account(
    account_id: str,
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """Get a single account with full order history and velocity analysis."""
    # Same role gate as list_accounts — viewers cannot read account detail.
    role = user.get("role", "viewer")
    if role not in ("owner", "manager", "sales_rep", "back_office"):
        raise HTTPException(status_code=403, detail="Not authorized to view accounts")

    # A sales rep can open only their own accounts. Someone else's account
    # answers exactly like one that does not exist.
    if not can_see_customer(visible_customer_ids(db, user), account_id):
        raise HTTPException(status_code=404, detail="Account not found")

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
    rep = _load_assignments(db, user["company_id"]).get(str(account_id))
    if rep:
        base["assigned_rep"] = rep["name"]
        base["assigned_rep_id"] = rep["user_id"]

    return {
        **base,
        "velocity":       velocity,
        "trend_30d":      trend,
        "recent_invoices": invoice_dicts[:20],
    }
