"""Accounts (customers) API — real QB data."""
import logging
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_adapter
from app.auth.rbac import get_current_user, require_permission
from app.intelligence.account_health import score_account
from app.intelligence.sales_analytics import (
    compute_account_velocity,
    compute_trend_comparison,
    filter_billable_invoices,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _field(record, name: str, default=None):
    """Read a connector dataclass or a normalized mapping without falsey fallbacks."""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _owned_by_sales_rep(customer, user: dict) -> bool:
    """An absent or unverified assignment grants no sales-rep access."""
    assigned_rep = _field(customer, "assigned_rep_id")
    return bool(assigned_rep and assigned_rep == user.get("sub"))


def _finite_amount(value, *, field: str, signed: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field} is missing or invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is missing or invalid") from exc
    if not result.is_finite() or (result < 0 and not signed):
        raise ValueError(f"{field} is missing or invalid")
    return result


def _response_money(value: Decimal, *, field: str) -> float:
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} cannot be represented as a finite amount") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{field} cannot be represented as a finite amount")
    return numeric


def _invoice_row(invoice) -> dict:
    """Normalize observed invoice dates/money/identity once for both endpoints."""
    source_id = _field(invoice, "qb_id") or _field(invoice, "id")
    customer_id = _field(invoice, "customer_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("Account invoice missing source id")
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError("Account invoice missing customer id")

    raw_date = _field(invoice, "date")
    if raw_date is None:
        raw_date = _field(invoice, "order_date")
    if isinstance(raw_date, datetime):
        raise ValueError("Account invoice date must be a calendar date")
    if isinstance(raw_date, date):
        invoice_date = raw_date
    elif isinstance(raw_date, str):
        try:
            invoice_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError("Account invoice has invalid date") from exc
    else:
        raise ValueError("Account invoice missing date")

    raw_total = _field(invoice, "total")
    if raw_total is None:
        raw_total = _field(invoice, "total_amount")
    amount = _response_money(
        _finite_amount(raw_total, field="Invoice total"), field="Invoice total",
    )
    status = _field(invoice, "status")
    if status is not None and not isinstance(status, str):
        raise ValueError("Account invoice has invalid status")

    items = _field(invoice, "line_items")
    if items is None:
        items = _field(invoice, "items")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("Account invoice line items must be a list")

    return {
        "id": source_id.strip(),
        "customer_id": customer_id.strip(),
        "customer_name": _field(invoice, "customer_name") or "",
        "date": invoice_date.isoformat(),
        "order_date": invoice_date.isoformat(),
        "total": amount,
        "total_amount": amount,
        "status": status or "",
        "items": items,
    }


def _to_account_dict(customer, orders: list[dict], last_order_str: str | None) -> dict:
    """
    Normalise a QB Customer object + order history into the shape expected
    by the frontend Account interface:
      id, name, email, phone, state, health_status, health_score,
      last_order_date, current_balance, avg_order_value, assigned_rep
    """
    last_order = date.fromisoformat(last_order_str) if last_order_str else None

    cid = _field(customer, "qb_id", "")
    name = _field(customer, "name", "")

    balance = _finite_amount(_field(customer, "balance"), field="current_balance", signed=True)
    if orders and balance >= 0:
        health = score_account(
            account_id=cid,
            account_name=name,
            last_order_date=last_order,
            avg_order_cycle_days=None,
            order_history=orders,
            current_balance=balance,
        )
        health_status, health_score = health.status, health.score
    else:
        # The adapter fetches only 365 days, not lifetime activity. An empty
        # window cannot prove dormancy. Negative customer balances may reflect
        # credits; the health scorer cannot model them until AR reconciliation.
        health_status, health_score = "unknown", None

    billed_total = sum((Decimal(str(o["total"])) for o in orders), Decimal("0"))
    average = round(billed_total / len(orders), 2) if orders else Decimal("0")
    avg_order_value = _response_money(average, field="avg_order_value")

    return {
        "id":              cid,           # frontend uses `id`, not `qb_id`
        "name":            name,
        "email":           _field(customer, "email") or "",
        "phone":           _field(customer, "phone") or "",
        "state":           _field(customer, "state") or "",
        "health_status":   health_status,
        "health_score":    health_score,
        "health_data_quality": (
            "credit_balance_unscored" if balance < 0 else "partial_365_day_window"
        ),
        "history_window_days": 365,
        "last_order_date": last_order_str,
        "current_balance": _response_money(balance, field="current_balance"),
        "avg_order_value": avg_order_value,
        "avg_order_value_observed": bool(orders),
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
    if role == "sales_rep":
        customers = [customer for customer in customers if _owned_by_sales_rep(customer, user)]
    invoices = await adapter.get_orders_last_n_days(365)

    # Normalize and check duplicate source identities before calculating any
    # account metric. Only verified owned accounts contribute to a rep's view.
    visible_ids = {_field(customer, "qb_id") for customer in customers}
    rows = [
        _invoice_row(inv) for inv in invoices
        if _field(inv, "customer_id") in visible_ids
    ]
    billable = filter_billable_invoices(rows)

    by_customer: dict[str, list] = {}
    for inv in billable:
        cid = inv["customer_id"]
        by_customer.setdefault(cid, []).append({
            "date": inv["date"],
            "total": inv["total"],
        })

    results = []
    for customer in customers[:limit]:
        cid = _field(customer, "qb_id", "")

        orders = by_customer.get(cid, [])
        dates = [o["date"] for o in orders if o["date"]]
        last_order_str = max(dates, default=None)
        results.append(_to_account_dict(customer, orders, last_order_str))

    return {"accounts": results, "total": len(customers)}


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
        (c for c in customers if _field(c, "qb_id", "") == account_id),
        None,
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Account not found")
    if role == "sales_rep" and not _owned_by_sales_rep(customer, user):
        raise HTTPException(status_code=403, detail="Account is not assigned to this sales representative")

    invoices = await adapter.get_orders_last_n_days(365)
    account_invoices = [
        inv for inv in invoices
        if _field(inv, "customer_id", "") == account_id
    ]

    invoice_dicts = [_invoice_row(inv) for inv in account_invoices]
    billed = filter_billable_invoices(invoice_dicts)
    velocity = compute_account_velocity(billed) if billed else {}
    trend = compute_trend_comparison(billed, days=30) if billed else {}

    last_order_str = max((row["date"] for row in billed), default=None)
    score_rows = [{"date": row["date"], "total": row["total"]} for row in billed]
    base = _to_account_dict(customer, score_rows, last_order_str)

    # Provider enumeration order is not a chronological business contract.
    recent_invoices = sorted(
        invoice_dicts, key=lambda row: (row["order_date"], row["id"]), reverse=True,
    )[:20]

    return {
        **base,
        "velocity":       velocity,
        "trend_30d":      trend,
        "recent_invoices": recent_invoices,
    }


ORDER_HISTORY_DAYS = 365
_ACCOUNT_ROLES = ("owner", "manager", "sales_rep", "back_office")


def _order_row(invoice) -> dict:
    """One invoice as shown on the account's Orders tab; invalid data raises."""
    row = _invoice_row(invoice)

    raw_due = _field(invoice, "due_date")
    if raw_due is None or raw_due == "":
        due_date = None
    elif isinstance(raw_due, date) and not isinstance(raw_due, datetime):
        due_date = raw_due.isoformat()
    elif isinstance(raw_due, str):
        try:
            due_date = date.fromisoformat(raw_due).isoformat()
        except ValueError as exc:
            raise ValueError("Account invoice has invalid due date") from exc
    else:
        raise ValueError("Account invoice has invalid due date")

    # Unknown open balance stays unknown; it is never shown as $0 owed.
    raw_balance = _field(invoice, "balance")
    balance_due = None
    if raw_balance is not None:
        balance_due = _response_money(
            _finite_amount(raw_balance, field="Invoice balance"), field="Invoice balance",
        )

    return {
        "id": row["id"],
        "invoice_date": row["date"],
        "due_date": due_date,
        "total_amount": row["total"],
        "balance_due": balance_due,
        "status": row["status"],
    }


@router.get("/{account_id}/orders")
async def get_account_orders(
    account_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
):
    """Invoices billed to one account in the last 365 days, newest first.

    Same access as account detail: owner/manager/back office, and a sales rep
    only for an account assigned to them. Tenant scope comes from the adapter
    built for ``user["company_id"]``. A failed or unreadable source is an
    outage (503), never an empty order list.
    """
    role = user.get("role", "viewer")
    if role not in _ACCOUNT_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to view accounts")

    try:
        customers = await adapter.get_all_customers()
    except Exception as exc:
        log.warning("Account orders: customer source read failed")
        raise HTTPException(
            status_code=503, detail="Customer records are unavailable right now",
        ) from exc
    customer = next(
        (c for c in customers if _field(c, "qb_id", "") == account_id),
        None,
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Account not found")
    if role == "sales_rep" and not _owned_by_sales_rep(customer, user):
        raise HTTPException(status_code=403, detail="Account is not assigned to this sales representative")

    try:
        invoices = await adapter.get_orders_last_n_days(ORDER_HISTORY_DAYS)
    except Exception as exc:
        log.warning("Account orders: invoice source read failed")
        raise HTTPException(
            status_code=503, detail="Order history is unavailable right now",
        ) from exc

    rows: list[dict] = []
    unmatched = 0
    try:
        for invoice in invoices:
            customer_id = _field(invoice, "customer_id")
            if not isinstance(customer_id, str) or not customer_id.strip():
                # Could belong to this account; reported, never silently dropped.
                unmatched += 1
                continue
            if customer_id.strip() == account_id:
                rows.append(_order_row(invoice))
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("Account invoices have duplicate source ids")
    except (ValueError, TypeError, ArithmeticError) as exc:
        log.warning("Account orders: invoice data unreadable")
        raise HTTPException(
            status_code=503,
            detail="Order history for this account could not be read completely",
        ) from exc

    # Provider enumeration order is not a chronological business contract.
    rows.sort(key=lambda row: (row["invoice_date"], row["id"]), reverse=True)
    return {
        "account_id": account_id,
        "orders": rows[:limit],
        "count": min(len(rows), limit),
        "total": len(rows),
        "truncated": len(rows) > limit,
        "history_window_days": ORDER_HISTORY_DAYS,
        "unmatched_invoices": unmatched,
    }
