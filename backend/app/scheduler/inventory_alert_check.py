"""
Inventory alert check — runs every 6 hours.
Evaluates current stock levels and queues alerts for critical/low items.
"""
import logging
from decimal import Decimal

log = logging.getLogger(__name__)


async def check_and_alert_inventory(company_id: str, adapter, action_engine, user_id: str) -> dict:
    """
    Pull current inventory, evaluate status, fire alerts for critical items.
    """
    from app.intelligence.inventory_monitor import evaluate_inventory

    results = {"critical": [], "low": [], "alerts_fired": 0}

    try:
        inventory = await adapter.get_inventory_merged()
    except Exception as e:
        log.error("Inventory alert check: fetch failed: %s", e)
        return results

    for item in inventory:
        qty_1 = int(item.get("warehouse_qty", 0))
        qty_2 = int(item.get("qb_qty", item.get("quantity_on_hand", 0)))
        sell_rate = Decimal(str(item.get("weekly_sell_rate", 0)))

        status = evaluate_inventory(
            item_id=item.get("qb_id", item.get("id", "")),
            product_name=item.get("product_name", "Unknown"),
            warehouse_1_qty=qty_1,
            warehouse_2_qty=qty_2,
            weekly_sell_rate=sell_rate,
        )

        if status.status == "critical":
            results["critical"].append(item["product_name"])
        elif status.status == "low":
            results["low"].append(item["product_name"])

    # Fire a single consolidated alert if any critical items
    if results["critical"] and action_engine:
        critical_names = ", ".join(results["critical"][:5])
        try:
            await action_engine.process(
                action_type="send_low_stock_alert",
                payload={
                    "subject": f"CRITICAL: {len(results['critical'])} products nearly out of stock",
                    "body": (
                        f"The following products are critically low:\n\n"
                        f"{chr(10).join('- ' + n for n in results['critical'])}\n\n"
                        f"Immediate action may be required."
                    ),
                    "severity": "critical",
                    "products": results["critical"],
                },
                company_id=company_id,
                user_id=user_id,
            )
            results["alerts_fired"] += 1
        except Exception as e:
            log.error("Inventory alert: failed to fire alert: %s", e)

    return results
