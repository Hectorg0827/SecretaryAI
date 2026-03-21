"""
Data summarizer — prepares business data into compact summaries for the AI.
The AI never receives raw database rows or financial records.

All terminology (unit names, entity labels, status labels) is pulled from the
active IndustryModule so output automatically uses the correct industry language.
"""
from decimal import Decimal
from typing import Optional


def _get_terminology(industry_module=None) -> dict:
    """Get terminology from module, or fall back to wholesale distribution defaults."""
    if industry_module is None:
        return {
            "unit_of_measure": "cases",
            "account": "account",
            "order": "order",
            "critical_stock_label": "critically low",
            "low_stock_label": "running low",
        }
    return industry_module.get_terminology()


def summarize_inventory(inventory_rows: list[dict], industry_module=None) -> str:
    """Build a plain-language inventory summary for AI context."""
    if not inventory_rows:
        return "No inventory data available."

    terms = _get_terminology(industry_module)
    unit = terms.get("unit_of_measure", "units")

    lines = ["Current inventory levels:"]
    critical = [i for i in inventory_rows if i.get("stock_status") in ("critical", "out_of_stock")]
    low = [i for i in inventory_rows if i.get("stock_status") == "low"]
    healthy = [i for i in inventory_rows if i.get("stock_status") == "healthy"]

    for item in critical:
        weeks = item.get("weeks_remaining")
        wks_str = f"~{float(weeks):.1f} wks" if weeks else "—"
        lines.append(
            f"  [CRITICAL] {item['product_name']}: {item['total_qty']} {unit}, {wks_str} supply"
        )

    for item in low:
        weeks = item.get("weeks_remaining")
        wks_str = f"~{float(weeks):.1f} wks" if weeks else "—"
        lines.append(
            f"  [LOW] {item['product_name']}: {item['total_qty']} {unit}, {wks_str} supply"
        )

    lines.append(f"  {len(healthy)} products at healthy stock levels.")
    return "\n".join(lines)


def summarize_account(account: dict, recent_orders: list[dict], industry_module=None) -> str:
    """Build a plain-language account summary for AI context."""
    terms = _get_terminology(industry_module)
    account_term = terms.get("account", "account").capitalize()
    order_term = terms.get("order", "order")

    name = account.get("name", "Unknown")
    health = account.get("health_status", "unknown")
    score = account.get("health_score", 0)
    last_order = account.get("last_order_date", "never")
    balance = account.get("current_balance", 0)
    cycle = account.get("avg_order_cycle_days")

    lines = [
        f"{account_term}: {name}",
        f"  Health: {health.upper()} (score: {score}/100)",
        f"  Last {order_term}: {last_order}",
        f"  Avg {order_term} cycle: {cycle} days" if cycle else f"  {order_term.capitalize()} cycle: not enough history",
        f"  Outstanding balance: ${float(balance):,.2f}",
    ]

    if recent_orders:
        lines.append(f"  Recent {order_term}s ({len(recent_orders)}):")
        for o in recent_orders[-5:]:
            lines.append(f"    {o.get('order_date')}: ${float(o.get('total_amount', 0)):,.2f}")

    return "\n".join(lines)


def summarize_accounts_overview(accounts: list[dict], industry_module=None) -> str:
    """High-level accounts overview for dashboard queries."""
    if not accounts:
        return "No account data available."

    terms = _get_terminology(industry_module)
    account_term = terms.get("account", "account")

    total = len(accounts)
    by_status: dict[str, int] = {}
    for a in accounts:
        s = a.get("health_status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    lines = [
        f"{account_term.capitalize()}s overview ({total} total):",
        f"  Healthy: {by_status.get('healthy', 0)}",
        f"  Slowing: {by_status.get('slowing', 0)}",
        f"  At risk: {by_status.get('at_risk', 0)}",
        f"  Dormant: {by_status.get('dormant', 0)}",
    ]

    at_risk = [a for a in accounts if a.get("health_status") in ("at_risk", "dormant")]
    if at_risk:
        lines.append(f"  {account_term.capitalize()}s needing attention:")
        for a in at_risk[:5]:
            lines.append(f"    - {a['name']} ({a.get('health_status')})")

    return "\n".join(lines)


def build_query_context(intent: str, data: dict, industry_module=None) -> str:
    """
    Route intent to appropriate summarizer and return context string.
    industry_module: optional, used to apply correct terminology.
    """
    if intent == "inventory_check":
        return summarize_inventory(data.get("inventory", []), industry_module)

    if intent == "account_status":
        account = data.get("account")
        if account:
            return summarize_account(account, data.get("orders", []), industry_module)
        return summarize_accounts_overview(data.get("accounts", []), industry_module)

    if intent in ("sales_report", "general_question"):
        parts = []
        if data.get("accounts"):
            parts.append(summarize_accounts_overview(data["accounts"], industry_module))
        if data.get("inventory"):
            parts.append(summarize_inventory(data["inventory"], industry_module))
        return "\n\n".join(parts) if parts else "No data available for this query."

    return "Data context not available for this query type."
