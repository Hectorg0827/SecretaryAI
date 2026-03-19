"""
Data summarizer — prepares business data into compact summaries for the AI.
The AI never receives raw database rows or financial records.
"""
from decimal import Decimal
from typing import Optional


def summarize_inventory(inventory_rows: list[dict]) -> str:
    """Build a plain-language inventory summary for AI context."""
    if not inventory_rows:
        return "No inventory data available."

    lines = ["Current inventory levels:"]
    critical = [i for i in inventory_rows if i.get("stock_status") in ("critical", "out_of_stock")]
    low = [i for i in inventory_rows if i.get("stock_status") == "low"]
    healthy = [i for i in inventory_rows if i.get("stock_status") == "healthy"]

    for item in critical:
        weeks = item.get("weeks_remaining")
        wks_str = f"~{float(weeks):.1f} wks" if weeks else "—"
        lines.append(
            f"  [CRITICAL] {item['product_name']}: {item['total_qty']} cases, {wks_str} supply"
        )

    for item in low:
        weeks = item.get("weeks_remaining")
        wks_str = f"~{float(weeks):.1f} wks" if weeks else "—"
        lines.append(
            f"  [LOW] {item['product_name']}: {item['total_qty']} cases, {wks_str} supply"
        )

    lines.append(f"  {len(healthy)} products at healthy stock levels.")
    return "\n".join(lines)


def summarize_account(account: dict, recent_orders: list[dict]) -> str:
    """Build a plain-language account summary for AI context."""
    name = account.get("name", "Unknown")
    health = account.get("health_status", "unknown")
    score = account.get("health_score", 0)
    last_order = account.get("last_order_date", "never")
    balance = account.get("current_balance", 0)
    cycle = account.get("avg_order_cycle_days")

    lines = [
        f"Account: {name}",
        f"  Health: {health.upper()} (score: {score}/100)",
        f"  Last order: {last_order}",
        f"  Avg order cycle: {cycle} days" if cycle else "  Order cycle: not enough history",
        f"  Outstanding balance: ${float(balance):,.2f}",
    ]

    if recent_orders:
        lines.append(f"  Recent orders ({len(recent_orders)}):")
        for o in recent_orders[-5:]:
            lines.append(f"    {o.get('order_date')}: ${float(o.get('total_amount', 0)):,.2f}")

    return "\n".join(lines)


def summarize_accounts_overview(accounts: list[dict]) -> str:
    """High-level accounts overview for dashboard queries."""
    if not accounts:
        return "No account data available."

    total = len(accounts)
    by_status: dict[str, int] = {}
    for a in accounts:
        s = a.get("health_status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    lines = [
        f"Accounts overview ({total} total):",
        f"  Healthy: {by_status.get('healthy', 0)}",
        f"  Slowing: {by_status.get('slowing', 0)}",
        f"  At risk: {by_status.get('at_risk', 0)}",
        f"  Dormant: {by_status.get('dormant', 0)}",
    ]

    at_risk = [a for a in accounts if a.get("health_status") in ("at_risk", "dormant")]
    if at_risk:
        lines.append(f"  Accounts needing attention:")
        for a in at_risk[:5]:
            lines.append(f"    - {a['name']} ({a.get('health_status')})")

    return "\n".join(lines)


def build_query_context(intent: str, data: dict) -> str:
    """Route intent to appropriate summarizer and return context string."""
    if intent == "inventory_check":
        return summarize_inventory(data.get("inventory", []))

    if intent == "account_status":
        account = data.get("account")
        if account:
            return summarize_account(account, data.get("orders", []))
        return summarize_accounts_overview(data.get("accounts", []))

    if intent in ("sales_report", "general_question"):
        parts = []
        if data.get("accounts"):
            parts.append(summarize_accounts_overview(data["accounts"]))
        if data.get("inventory"):
            parts.append(summarize_inventory(data["inventory"]))
        return "\n\n".join(parts) if parts else "No data available for this query."

    return "Data context not available for this query type."
