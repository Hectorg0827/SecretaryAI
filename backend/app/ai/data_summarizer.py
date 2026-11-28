"""
Data summarizer — prepares business data into compact summaries for the AI.
The AI never receives raw database rows or financial records.

All terminology (unit names, entity labels, status labels) is pulled from the
active IndustryModule so output automatically uses the correct industry language.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional


def _format_money(value: object) -> str:
    """Format a numeric monetary amount using exact Decimal rounding."""
    if value is None or isinstance(value, bool):
        raise ValueError("Monetary value is missing or invalid")
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Monetary value is invalid") from exc
    if not dec.is_finite():
        raise ValueError("Monetary value is not finite")
    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{quantized:,.2f}"


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
    unassessed = [i for i in inventory_rows if i.get("stock_status") == "unassessed"]

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

    for item in unassessed:
        lines.append(
            f"  [UNASSESSED] {item['product_name']}: {item['total_qty']} {unit}; "
            "weekly sell-through was not verified, so stock coverage is unknown"
        )
    lines.append(f"  {len(healthy)} products at verified healthy stock levels.")
    if unassessed:
        lines.append(f"  {len(unassessed)} product(s) could not be assessed; do not claim inventory is healthy.")
    return chr(10).join(lines)


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
        f"  Current account balance: ${_format_money(balance)}" if balance is not None else
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
            f"  Last {days} days: ${_format_money(cur['revenue'])} gross billed / {cur['orders']} invoices; "
            f"previous {days} days: ${_format_money(prior['revenue'])} / {prior['orders']} invoices."
        )
        if trend.get("change_pct") is not None:
            lines.append(f"  {days}-day billed amount change: {trend['change_pct']:+.1f}%.")
        else:
            lines.append(f"  {days}-day percentage change unavailable (no positive prior-period billed amount).")
    if recent_orders:
        lines.append(f"  Most recent invoiced purchases ({len(recent_orders)}):")
        for order in reversed(recent_orders[-5:]):
            lines.append(
                f"    {order['order_date']}: ${_format_money(order['total_amount'])}"
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


def summarize_receivables_aging(
    aging_report: dict,
    collections_summary: Optional[dict] = None,
) -> str:
    """Summarize aged receivables and deterministic collection priorities with provenance."""
    if not isinstance(aging_report, dict) or not aging_report:
        return (
            "Receivables aging source unavailable (not reconciled to payments or GL). "
            "Do not invent open balances or claim accounts are current."
        )

    source_as_of = aging_report.get("source_as_of") or "unknown"
    observed_at = aging_report.get("source_observed_at") or "unknown"
    currency = aging_report.get("currency") or "unverified"
    reconciliation = aging_report.get(
        "reconciliation_status", "not_reconciled_to_payments_or_gl"
    )
    status = aging_report.get("status", "unavailable")
    exceptions = aging_report.get("exceptions") or []

    lines = [
        f"Source: aged invoice snapshot (as_of={aging_report.get('as_of', 'unknown')}, "
        f"source_as_of={source_as_of}, observed_at={observed_at}, currency={currency}); "
        f"status={status}; reconciliation={reconciliation} "
        "(NOT recognized revenue and NOT GL-reconciled AR)."
    ]

    buckets = aging_report.get("buckets")
    if status == "unavailable" or not isinstance(buckets, dict):
        exc_text = ", ".join(str(e) for e in exceptions) if exceptions else "unknown"
        lines.append(
            f"  [UNAVAILABLE] Receivables aging cannot be quantified ({exc_text}). "
            "Do not invent balances, aging buckets, or claim accounts are current."
        )
        return "\n".join(lines)

    total_open = aging_report.get("total_open", Decimal("0"))
    lines.append(
        f"  Total open invoice balance ({currency}): ${_format_money(total_open)} "
        f"across {aging_report.get('invoice_count', 0)} open invoices "
        f"({aging_report.get('excluded_count', 0)} paid/void excluded)."
    )
    bucket_labels = (
        ("current", "Current"),
        ("days_1_30", "1-30d overdue"),
        ("days_31_60", "31-60d overdue"),
        ("days_61_90", "61-90d overdue"),
        ("days_91_plus", "91+d overdue"),
        ("unaged", "Unaged (missing due date)"),
    )
    for key, label in bucket_labels:
        lines.append(f"  {label}: ${_format_money(buckets.get(key, Decimal('0')))}")
    if exceptions:
        lines.append("  Aging exceptions: " + ", ".join(str(e) for e in exceptions) + ".")

    if isinstance(collections_summary, dict) and collections_summary:
        c_status = collections_summary.get("status", "unavailable")
        priorities = collections_summary.get("priorities") or []
        totals = collections_summary.get("totals") or {}
        if c_status == "unavailable" or not totals:
            lines.append("  Collections prioritization: UNAVAILABLE; human review required.")
        else:
            lines.append(
                f"  Collections totals: collectible overdue=${_format_money(totals.get('total_collectible_overdue', Decimal('0')))}, "
                f"disputed holdback=${_format_money(totals.get('total_disputed', Decimal('0')))}."
            )
            for row in priorities[:10]:
                parts = [
                    f"    [{str(row.get('priority', 'monitor')).upper()}] {row.get('customer_name', row.get('customer_id'))}",
                    f"open=${_format_money(row.get('total_open', Decimal('0')))}",
                    f"collectible_overdue=${_format_money(row.get('collectible_overdue_amount', Decimal('0')))}",
                ]
                disputed = row.get("disputed_amount") or Decimal("0")
                if Decimal(str(disputed)) > 0:
                    parts.append(f"disputed_holdback=${_format_money(disputed)}")
                util = row.get("credit_utilization_pct")
                if util is not None:
                    limit_tag = " OVER LIMIT" if row.get("over_credit_limit") else ""
                    parts.append(f"credit_util={util}%{limit_tag}")
                promise = row.get("promise_status")
                if promise and promise != "none":
                    parts.append(f"promise={promise}")
                owner = row.get("responsible_employee")
                if owner:
                    parts.append(f"owner={owner}")
                parts.append(f"action={row.get('recommended_action', 'review')}")
                lines.append(": ".join([parts[0], ", ".join(parts[1:])]))
            lines.append(
                "  Action guardrail: external collection emails or calls require human approval "
                "(DRAFT AND WAIT); disputed invoices remain held from automated dunning."
            )
    return "\n".join(lines)


def build_query_context(intent: str, data: dict, industry_module=None) -> str:
    """
    Route intent to appropriate summarizer and return context string.
    industry_module: optional, used to apply correct terminology.
    """
    if intent == "compliance_check":
        compliance = data.get("compliance")
        if not isinstance(compliance, dict):
            return "Compliance source unavailable. Do not infer regulatory authorization."
        counts = compliance.get("counts") or {}
        lines = [
            "Source: company-scoped Lion BI compliance registry; records are not independently verified with regulators.",
            "Registry setup: " + ("complete per saved records" if compliance.get("is_setup") else
                                  "INCOMPLETE — regulatory approval or clearance cannot be confirmed"),
            "Recorded inventory: "
            + ", ".join(f"{name}={counts.get(name, 'unknown')}" for name in (
                "products_count", "licenses_count", "federal_permits_count",
                "distributors_count", "brand_registrations_count", "colas_count"
            )),
            f"Registry status: {compliance.get('overall_status', 'unverified')}. "
            "This is not proof of current license or regulator approval.",
            f"Current compliance alerts recorded: {compliance.get('alerts_count', 'unknown')}.",
        ]
        missing = compliance.get("missing") or []
        if missing:
            lines.append("Missing required setup records: " + ", ".join(missing) + ".")
        for alert in compliance.get("alerts", [])[:10]:
            lines.append(
                f"  [ALERT] {alert.get('priority', 'unknown')}: "
                f"{alert.get('item_name', '')} ({alert.get('state_code', '')})"
            )
        lines.append(
            "Do not say the business, its products, or a shipment is licensed, "
            "approved, or compliant without complete authoritative evidence."
        )
        return chr(10).join(lines)

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

    if intent in ("receivables_aging", "collections_priority"):
        return summarize_receivables_aging(
            data.get("receivables_aging") or {},
            data.get("collections_summary"),
        )

    if intent in ("sales_report", "general_question"):
        parts = []
        sales = data.get("sales_summary")
        if sales:
            parts.append(
                f"Verified gross billed invoices ({sales['period_start']} to {sales['period_end']}): "
                f"${_format_money(sales['gross_billed_amount'])} across {sales['order_count']} billable invoices. "
                "Source: QuickBooks invoice headers. This is gross billed amount, NOT recognized revenue; "
                "SKU/product sales breakdown is unavailable with header-only data."
            )
            if sales.get("top_accounts"):
                parts.append("Top billed accounts in this period: " +
                    "; ".join(f"{a['name']}: ${_format_money(a['revenue'])} ({a['order_count']} invoices)"
                              for a in sales["top_accounts"]))
        if data.get("receivables_aging"):
            parts.append(
                summarize_receivables_aging(
                    data["receivables_aging"],
                    data.get("collections_summary"),
                )
            )
        if data.get("accounts"):
            parts.append(summarize_accounts_overview(data["accounts"], industry_module))
        if data.get("inventory"):
            parts.append(summarize_inventory(data["inventory"], industry_module))
        if data.get("inventory_unavailable"):
            parts.append("Inventory source is unavailable; do not infer inventory or sell-through.")
        return "\n\n".join(parts) if parts else "No data available for this query."

    return "Data context not available for this query type."
