"""Accounts Payable functions (read-only).

``payment_proposal`` only *describes* a proposed payment run. It returns
``ActionProposal``-shaped dicts (action_class=commit) for Workstream 1's
policy engine and approval workflow; nothing is submitted, queued or paid here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.lola_bi.common import currency_context, dedupe, note_currency, with_currency
from app.lola_bi.contracts import ResultBuilder, SourceUnavailable
from app.lola_bi.entities import normalize_name
from app.lola_bi.money import MoneyTotals


def find_duplicate_bills(bills: list[dict]) -> list[dict]:
    """Same vendor + same bill number (strong), or same vendor/amount/date (possible)."""
    out: list[dict] = []
    by_number: dict[tuple, list[dict]] = defaultdict(list)
    by_amount: dict[tuple, list[dict]] = defaultdict(list)
    for bill in bills:
        doc = normalize_name(bill.get("doc_number")).replace(" ", "")
        if doc:
            by_number[(bill["vendor_id"], doc)].append(bill)
        by_amount[(bill["vendor_id"], bill["total"], bill["date"])].append(bill)
    flagged: set[frozenset] = set()
    for (vendor, doc), group in by_number.items():
        if len(group) > 1:
            ids = frozenset(b["id"] for b in group)
            flagged.add(ids)
            out.append({"match": "same_vendor_and_number", "vendor_id": vendor,
                        "vendor_name": group[0]["vendor_name"], "doc_number": group[0]["doc_number"],
                        "bill_ids": sorted(ids), "amounts": [b["total"] for b in group]})
    for (vendor, total, day), group in by_amount.items():
        ids = frozenset(b["id"] for b in group)
        if len(group) > 1 and ids not in flagged:
            out.append({"match": "same_vendor_amount_and_date", "vendor_id": vendor,
                        "vendor_name": group[0]["vendor_name"], "total": total, "date": day,
                        "bill_ids": sorted(ids)})
    return out


async def open_bills(gw, rb: ResultBuilder, *, as_of: date, due_within_days: int = 14) -> dict:
    if not 0 <= due_within_days <= 120:
        raise ValueError("due_within_days must be between 0 and 120")
    ctx = await currency_context(gw, rb)
    bills = with_currency(dedupe(rb.use(await gw.open_bills()), label="bill", rb=rb), ctx)
    note_currency(rb, ctx, bills)
    horizon = as_of + timedelta(days=due_within_days)
    overdue, due_soon, later, undated = MoneyTotals(), MoneyTotals(), MoneyTotals(), MoneyTotals()
    rows = []
    for b in bills:
        due = b.get("due_date")
        if due is None:
            undated.add(b["currency"], b["balance"])
            state = "no_due_date"
        elif due < as_of:
            overdue.add(b["currency"], b["balance"])
            state = "overdue"
        elif due <= horizon:
            due_soon.add(b["currency"], b["balance"])
            state = "due_soon"
        else:
            later.add(b["currency"], b["balance"])
            state = "later"
        rows.append({"id": b["id"], "vendor": b["vendor_name"], "doc_number": b["doc_number"],
                     "due_date": due, "balance": b["balance"], "currency": b["currency"], "state": state,
                     "days_overdue": (as_of - due).days if due and due < as_of else 0})
    rows.sort(key=lambda r: (r["due_date"] is None, r["due_date"] or date.max))
    total = MoneyTotals()
    for b in bills:
        total.add(b["currency"], b["balance"])
    answer = {
        "as_of": as_of, "bill_count": len(bills), "total_open": total.describe(),
        "overdue": overdue.describe(), f"due_within_{due_within_days}_days": due_soon.describe(),
        "due_later": later.describe(), "no_due_date": undated.describe(),
        "bills": rows[:100], "truncated": len(rows) > 100,
        "possible_duplicates": find_duplicate_bills(bills),
    }
    return rb.finish(answer)


async def duplicate_bills(gw, rb: ResultBuilder) -> dict:
    bills = dedupe(rb.use(await gw.open_bills()), label="bill", rb=rb)
    dups = find_duplicate_bills(bills)
    return rb.finish({"checked_open_bills": len(bills), "possible_duplicates": dups,
                      "note": "same vendor + bill number is a strong duplicate signal; "
                              "same vendor + amount + date needs human review"})


async def payment_proposal(gw, rb: ResultBuilder, *, as_of: date, horizon_days: int = 14,
                           cash_reserve: Decimal = Decimal("0")) -> dict:
    if not 0 <= horizon_days <= 60:
        raise ValueError("horizon_days must be between 0 and 60")
    if cash_reserve < 0:
        raise ValueError("cash_reserve must not be negative")
    ctx = await currency_context(gw, rb)
    if ctx.home_currency is None:
        raise SourceUnavailable("qb.company_currency", "unverified", "home currency unknown")
    home = ctx.home_currency
    banks = with_currency(rb.use(await gw.bank_accounts()), ctx)
    bills = with_currency(dedupe(rb.use(await gw.open_bills()), label="bill", rb=rb), ctx)
    cash = sum((b["balance"] for b in banks if b["currency"] == home), Decimal("0"))
    available = cash - cash_reserve
    suspect = {bid for d in find_duplicate_bills(bills) for bid in d["bill_ids"]}
    horizon = as_of + timedelta(days=horizon_days)
    candidates = sorted(
        (b for b in bills if b["currency"] == home and b.get("due_date") and b["due_date"] <= horizon),
        key=lambda b: (b["due_date"], b["id"]))
    proposed, deferred, held = [], [], []
    running = Decimal("0")
    for bill in candidates:
        item = {"bill_id": bill["id"], "vendor": bill["vendor_name"], "doc_number": bill["doc_number"],
                "due_date": bill["due_date"], "amount": bill["balance"]}
        if bill["id"] in suspect:
            held.append({**item, "reason": "possible duplicate — verify before paying"})
        elif running + bill["balance"] <= available:
            running += bill["balance"]
            proposed.append(item)
        else:
            deferred.append({**item, "reason": "exceeds available cash after reserve"})
    action_proposals = [{
        "capability": "ap_bill_payment", "action_class": "commit", "amount": p["amount"],
        "currency": home, "target_id": p["bill_id"], "description": f"Pay bill {p['doc_number'] or p['bill_id']}",
        "requires_human_approval": True, "submitted": False,
    } for p in proposed]
    other = [b for b in bills if b["currency"] != home]
    if other:
        rb.warn("bills in other currencies are not included in this proposal")
    answer = {
        "status": "proposal_only", "executed": False, "requires_approval": True,
        "currency": home, "as_of": as_of, "horizon_end": horizon,
        "cash_book_balance": cash, "cash_reserve": cash_reserve, "available_for_payments": available,
        "proposed_payments": proposed, "proposed_total": running,
        "cash_remaining_after_proposal": available - running,
        "deferred": deferred, "held_for_review": held,
        "action_proposals_for_approval": action_proposals,
    }
    return rb.finish(answer)
