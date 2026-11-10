"""Sales & CRM functions (read-only, record-scoped).

"Sales" here means **gross billed invoice totals** from QuickBooks (excluding
void/draft). That is not recognized revenue; credit memos and returns posted
separately are not netted. Every response says so.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.lola_bi.common import (
    Window, billed_totals, compare_totals, currency_context, dedupe, invoices_for, invoices_in,
    month_to_date, note_currency, pct_change, shift_year, trailing_windows, with_currency,
)
from app.lola_bi.contracts import Finding, ResultBuilder
from app.lola_bi.entities import resolve
from app.lola_bi.money import MoneyTotals

SALES_BASIS = "gross billed invoice totals per QuickBooks (excludes void/draft; not recognized revenue)"


def _home_amounts(invoices: list[dict], home: str | None, rb: ResultBuilder) -> list[dict]:
    kept = [i for i in invoices if home is not None and i["currency"] == home]
    if len(kept) != len(invoices):
        rb.warn("invoices in other or unknown currencies were excluded from per-account comparisons")
    return kept


def _by_customer(invoices: list[dict], window: Window) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for inv in invoices:
        if not window.contains(inv["date"]):
            continue
        c = out.setdefault(inv["customer_id"], {"name": inv["customer_name"], "total": Decimal("0"),
                                                "orders": 0, "last": None})
        c["total"] += inv["total"]
        c["orders"] += 1
        c["last"] = max(c["last"], inv["date"]) if c["last"] else inv["date"]
    return out


def _by_product(invoices: list[dict], window: Window) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for inv in invoices:
        if not window.contains(inv["date"]):
            continue
        for line in inv["lines"]:
            item = line.get("item_id")
            if not item:
                continue
            p = out.setdefault(item, {"name": line.get("name") or item, "units": Decimal("0"),
                                      "amount": Decimal("0")})
            p["units"] += line["quantity"]
            p["amount"] += line["amount"]
    return out


async def period_comparison(gw, rb: ResultBuilder, *, as_of: date) -> dict:
    ctx = await currency_context(gw, rb)
    mtd = month_to_date(as_of)
    ly = Window(shift_year(mtd.start, -1), shift_year(mtd.end, -1))
    pm_end = mtd.start - timedelta(days=1)
    pm = Window(pm_end.replace(day=1), min(pm_end, pm_end.replace(day=min(as_of.day, pm_end.day))))
    cur = await invoices_for(gw, rb, mtd, ctx)
    last_year = await invoices_for(gw, rb, ly, ctx, required=False)
    prior_month = await invoices_for(gw, rb, pm, ctx, required=False)
    note_currency(rb, ctx, cur)
    answer = {
        "basis": SALES_BASIS,
        "month_to_date": {**mtd.describe(), "billed": billed_totals(cur).describe(), "invoice_count": len(cur)},
        "vs_same_period_last_year": None if last_year is None else {
            **ly.describe(), **compare_totals(billed_totals(cur), billed_totals(last_year)),
            "invoice_count": len(last_year)},
        "vs_prior_month_same_days": None if prior_month is None else {
            **pm.describe(), **compare_totals(billed_totals(cur), billed_totals(prior_month)),
            "invoice_count": len(prior_month)},
    }
    return rb.finish(answer)


async def _two_windows(gw, rb, as_of: date, window_days: int):
    """Invoices for both windows in home currency, plus customers billed in other currencies."""
    ctx = await currency_context(gw, rb)
    current, prior = trailing_windows(as_of, window_days)
    rows = await invoices_for(gw, rb, Window(prior.start, current.end), ctx)
    home_rows = _home_amounts(rows, ctx.home_currency, rb)
    other = {i["customer_id"] for i in rows if i["currency"] != ctx.home_currency}
    return ctx, current, prior, home_rows, other


async def declining_accounts(gw, rb: ResultBuilder, *, as_of: date, window_days: int = 90,
                             min_decline_pct: Decimal = Decimal("20"), limit: int = 25) -> dict:
    ctx, current, prior, rows, _other = await _two_windows(gw, rb, as_of, window_days)
    cur, pri = _by_customer(rows, current), _by_customer(rows, prior)
    out = []
    for cid, p in pri.items():
        c = cur.get(cid, {"total": Decimal("0"), "orders": 0, "last": None})
        change = pct_change(c["total"], p["total"])
        if change is not None and change <= -min_decline_pct:
            out.append({"customer_id": cid, "customer_name": p["name"], "current": c["total"],
                        "prior": p["total"], "change_amount": c["total"] - p["total"], "change_pct": change,
                        "orders_current": c["orders"], "orders_prior": p["orders"],
                        "stopped_ordering": c["orders"] == 0,
                        "last_order_date": c["last"] or p["last"]})
    out.sort(key=lambda r: (r["change_amount"], r["customer_name"]))
    return rb.finish({"basis": SALES_BASIS, "currency": ctx.home_currency, "current_window": current.describe(),
                      "prior_window": prior.describe(), "min_decline_pct": min_decline_pct,
                      "declining_count": len(out), "accounts": out[:limit],
                      "lost_sales_total": sum((r["change_amount"] for r in out), Decimal("0"))})


async def underperforming_products(gw, rb: ResultBuilder, *, as_of: date, window_days: int = 90,
                                   min_decline_pct: Decimal = Decimal("25"), limit: int = 25) -> dict:
    ctx, current, prior, rows, _other = await _two_windows(gw, rb, as_of, window_days)
    catalog = {p["id"]: p for p in (rb.optional(await gw.products()) or ())}
    cur, pri = _by_product(rows, current), _by_product(rows, prior)
    out = []
    for pid in set(pri) | set(catalog):
        p = pri.get(pid, {"amount": Decimal("0"), "units": Decimal("0"),
                          "name": catalog.get(pid, {}).get("name", pid)})
        c = cur.get(pid, {"amount": Decimal("0"), "units": Decimal("0")})
        stock = catalog.get(pid, {}).get("qty_on_hand")
        change = pct_change(c["amount"], p["amount"])
        reason = None
        if change is not None and change <= -min_decline_pct:
            reason = "sales_declined"
        elif c["units"] <= 0 and stock is not None and stock > 0 and catalog.get(pid, {}).get("active", True):
            reason = "no_sales_with_stock_on_hand"
        if reason:
            out.append({"product_id": pid, "name": p["name"], "reason": reason, "current_amount": c["amount"],
                        "prior_amount": p["amount"], "change_pct": change, "units_current": c["units"],
                        "units_prior": p["units"], "qty_on_hand": stock})
    out.sort(key=lambda r: (r["current_amount"] - r["prior_amount"], r["name"]))
    if not catalog:
        rb.note_missing("qb.products", "unavailable", "stock-on-hand check skipped")
    return rb.finish({"basis": "invoice line amounts (" + SALES_BASIS + ")", "currency": ctx.home_currency,
                      "current_window": current.describe(), "prior_window": prior.describe(),
                      "products": out[:limit], "underperforming_count": len(out)})


async def rep_attention(gw, rb: ResultBuilder, *, as_of: date, window_days: int = 90) -> dict:
    gw.scope.require("sales.team")
    directory = rb.use(await gw.account_directory())
    ctx, current, prior, rows, other_currency = await _two_windows(gw, rb, as_of, window_days)
    open_inv = rb.optional(await gw.open_invoices())
    cur, pri = _by_customer(rows, current), _by_customer(rows, prior)
    overdue: dict[str, Decimal] = defaultdict(Decimal)
    if open_inv is not None:
        for inv in with_currency(dedupe(open_inv, label="invoice", rb=rb), ctx):
            if inv["currency"] == ctx.home_currency and inv.get("due_date") and inv["due_date"] < as_of:
                overdue[inv["customer_id"]] += inv["balance"]
    reps: dict[str, dict] = {}
    for acct in directory:
        rep = acct["assigned_rep_id"] or "unassigned"
        r = reps.setdefault(rep, {"rep_id": None if rep == "unassigned" else rep,
                                  "rep_name": acct.get("assigned_rep_name") or ("Unassigned" if rep == "unassigned" else None),
                                  "accounts": 0, "current": Decimal("0"), "prior": Decimal("0"),
                                  "declining_accounts": [], "inactive_accounts": 0,
                                  "accounts_excluded_other_currency": 0,
                                  "overdue_balance": Decimal("0") if open_inv is not None else None})
        cid = acct["customer_id"]
        r["accounts"] += 1
        if cid in other_currency:
            r["accounts_excluded_other_currency"] += 1
            continue
        c = cur.get(cid, {}).get("total", Decimal("0"))
        p = pri.get(cid, {}).get("total", Decimal("0"))
        r["current"] += c
        r["prior"] += p
        if p > 0 and (pct_change(c, p) or 0) <= -20:
            r["declining_accounts"].append(acct["name"] or cid)
        if c == 0:
            r["inactive_accounts"] += 1
        if r["overdue_balance"] is not None:
            r["overdue_balance"] += overdue.get(cid, Decimal("0"))
    out = []
    for r in reps.values():
        change = pct_change(r["current"], r["prior"])
        reasons = []
        if change is not None and change <= -10:
            reasons.append(f"book sales down {abs(change)}%")
        if r["declining_accounts"]:
            reasons.append(f"{len(r['declining_accounts'])} account(s) down 20%+")
        if r["overdue_balance"]:
            reasons.append("overdue balances in book")
        if r["accounts"] and r["inactive_accounts"] * 2 >= r["accounts"]:
            reasons.append("half or more of accounts had no orders this window")
        out.append({**r, "change_pct": change, "needs_attention": bool(reasons), "reasons": reasons})
    out.sort(key=lambda r: (-len(r["reasons"]), r["change_pct"] if r["change_pct"] is not None else 0))
    unmapped = set(cur) | set(pri)
    unmapped -= {a["customer_id"] for a in directory}
    if unmapped:
        rb.note_missing("account_assignments", "partial",
                        f"{len(unmapped)} customer(s) with sales have no assignment row")
    return rb.finish({"basis": SALES_BASIS, "currency": ctx.home_currency, "current_window": current.describe(),
                      "prior_window": prior.describe(), "reps": out})


async def territory_drivers(gw, rb: ResultBuilder, *, territory: str, as_of: date, window_days: int = 90) -> dict:
    directory = rb.use(await gw.account_directory())
    wanted = territory.strip().casefold()
    accounts = {a["customer_id"]: a for a in directory if (a.get("territory") or "").casefold() == wanted}
    if not accounts:
        known = sorted({a["territory"] for a in directory if a.get("territory")})
        return rb.finish({"territory": territory, "found": False, "known_territories": known}, status="partial")
    ctx, current, prior, rows, other_currency = await _two_windows(gw, rb, as_of, window_days)
    rows = [r for r in rows if r["customer_id"] in accounts]
    cur, pri = _by_customer(rows, current), _by_customer(rows, prior)
    acct_deltas, excluded = [], []
    for cid, a in accounts.items():
        if cid in other_currency:
            excluded.append({"customer_id": cid, "name": a["name"] or cid, "current": None, "prior": None,
                             "excluded_reason": "billed in another currency (no approved FX source)"})
            continue
        c = cur.get(cid, {}).get("total", Decimal("0"))
        p = pri.get(cid, {}).get("total", Decimal("0"))
        acct_deltas.append({"customer_id": cid, "name": a["name"] or cid, "current": c, "prior": p,
                            "change_amount": c - p, "change_pct": pct_change(c, p)})
    total_cur = sum((a["current"] for a in acct_deltas), Decimal("0"))
    total_pri = sum((a["prior"] for a in acct_deltas), Decimal("0"))
    delta = total_cur - total_pri
    reconciles = sum((a["change_amount"] for a in acct_deltas), Decimal("0")) == delta
    pc, pp = _by_product(rows, current), _by_product(rows, prior)
    prod_deltas = sorted(({"product_id": pid, "name": (pc.get(pid) or pp.get(pid))["name"],
                           "change_amount": pc.get(pid, {}).get("amount", Decimal("0")) - pp.get(pid, {}).get("amount", Decimal("0"))}
                          for pid in set(pc) | set(pp)), key=lambda r: r["change_amount"])
    acct_deltas.sort(key=lambda r: r["change_amount"])
    findings = [Finding(f"Territory billed sales changed by {delta} ({pct_change(total_cur, total_pri)}%)",
                        "verified", {"current": total_cur, "prior": total_pri}, ["qb.invoices", "db.account_directory"])]
    top_losers = [a for a in acct_deltas if a["change_amount"] < 0][:5]
    if delta < 0 and top_losers:
        share = sum((a["change_amount"] for a in top_losers), Decimal("0"))
        findings.append(Finding(f"{len(top_losers)} account(s) account for {share} of the change",
                                "verified", {"accounts": [a["name"] for a in top_losers]}, ["qb.invoices"]))
    catalog = {p["id"]: p for p in (rb.optional(await gw.products()) or ())}
    for prod in prod_deltas[:5]:
        stock = catalog.get(prod["product_id"], {}).get("qty_on_hand")
        if prod["change_amount"] < 0 and stock is not None and stock <= 0:
            findings.append(Finding(f"{prod['name']} declined and is out of stock now; a stock-out may have "
                                    "limited orders (historical availability not observed)", "hypothesis",
                                    {"product_id": prod["product_id"], "qty_on_hand": stock}, ["qb.products"]))
    return rb.finish({
        "territory": territory, "found": True, "basis": SALES_BASIS, "currency": ctx.home_currency,
        "current_window": current.describe(), "prior_window": prior.describe(),
        "current_total": total_cur, "prior_total": total_pri, "change_amount": delta,
        "change_pct": pct_change(total_cur, total_pri), "decomposition_reconciles": reconciles,
        "by_account": acct_deltas, "accounts_excluded": excluded, "by_product": prod_deltas[:15],
    }, findings=findings)


async def sales_projection(gw, rb: ResultBuilder, *, as_of: date) -> dict:
    ctx = await currency_context(gw, rb)
    mtd = month_to_date(as_of)
    trailing = Window(mtd.start - timedelta(days=90), mtd.start - timedelta(days=1))
    cur = _home_amounts(await invoices_for(gw, rb, mtd, ctx), ctx.home_currency, rb)
    hist = _home_amounts(await invoices_for(gw, rb, trailing, ctx), ctx.home_currency, rb)
    month_end = (mtd.start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    remaining = (month_end - as_of).days
    mtd_total = sum((i["total"] for i in cur), Decimal("0"))
    daily = (sum((i["total"] for i in hist), Decimal("0")) / trailing.days).quantize(Decimal("0.01"))
    ly = await invoices_for(gw, rb, Window(shift_year(mtd.start, -1), shift_year(month_end, -1)), ctx,
                            required=False)
    return rb.finish({
        "basis": SALES_BASIS, "currency": ctx.home_currency,
        "method": "run-rate projection: month-to-date + (trailing 90-day average daily billings x remaining days); "
                  "uncalibrated, ignores seasonality, open orders and pipeline",
        "month_to_date": mtd_total, "trailing_daily_average": daily, "remaining_days": remaining,
        "projected_month_total": mtd_total + daily * remaining,
        "same_month_last_year_actual": None if ly is None else
        sum((i["total"] for i in _home_amounts(ly, ctx.home_currency, rb)), Decimal("0")),
    })


async def customer_profile(gw, rb: ResultBuilder, *, customer: str, as_of: date) -> dict:
    ctx = await currency_context(gw, rb)
    customers = rb.use(await gw.customers())
    match = resolve(customer, customers, email_key="email")
    if match.status != "matched":
        return rb.finish({"match": match.describe()}, status="partial")
    cust = match.entity
    open_inv = with_currency([i for i in dedupe(rb.use(await gw.open_invoices()), label="invoice", rb=rb)
                              if i["customer_id"] == cust["id"]], ctx)
    current, prior = trailing_windows(as_of, 90)
    year = Window(as_of - timedelta(days=364), as_of)
    hist = [i for i in await invoices_for(gw, rb, year, ctx) if i["customer_id"] == cust["id"]]
    open_total, overdue = MoneyTotals(), MoneyTotals()
    for i in open_inv:
        open_total.add(i["currency"], i["balance"])
        if i.get("due_date") and i["due_date"] < as_of:
            overdue.add(i["currency"], i["balance"])
    products = _by_product(hist, year)
    top = sorted(products.values(), key=lambda p: -p["amount"])[:10]
    profile = {
        "customer": {"id": cust["id"], "name": cust["name"], "email": cust.get("email")},
        "match": match.describe(), "basis": SALES_BASIS,
        "open_balance": open_total.describe(), "overdue_balance": overdue.describe(),
        "open_invoices": len(open_inv),
        "last_90_days": compare_totals(billed_totals(invoices_in(hist, current)),
                                       billed_totals(invoices_in(hist, prior))),
        "last_12_months": billed_totals(hist).describe(), "orders_last_12_months": len(hist),
        "last_order_date": max((i["date"] for i in hist), default=None),
        "top_products_12_months": top,
    }
    directory = rb.optional(await gw.account_directory())
    if directory is not None:
        row = next((d for d in directory if d["customer_id"] == cust["id"]), None)
        profile["territory"] = row["territory"] if row else None
        profile["assigned_rep"] = (row.get("assigned_rep_name") or row.get("assigned_rep_id")) if row else None
    if gw.scope.has("comms.metadata") and cust.get("email"):
        mail = rb.optional(await gw.messages(query=f"from:{cust['email']}", max_results=25))
        profile["recent_messages_from_customer"] = None if mail is None else len(mail)
    return rb.finish(profile)


async def entity_lookup(gw, rb: ResultBuilder, *, query: str, kind: str = "customer") -> dict:
    if kind == "customer":
        match = resolve(query, rb.use(await gw.customers()), email_key="email")
    elif kind == "product":
        match = resolve(query, rb.use(await gw.products()), name_keys=("name", "full_name", "sku"))
    elif kind == "vendor":
        bills = rb.use(await gw.open_bills())
        vendors = {b["vendor_id"]: {"id": b["vendor_id"], "name": b["vendor_name"]} for b in bills}
        match = resolve(query, vendors.values())
        rb.warn("vendor lookup only covers vendors with open bills")
    else:
        raise ValueError("kind must be customer, product or vendor")
    return rb.finish({"kind": kind, "query": query, "match": match.describe()})

