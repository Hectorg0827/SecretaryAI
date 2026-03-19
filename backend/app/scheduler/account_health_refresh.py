"""
Account health refresh — re-scores all accounts nightly.
Updates health_status and health_score in the DB.
"""
import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)


async def refresh_all_account_health(company_id: str, adapter, db) -> dict:
    """Re-score every account and persist updated scores."""
    from app.intelligence.account_health import score_account

    results = {"updated": 0, "alerts": []}

    customers = await adapter.get_all_customers()
    date_to = date.today()
    date_from = date_to - timedelta(days=365)
    invoices = await adapter.get_orders_last_n_days(365)

    # Group invoices by customer
    by_customer: dict[str, list[dict]] = {}
    for inv in invoices:
        cid = inv.customer_id if hasattr(inv, "customer_id") else inv.get("customer_id", "")
        by_customer.setdefault(cid, []).append(
            {"date": inv.date if hasattr(inv, "date") else inv.get("order_date"),
             "total": float(inv.total if hasattr(inv, "total") else inv.get("total_amount", 0))}
        )

    for customer in customers:
        cid = customer.qb_id if hasattr(customer, "qb_id") else customer.get("qb_id", "")
        orders = by_customer.get(cid, [])
        last_order = max((o["date"] for o in orders), default=None)
        if isinstance(last_order, str):
            try:
                last_order = date.fromisoformat(last_order)
            except ValueError:
                last_order = None

        balance = customer.balance if hasattr(customer, "balance") else 0

        health = score_account(
            account_id=cid,
            account_name=customer.name if hasattr(customer, "name") else customer.get("name", ""),
            last_order_date=last_order,
            avg_order_cycle_days=None,
            order_history=orders,
            current_balance=balance,
        )

        # Update account in DB
        try:
            if hasattr(db, "table"):
                db.table("accounts").update({
                    "health_status": health.status,
                    "health_score": health.score,
                    "last_health_check": date.today().isoformat(),
                }).eq("qb_customer_id", cid).eq("company_id", company_id).execute()
        except Exception as e:
            log.error("Health refresh: DB update failed for %s: %s", cid, e)

        results["updated"] += 1

        if health.status in ("at_risk", "dormant"):
            results["alerts"].append({
                "account_id": cid,
                "account_name": health.account_name,
                "status": health.status,
                "score": health.score,
                "flags": health.flags,
            })

    log.info(
        "Account health refresh: %d accounts updated, %d alerts",
        results["updated"], len(results["alerts"]),
    )
    return results
