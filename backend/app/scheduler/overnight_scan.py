"""
Overnight scan — full account health + inventory check for all active companies.
Runs nightly via Celery beat, typically 2–3 AM in the company's timezone.
"""
from datetime import date, timedelta

from app.intelligence.account_health import score_account
from app.intelligence.inventory_monitor import evaluate_inventory


async def run_overnight_scan(company_id: str, adapter) -> dict:
    """
    Pull fresh data from QB, score all accounts, evaluate inventory,
    and queue any alerts that need to go out.
    """
    results = {
        "company_id": company_id,
        "accounts_scanned": 0,
        "alerts_queued": 0,
        "inventory_alerts": [],
        "account_alerts": [],
    }

    # -- Account health --
    customers = await adapter.get_customers()
    date_to = date.today()
    date_from = date_to - timedelta(days=365)
    invoices = await adapter.get_invoices(date_from, date_to)

    # Group invoices by customer
    orders_by_customer: dict[str, list[dict]] = {}
    for inv in invoices:
        orders_by_customer.setdefault(inv.customer_id, []).append(
            {"date": inv.date, "total": float(inv.total)}
        )

    for customer in customers:
        cust_orders = orders_by_customer.get(customer.qb_id, [])
        last_order_date = max((o["date"] for o in cust_orders), default=None)
        health = score_account(
            account_id=customer.id,
            account_name=customer.name,
            last_order_date=last_order_date,
            avg_order_cycle_days=None,
            order_history=cust_orders,
            current_balance=customer.balance,
        )

        results["accounts_scanned"] += 1

        if health.status in ("at_risk", "dormant"):
            results["account_alerts"].append(
                {
                    "account_id": customer.id,
                    "account_name": customer.name,
                    "status": health.status,
                    "flags": health.flags,
                }
            )
            results["alerts_queued"] += 1

    # -- Inventory health --
    inventory = await adapter.get_inventory()
    for item in inventory:
        # Estimate weekly sell rate from recent invoices
        weekly_rate = _estimate_sell_rate(item.qb_id, invoices)
        status = evaluate_inventory(
            item_id=item.id,
            product_name=item.name,
            warehouse_1_qty=int(item.quantity_on_hand),
            warehouse_2_qty=0,
            weekly_sell_rate=weekly_rate,
        )

        if status.status in ("critical", "out_of_stock"):
            results["inventory_alerts"].append(
                {
                    "item_id": item.id,
                    "product_name": item.name,
                    "status": status.status,
                    "message": status.message,
                }
            )
            results["alerts_queued"] += 1

    return results


def _estimate_sell_rate(qb_item_id: str, invoices) -> float:
    """Rough weekly sell rate from invoice line items over the past 90 days."""
    from decimal import Decimal
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=90)
    total_qty = 0
    for inv in invoices:
        if inv.date < cutoff:
            continue
        for line in inv.line_items:
            if str(line.get("item_id", "")) == qb_item_id:
                total_qty += float(line.get("quantity", 0))

    return round(total_qty / 13, 2)  # 90 days ≈ 13 weeks
