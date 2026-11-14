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
    """Explain verified account metrics, not a speculative reason for slowdown."""
    terms = _get_terminology(industry_module)
    name = account.get("name", "Unknown")
    status = account.get("health_status", "unknown")
    score = account.get("health_score")
    last_order = account.get("last_order_date")
    balance = account.get("current_balance")
    velocity = account.get("velocity") or {}
    cycle = velocity.get("avg_days_between_orders")
    lines = [
        f"{terms.get('account', 'account').capitalize()}: {name}",
        "Source: QuickBooks verified invoice headers; last 365 days (not lifetime history).",
        f"  Account health: {status.upper()} (score: {score}/100)" if score is not None else
        "  Account health: UNKNOWN (not enough verified data to score)",
        f"  Last invoiced purchase: {last_order}" if last_order else
        "  Last invoiced purchase: not observed in the 365-day window",
        f"  Average observed days between invoices: {cycle}" if cycle is not None else
        "  Average observed days between invoices: insufficient history",
        f"  Current account balance: ${float(balance):,.2f}" if balance is not None else
        "  Current account balance: unavailable",
        f"  Billable invoices observed (365d): {account.get('invoice_count_365d', 'unknown')}",
    ]
    flags = account.get("health_flags") or []
    if flags:
        lines.append("  Health-scoring evidence: " + "; ".join(flags))
    for days in (30, 90):
        trend = account.get(f"trend_{days}d")
        if trend is None:
            continue
        cur, prior = trend["current"], trend["prior"]
        lines.append(
            f"  Last {days} days: ${cur['revenue']:,.2f} gross billed / {cur['orders']} invoices; "
            f"previous {days} days: ${prior['revenue']:,.2f} / {prior['orders']} invoices."
        )
        if trend.get("change_pct") is not None:
            lines.append(f"  {days}-day billed amount change: {trend['change_pct']:+.1f}%.")
        else:
            lines.append(f"  {days}-day percentage change unavailable (no positive prior-period billed amount).")
    if recent_orders:
        lines.append(f"  Most recent invoiced purchases ({len(recent_orders)}):")
        for order in reversed(recent_orders[-5:]):
            lines.append(
                f"    {order['order_date']}: ${float(order['total_amount']):,.2f}"
            )
    lines.append(
        "Interpretation: health classification is a scoring heuristic, not proof "
        "of an underlying cause. Invoice data does not establish sell-through, "
        "depletion rates, SKU breadth, competitor activity, or a credit hold. "
        "Do not attribute a slowdown to any of these without additional authorized evidence."
    )
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
        f"  Unknown / unscored: {by_status.get('unknown', 0)}",
    ]

    at_risk = [a for a in accounts if a.get("health_status") in ("slowing", "at_risk", "dormant")]
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
        ambiguous = data.get("ambiguous_accounts")
        if ambiguous:
            return ("Multiple accounts match that name: " + ", ".join(ambiguous) +
                    ". Ask which account the user means. Do not select one or invent sales data.")
        account = data.get("account")
        if account:
            return summarize_account(account, data.get("orders", []), industry_module)
        return summarize_accounts_overview(data.get("accounts", []), industry_module)

    if intent in ("sales_report", "general_question"):
        parts = []
        sales = data.get("sales_summary")
        if sales:
            parts.append(
                f"Verified gross billed invoices ({sales['period_start']} to {sales['period_end']}): "
                f"${sales['gross_billed_amount']:,.2f} across {sales['order_count']} billable invoices. "
                "Source: QuickBooks invoice headers. This is gross billed amount, NOT recognized revenue; "
                "SKU/product sales breakdown is unavailable with header-only data."
            )
            if sales.get("top_accounts"):
                parts.append("Top billed accounts in this period: " +
                    "; ".join(f"{a['name']}: ${a['revenue']:,.2f} ({a['order_count']} invoices)"
                              for a in sales["top_accounts"]))
        if data.get("accounts"):
            parts.append(summarize_accounts_overview(data["accounts"], industry_module))
        if data.get("inventory"):
            parts.append(summarize_inventory(data["inventory"], industry_module))
        if data.get("inventory_unavailable"):
            parts.append("Inventory source is unavailable; do not infer inventory or sell-through.")
        return "\n\n".join(parts) if parts else "No data available for this query."

    return "Data context not available for this query type."
