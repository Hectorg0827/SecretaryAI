"""Cross-system investigation: "why is this account ordering less?"

Combines sales history, product mix, current stock, prices paid, open
balances and correspondence metadata. Output keeps two lists:

* ``verified`` – deterministic facts computed from named sources.
* ``hypotheses`` – plausible explanations those facts are *consistent with*.
  None is claimed as a cause: correlation in a few months of invoices does not
  identify causation, current stock does not show past availability, and
  message counts say nothing about message content.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.lola_bi.common import (
    HUNDRED, currency_context, dedupe, invoices_for, pct_change, trailing_windows, Window, with_currency,
)
from app.lola_bi.contracts import Finding, ResultBuilder
from app.lola_bi.entities import resolve


def _avg_prices(invoices: list[dict], window: Window) -> dict[str, dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"units": Decimal("0"), "amount": Decimal("0"), "name": ""})
    for inv in invoices:
        if not window.contains(inv["date"]):
            continue
        for line in inv["lines"]:
            if line.get("item_id") and line["quantity"] > 0:
                a = agg[line["item_id"]]
                a["units"] += line["quantity"]
                a["amount"] += line["amount"]
                a["name"] = line.get("name") or line["item_id"]
    return agg


async def account_decline(gw, rb: ResultBuilder, *, customer: str, as_of: date, window_days: int = 90) -> dict:
    ctx = await currency_context(gw, rb)
    match = resolve(customer, rb.use(await gw.customers()), email_key="email")
    if match.status != "matched":
        return rb.finish({"match": match.describe(),
                          "next_step": "ask the user which account they mean"}, status="partial")
    cust = match.entity
    cid = cust["id"]
    current, prior = trailing_windows(as_of, window_days)
    all_rows = await invoices_for(gw, rb, Window(prior.start, current.end), ctx)
    home = ctx.home_currency
    rows = [i for i in all_rows if i["customer_id"] == cid and i["currency"] == home]
    book = [i for i in all_rows if i["currency"] == home]
    verified: list[Finding] = []
    hypotheses: list[Finding] = []
    src_inv = ["qb.invoices"]

    def total(invs: list[dict], w: Window) -> Decimal:
        return sum((i["total"] for i in invs if w.contains(i["date"])), Decimal("0"))

    cur_t, pri_t = total(rows, current), total(rows, prior)
    cur_n = sum(1 for i in rows if current.contains(i["date"]))
    pri_n = sum(1 for i in rows if prior.contains(i["date"]))
    change = pct_change(cur_t, pri_t)
    verified.append(Finding(
        f"Billed sales to {cust['name']} were {cur_t} in the last {window_days} days vs {pri_t} in the "
        f"prior {window_days} days" + (f" ({change}%)" if change is not None else ""), "verified",
        {"current": cur_t, "prior": pri_t, "change_pct": change, "orders_current": cur_n,
         "orders_prior": pri_n, "currency": home}, src_inv))
    declined = pri_t > 0 and cur_t < pri_t
    if not declined:
        verified.append(Finding("Orders did not decline over the compared windows", "verified",
                                {"current": cur_t, "prior": pri_t}, src_inv))

    if declined:
        if cur_n < pri_n:
            verified.append(Finding(f"Order count fell from {pri_n} to {cur_n}", "verified",
                                    {"orders_current": cur_n, "orders_prior": pri_n}, src_inv))
        if cur_n and pri_n:
            avg_c, avg_p = cur_t / cur_n, pri_t / pri_n
            if avg_c < avg_p:
                verified.append(Finding("Average order value fell", "verified",
                                        {"current": avg_c.quantize(Decimal("0.01")),
                                         "prior": avg_p.quantize(Decimal("0.01"))}, src_inv))

        cur_p, pri_p = _avg_prices(rows, current), _avg_prices(rows, prior)
        drops = sorted(((pid, pri_p[pid]["amount"] - cur_p.get(pid, {"amount": Decimal("0")})["amount"])
                        for pid in pri_p), key=lambda x: -x[1])
        dropped = [(pid, amt) for pid, amt in drops if amt > 0][:5]
        if dropped:
            verified.append(Finding("Products with the largest drop in purchases", "verified",
                                    {"products": [{"product_id": pid, "name": pri_p[pid]["name"], "decrease": amt}
                                                  for pid, amt in dropped]}, src_inv))
        catalog = rb.optional(await gw.products())
        if catalog is not None:
            stock = {p["id"]: p for p in catalog}
            for pid, _ in dropped:
                p = stock.get(pid)
                if p is not None and p["qty_on_hand"] <= 0:
                    verified.append(Finding(f"{p['name']} has no stock on hand now", "verified",
                                            {"product_id": pid, "qty_on_hand": p["qty_on_hand"]}, ["qb.products"]))
                    hypotheses.append(Finding(
                        f"A stock-out of {p['name']} may have limited their orders "
                        "(only current stock is observed, not past availability)", "hypothesis",
                        {"product_id": pid}, ["qb.products", "qb.invoices"]))
        for pid in cur_p:
            if pid in pri_p and cur_p[pid]["units"] and pri_p[pid]["units"]:
                pc = cur_p[pid]["amount"] / cur_p[pid]["units"]
                pp = pri_p[pid]["amount"] / pri_p[pid]["units"]
                if pp > 0 and pc > pp:
                    rise = ((pc - pp) / pp * HUNDRED).quantize(Decimal("0.1"))
                    verified.append(Finding(f"Average price paid for {cur_p[pid]['name']} rose {rise}%", "verified",
                                            {"product_id": pid, "current_avg": pc.quantize(Decimal("0.0001")),
                                             "prior_avg": pp.quantize(Decimal("0.0001"))}, src_inv))
                    hypotheses.append(Finding(f"The price increase on {cur_p[pid]['name']} may have reduced volume",
                                              "hypothesis", {"product_id": pid, "increase_pct": rise}, src_inv))

        open_inv = rb.optional(await gw.open_invoices())
        if open_inv is not None:
            mine = [i for i in with_currency(dedupe(open_inv, label="invoice", rb=rb), ctx)
                    if i["customer_id"] == cid and i.get("due_date") and i["due_date"] < as_of]
            if mine:
                overdue = sum((i["balance"] for i in mine if i["currency"] == home), Decimal("0"))
                oldest = max((as_of - i["due_date"]).days for i in mine)
                verified.append(Finding(f"They have {len(mine)} overdue invoice(s), oldest {oldest} days past due",
                                        "verified", {"overdue_balance": overdue, "currency": home},
                                        ["qb.open_invoices"]))
                hypotheses.append(Finding("Overdue balances or a credit hold may be restricting new orders",
                                          "hypothesis", {}, ["qb.open_invoices"]))
            else:
                verified.append(Finding("No overdue invoices for this account", "verified", {},
                                        ["qb.open_invoices"]))

        rest = [i for i in book if i["customer_id"] != cid]
        book_cur, book_pri = total(rest, current), total(rest, prior)
        book_change = pct_change(book_cur, book_pri)
        scope_label = "other accounts" if gw.scope.company_wide else "your other assigned accounts"
        verified.append(Finding(f"Across {scope_label}, billed sales changed "
                                f"{book_change if book_change is not None else 'n/a (no prior sales)'}% "
                                "over the same windows", "verified",
                                {"current": book_cur, "prior": book_pri, "change_pct": book_change}, src_inv))
        if book_change is not None and change is not None and book_change <= change / 2:
            hypotheses.append(Finding("The decline looks broad (seasonal or market-wide) rather than "
                                      "specific to this account", "hypothesis", {}, src_inv))
        elif change is not None:
            hypotheses.append(Finding("The decline is steeper than the wider trend, suggesting an "
                                      "account-specific cause (e.g. a competitor or a lost line)", "hypothesis",
                                      {}, src_inv))

        if gw.scope.has("comms.metadata") and cust.get("email"):
            mail = rb.optional(await gw.messages(query=f"from:{cust['email']}", max_results=25))
            if mail is not None:
                verified.append(Finding(f"{len(mail)} recent message(s) from {cust['email']} in the mailbox sample",
                                        "verified", {"message_ids": [m.get("id") for m in mail][:25]},
                                        ["mail.messages"]))
                if mail:
                    hypotheses.append(Finding("Recent correspondence may explain the change — review those "
                                              "messages (content was not analysed)", "hypothesis", {},
                                              ["mail.messages"]))
        else:
            rb.note_missing("correspondence", "not_in_scope_or_no_email",
                            "customer correspondence was not checked")
        rb.note_missing("competitor_and_market_data", "not_connected",
                        "external causes cannot be observed from connected systems")

    return rb.finish({
        "customer": {"id": cid, "name": cust["name"]}, "match": match.describe(),
        "current_window": current.describe(), "prior_window": prior.describe(),
        "declined": declined, "currency": home,
        "verified_count": len(verified), "hypothesis_count": len(hypotheses),
        "causality_note": "hypotheses are consistent with the data but are not proven causes",
    }, findings=verified + hypotheses)
