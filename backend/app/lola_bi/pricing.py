"""Raul — Pricing / Billbacks functions (read-only, cost-visible roles only)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median

from app.lola_bi.common import HUNDRED, Window, currency_context, invoices_for
from app.lola_bi.contracts import Finding, ResultBuilder


def _unit_price(line: dict) -> Decimal | None:
    qty = line.get("quantity")
    if line.get("unit_price") is not None:
        return line["unit_price"]
    if qty:
        return (line["amount"] / qty).quantize(Decimal("0.0001"))
    return None


async def _priced_lines(gw, rb: ResultBuilder, start: date, end: date):
    ctx = await currency_context(gw, rb)
    invoices = await invoices_for(gw, rb, Window(start, end), ctx)
    products = {p["id"]: p for p in rb.use(await gw.products())}
    lines, skipped = [], 0
    for inv in invoices:
        if ctx.home_currency is None or inv["currency"] != ctx.home_currency:
            skipped += len(inv["lines"])
            continue
        for line in inv["lines"]:
            product = products.get(line.get("item_id"))
            if product is None or line["quantity"] <= 0:
                continue  # services/returns are not priced sales of a catalog item
            lines.append({"invoice_id": inv["id"], "customer_id": inv["customer_id"],
                          "customer_name": inv["customer_name"], "date": inv["date"],
                          "product": product, "quantity": line["quantity"], "amount": line["amount"],
                          "unit_price": _unit_price(line)})
    if skipped:
        rb.warn("lines in a non-home or unknown currency were excluded")
    return ctx, lines


async def product_margins(gw, rb: ResultBuilder, *, start: date, end: date, top_n: int = 15) -> dict:
    if not 1 <= top_n <= 100:
        raise ValueError("top_n must be between 1 and 100")
    ctx, lines = await _priced_lines(gw, rb, start, end)
    agg: dict[str, dict] = {}
    for ln in lines:
        p = ln["product"]
        a = agg.setdefault(p["id"], {"product_id": p["id"], "name": p["name"], "brand": p.get("brand"),
                                     "units": Decimal("0"), "revenue": Decimal("0"),
                                     "current_unit_cost": p.get("cost")})
        a["units"] += ln["quantity"]
        a["revenue"] += ln["amount"]
    rows, no_cost = [], 0
    for a in agg.values():
        cost = a["current_unit_cost"]
        avg_price = (a["revenue"] / a["units"]).quantize(Decimal("0.0001")) if a["units"] else None
        if cost is None:
            no_cost += 1
            gp = margin = None
        else:
            gp = a["revenue"] - a["units"] * cost
            margin = (gp / a["revenue"] * HUNDRED).quantize(Decimal("0.1")) if a["revenue"] else None
        rows.append({**a, "avg_net_price": avg_price, "estimated_gross_profit": gp,
                     "estimated_margin_pct": margin})
    ranked = sorted((r for r in rows if r["estimated_margin_pct"] is not None),
                    key=lambda r: (-r["estimated_margin_pct"], r["name"]))
    if no_cost:
        rb.note_missing("item_cost", "missing", f"{no_cost} product(s) have no purchase cost in QuickBooks")
    return rb.finish({
        "period": Window(start, end).describe(), "currency": ctx.home_currency,
        "method": "line revenue minus units x current QuickBooks purchase cost (contribution before "
                  "billbacks, freight and promotions, which are not connected)",
        "highest_margin": ranked[:top_n], "lowest_margin": list(reversed(ranked[-top_n:])),
        "products_without_cost": [r["name"] for r in rows if r["current_unit_cost"] is None][:50],
    })


async def price_discrepancies(gw, rb: ResultBuilder, *, start: date, end: date,
                              tolerance_pct: Decimal = Decimal("10")) -> dict:
    if not Decimal("0") < tolerance_pct <= Decimal("100"):
        raise ValueError("tolerance_pct must be within (0, 100]")
    _, lines = await _priced_lines(gw, rb, start, end)
    by_product: dict[str, list[dict]] = defaultdict(list)
    for ln in lines:
        if ln["unit_price"] is not None:
            by_product[ln["product"]["id"]].append(ln)
    findings: list[Finding] = []
    below_cost, off_median, off_list = [], [], []
    for pid, group in by_product.items():
        product = group[0]["product"]
        typical = Decimal(str(median([g["unit_price"] for g in group])))
        for g in group:
            ref = {"invoice_id": g["invoice_id"], "customer": g["customer_name"], "product": product["name"],
                   "unit_price": g["unit_price"], "date": g["date"]}
            cost = product.get("cost")
            if cost is not None and g["unit_price"] < cost:
                below_cost.append({**ref, "current_unit_cost": cost})
            if len(group) >= 3 and typical > 0 and abs(g["unit_price"] - typical) / typical * HUNDRED > tolerance_pct:
                off_median.append({**ref, "median_price": typical})
            lp = product.get("list_price")
            if lp and abs(g["unit_price"] - lp) / lp * HUNDRED > tolerance_pct:
                off_list.append({**ref, "list_price": lp})
    for row in below_cost:
        findings.append(Finding("Sold below current unit cost", "verified", row, ["qb.invoices", "qb.products"]))
    return rb.finish({
        "period": Window(start, end).describe(), "tolerance_pct": tolerance_pct,
        "sold_below_current_cost": below_cost[:100],
        "deviates_from_median_price": off_median[:100],
        "deviates_from_list_price": off_list[:100],
        "note": "price deviations may reflect approved customer-specific pricing; "
                "customer price lists and contracts are not connected",
    }, findings=findings)


async def billbacks_and_promotions(gw, rb: ResultBuilder) -> dict:
    """No supplier billback or promotion source exists yet — say so, never estimate."""
    rb.note_missing("supplier_billbacks", "not_connected",
                    "no supplier billback/chargeback agreements or claims source is connected")
    rb.note_missing("promotions", "not_connected",
                    "no promotion calendar, deal terms or depletion allowance source is connected")
    return rb.finish({
        "available": False,
        "what_is_needed": [
            "supplier billback agreements (rate per case, eligible SKUs/accounts, dates)",
            "promotion calendar with deal terms",
            "claim submissions and supplier credit memos (read from QuickBooks vendor credits)",
        ],
        "related_tools_available": ["pricing.product_margins", "pricing.price_discrepancies",
                                    "the existing /api/insights/price-waterfall what-if calculator"],
    }, status="unavailable")
