"""Franklin — Finance / CPA functions (read-only, deterministic)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Iterable

from app.lola_bi.common import (
    HUNDRED, Window, currency_context, dedupe, invoices_for, note_currency, pct_change,
    with_currency,
)
from app.lola_bi.contracts import Finding, ResultBuilder, SourceUnavailable
from app.lola_bi.money import FxRate, MoneyTotals

CASH_FLOW_EXCLUSIONS = [
    "payroll and payroll taxes", "sales/income taxes", "rent, utilities and other unbilled expenses",
    "new sales not yet invoiced", "credit lines and loan payments",
    "probability or timing of customer collection (receipts assumed on due date)",
]


async def cash_position(gw, rb: ResultBuilder, *, fx_rates: Iterable[FxRate] = ()) -> dict:
    ctx = await currency_context(gw, rb)
    accounts = with_currency(rb.use(await gw.bank_accounts()), ctx)
    note_currency(rb, ctx, accounts)
    totals = MoneyTotals()
    for acct in accounts:
        totals.add(acct["currency"], acct["balance"])
    overdrawn = [a for a in accounts if a["balance"] < 0]
    if not accounts:
        rb.warn("no active bank accounts are recorded in QuickBooks")
    answer = {
        "basis": "QuickBooks book balances of active bank accounts (not bank-statement balances; "
                 "uncleared/unrecorded items are not reflected)",
        "accounts": [{"id": a["id"], "name": a["name"], "balance": a["balance"], "currency": a["currency"]}
                     for a in sorted(accounts, key=lambda a: a["name"])],
        "cash": totals.describe(fx_rates, ctx.home_currency),
        "overdrawn_accounts": [a["name"] for a in overdrawn],
    }
    return rb.finish(answer)


async def profit_and_loss(gw, rb: ResultBuilder, *, start: date, end: date, compare_prior: bool = True) -> dict:
    window = Window(start, end)
    current = rb.use(await gw.profit_and_loss(start, end))[0]
    checks = _pnl_checks(current)
    for check in checks:
        if not check["consistent"]:
            rb.warn(f"P&L report subtotal does not reconcile: {check['name']}")
    prior = None
    if compare_prior:
        p_end = start - timedelta(days=1)
        p_start = p_end - timedelta(days=window.days - 1)
        rows = rb.optional(await gw.profit_and_loss(p_start, p_end))
        prior = rows[0] if rows else None
    lines = ("income", "cost_of_goods_sold", "gross_profit", "expenses", "net_operating_income",
             "other_income", "other_expenses", "net_income")
    answer = {
        "period": window.describe(), "basis": current.get("basis"), "currency": current.get("currency"),
        "lines": {k: current.get(k) for k in lines},
        "gross_margin_pct": _ratio(current.get("gross_profit"), current.get("income")),
        "net_margin_pct": _ratio(current.get("net_income"), current.get("income")),
        "subtotal_checks": checks,
        "prior_period": None if prior is None else {
            "period": {"from": prior["start"], "to": prior["end"]},
            "lines": {k: prior.get(k) for k in lines},
            "change_pct": {k: pct_change(current.get(k), prior.get(k)) for k in lines},
        },
        "source_note": "QuickBooks Profit and Loss report totals as reported by the accounting system",
    }
    return rb.finish(answer)


def _ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == 0:
        return None
    return (num / den * HUNDRED).quantize(Decimal("0.1"))


def _pnl_checks(p: dict) -> list[dict]:
    checks = []

    def check(name: str, expected: Decimal | None, parts: list[Decimal | None]) -> None:
        if expected is None or any(x is None for x in parts):
            return
        calc = parts[0] - sum(parts[1:], Decimal("0"))
        checks.append({"name": name, "reported": expected, "calculated": calc, "consistent": calc == expected})

    check("gross_profit = income - cost_of_goods_sold", p.get("gross_profit"),
          [p.get("income"), p.get("cost_of_goods_sold")])
    check("net_operating_income = gross_profit - expenses", p.get("net_operating_income"),
          [p.get("gross_profit"), p.get("expenses")])
    if p.get("net_operating_income") is not None and p.get("net_income") is not None:
        other = p.get("net_other_income") if p.get("net_other_income") is not None else Decimal("0")
        calc = p["net_operating_income"] + other
        checks.append({"name": "net_income = net_operating_income + net_other_income",
                       "reported": p["net_income"], "calculated": calc, "consistent": calc == p["net_income"]})
    return checks


async def cash_flow_projection(gw, rb: ResultBuilder, *, as_of: date, weeks: int = 13) -> dict:
    """Contractual due-date schedule. Explicitly NOT a forecast."""
    if not 1 <= weeks <= 13:
        raise ValueError("weeks must be between 1 and 13")
    if not gw.scope.company_wide:
        raise SourceUnavailable("qb.open_invoices", "denied", "company-wide receivables required")
    ctx = await currency_context(gw, rb)
    if ctx.home_currency is None:
        raise SourceUnavailable("qb.company_currency", "unverified", "home currency unknown")
    home = ctx.home_currency
    banks = with_currency(rb.use(await gw.bank_accounts()), ctx)
    receivables = with_currency(dedupe(rb.use(await gw.open_invoices()), label="invoice", rb=rb), ctx)
    payables = with_currency(dedupe(rb.use(await gw.open_bills()), label="bill", rb=rb), ctx)

    excluded = MoneyTotals()
    for row, field_name in [(b, "balance") for b in banks] + [(r, "balance") for r in receivables] + \
            [(p, "balance") for p in payables]:
        if row["currency"] != home:
            excluded.add(row["currency"], row[field_name])
    if excluded.currencies:
        rb.warn("non-home-currency balances are excluded from the schedule (no approved FX source)")

    start_cash = sum((b["balance"] for b in banks if b["currency"] == home), Decimal("0"))
    weeks_out = []
    horizon_end = as_of + timedelta(days=7 * weeks - 1)
    overdue_ar = overdue_ap = undated_ar = undated_ap = beyond_ar = beyond_ap = Decimal("0")
    week_in = [Decimal("0")] * weeks
    week_out = [Decimal("0")] * weeks
    for inv in receivables:
        if inv["currency"] != home:
            continue
        due = inv.get("due_date")
        if due is None:
            undated_ar += inv["balance"]
        elif due < as_of:
            overdue_ar += inv["balance"]
        elif due > horizon_end:
            beyond_ar += inv["balance"]
        else:
            week_in[(due - as_of).days // 7] += inv["balance"]
    for bill in payables:
        if bill["currency"] != home:
            continue
        due = bill.get("due_date")
        if due is None:
            undated_ap += bill["balance"]
        elif due < as_of:
            overdue_ap += bill["balance"]
        elif due > horizon_end:
            beyond_ap += bill["balance"]
        else:
            week_out[(due - as_of).days // 7] += bill["balance"]

    running = start_cash
    for k in range(weeks):
        opening = running
        running = opening + week_in[k] - week_out[k]
        weeks_out.append({
            "week": k + 1, "from": as_of + timedelta(days=7 * k), "to": as_of + timedelta(days=7 * k + 6),
            "opening": opening, "receivables_due": week_in[k], "payables_due": week_out[k], "closing": running,
        })
    lowest = min(weeks_out, key=lambda w: w["closing"])
    answer = {
        "method": "contractual_due_date_schedule",
        "not_a_forecast": True,
        "currency": home,
        "starting_cash": start_cash,
        "starting_cash_basis": "QuickBooks bank book balances",
        "weeks": weeks_out,
        "ending_cash": running,
        "lowest_week": {"week": lowest["week"], "closing": lowest["closing"]},
        "outside_schedule": {
            "overdue_receivables_timing_unknown": overdue_ar,
            "overdue_payables_already_due": overdue_ap,
            "receivables_without_due_date": undated_ar,
            "payables_without_due_date": undated_ap,
            "receivables_due_after_horizon": beyond_ar,
            "payables_due_after_horizon": beyond_ap,
        },
        "excluded_other_currencies": excluded.by_currency(),
        "not_included": CASH_FLOW_EXCLUSIONS,
    }
    rb.note_missing("cash_flow_inputs", "not_connected",
                    "payroll, tax, recurring expenses and collection history are not connected sources")
    return rb.finish(answer, status="partial")


async def profitability_by_brand(gw, rb: ResultBuilder, *, start: date, end: date) -> dict:
    ctx = await currency_context(gw, rb)
    invoices = await invoices_for(gw, rb, Window(start, end), ctx)
    products = {p["id"]: p for p in rb.use(await gw.products())}
    note_currency(rb, ctx, invoices)
    brands: dict[str, dict] = defaultdict(lambda: {
        "revenue": Decimal("0"), "revenue_with_cost": Decimal("0"), "estimated_cost": Decimal("0"),
        "units": Decimal("0"), "lines_without_cost": 0, "products": set(),
    })
    skipped_currency = 0
    not_in_catalog = 0
    for inv in invoices:
        if ctx.home_currency is None or inv["currency"] != ctx.home_currency:
            skipped_currency += len(inv["lines"])
            continue
        for line in inv["lines"]:
            product = products.get(line.get("item_id"))
            if product is None:
                not_in_catalog += 1
                continue
            label = product.get("brand") or "Unassigned"
            entry = brands[label]
            entry["revenue"] += line["amount"]
            entry["units"] += line["quantity"]
            entry["products"].add(product["id"])
            if product.get("cost") is None:
                entry["lines_without_cost"] += 1
                continue
            entry["revenue_with_cost"] += line["amount"]
            entry["estimated_cost"] += line["quantity"] * product["cost"]
    rows = []
    for brand, e in brands.items():
        gp = e["revenue_with_cost"] - e["estimated_cost"]
        rows.append({
            "brand": brand, "revenue": e["revenue"], "units": e["units"], "product_count": len(e["products"]),
            "estimated_gross_profit": gp if e["revenue_with_cost"] else None,
            "estimated_margin_pct": _ratio(gp, e["revenue_with_cost"]),
            "revenue_without_cost_data": e["revenue"] - e["revenue_with_cost"],
            "lines_without_cost": e["lines_without_cost"],
        })
    rows.sort(key=lambda r: (r["estimated_gross_profit"] is None, -(r["estimated_gross_profit"] or 0)))
    if skipped_currency:
        rb.warn("invoice lines in a non-home or unknown currency were excluded")
    if not_in_catalog:
        rb.warn("some invoice lines reference items outside the inventory catalog (services, non-stock)")
    if any(r["brand"] == "Unassigned" for r in rows):
        rb.note_missing("brand_mapping", "partial",
                        "items without a 'Brand:Item' QuickBooks hierarchy are grouped as Unassigned")
    answer = {
        "period": Window(start, end).describe(), "currency": ctx.home_currency,
        "method": "invoice line amounts minus quantity x current QuickBooks item cost "
                  "(estimate; not COGS at time of sale, excludes billbacks/freight/discounts at header level)",
        "brands": rows,
        "excluded_lines": {"non_home_currency": skipped_currency, "not_in_catalog": not_in_catalog},
    }
    return rb.finish(answer)


async def financial_anomalies(gw, rb: ResultBuilder, *, as_of: date, lookback_days: int = 365) -> dict:
    ctx = await currency_context(gw, rb)
    findings: list[Finding] = []
    open_inv = rb.optional(await gw.open_invoices())
    if open_inv is not None:
        open_inv = with_currency(dedupe(open_inv, label="invoice", rb=rb), ctx)
        for group in _duplicate_groups(open_inv, ("customer_id", "doc_number")):
            findings.append(Finding(
                "Open invoices share a customer and invoice number", "verified",
                {"invoice_ids": [g["id"] for g in group], "doc_number": group[0]["doc_number"]},
                ["qb.open_invoices"]))
    customers = rb.optional(await gw.customers())
    for cust in customers or ():
        if cust["balance"] < 0:
            findings.append(Finding(
                "Customer carries a credit balance (unapplied credit or overpayment)", "verified",
                {"customer_id": cust["id"], "name": cust["name"], "balance": cust["balance"]}, ["qb.customers"]))
    banks = rb.optional(await gw.bank_accounts())
    for bank in banks or ():
        if bank["balance"] < 0:
            findings.append(Finding("Bank account book balance is negative", "verified",
                                    {"account": bank["name"], "balance": bank["balance"]}, ["qb.bank_accounts"]))
    bills = rb.optional(await gw.open_bills())
    if bills is not None:
        from app.lola_bi.payables import find_duplicate_bills
        for dup in find_duplicate_bills(dedupe(bills, label="bill", rb=rb)):
            findings.append(Finding(f"Possible duplicate vendor bill ({dup['match']})",
                                    "verified" if dup["match"] == "same_vendor_and_number" else "hypothesis",
                                    dup, ["qb.open_bills"]))
    invoices = await invoices_for(gw, rb, Window(as_of - timedelta(days=lookback_days - 1), as_of), ctx,
                                  required=False)
    if invoices is not None:
        per_customer: dict[str, list[dict]] = defaultdict(list)
        for inv in invoices:
            per_customer[inv["customer_id"]].append(inv)
        for cid, rows in per_customer.items():
            if len(rows) < 5:
                continue
            typical = Decimal(str(median([r["total"] for r in rows])))
            for r in rows:
                if typical > 0 and r["total"] > typical * 3:
                    findings.append(Finding(
                        "Invoice is more than 3x the customer's median invoice", "hypothesis",
                        {"invoice_id": r["id"], "customer_id": cid, "total": r["total"],
                         "customer_median": typical}, ["qb.invoices"]))
    answer = {"as_of": as_of, "checks_run": [
        "duplicate open invoice numbers", "customer credit balances", "negative bank balances",
        "duplicate vendor bills", "invoice amount outliers (>3x customer median)"],
        "anomaly_count": len(findings)}
    return rb.finish(answer, findings=findings)


def _duplicate_groups(rows: list[dict], keys: tuple[str, ...]) -> list[list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        values = tuple(row.get(k) for k in keys)
        if all(isinstance(v, str) and v.strip() for v in values):
            groups[tuple(v.strip().lower() for v in values)].append(row)
    return [g for g in groups.values() if len(g) > 1]

