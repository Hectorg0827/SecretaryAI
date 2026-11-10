"""Wildora — Accounts Receivable functions (read-only).

Aging reuses ``app.intelligence.receivables.age_invoice_balances`` (WS2 M7),
fed from the *all open invoices* read so coverage is exhaustive rather than a
date window. Collection work is prioritised and explained; no message is
drafted or sent here (outbound email belongs to WS1's approval gateway).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.intelligence.receivables import age_invoice_balances
from app.lola_bi.common import (
    currency_context, dedupe, invoices_for, note_currency, Window, with_currency,
)
from app.lola_bi.contracts import Finding, ResultBuilder
from app.lola_bi.money import MoneyTotals, UNKNOWN_CURRENCY

_BUCKET_WEIGHT = {"current": Decimal("0"), "days_1_30": Decimal("1"), "days_31_60": Decimal("2"),
                  "days_61_90": Decimal("3"), "days_91_plus": Decimal("4"), "unaged": Decimal("1")}


async def _open_invoices(gw, rb: ResultBuilder):
    ctx = await currency_context(gw, rb)
    snap = await gw.open_invoices()
    rows = with_currency(dedupe(rb.use(snap), label="invoice", rb=rb), ctx)
    note_currency(rb, ctx, rows)
    return ctx, snap, rows


async def receivables_summary(gw, rb: ResultBuilder, *, as_of: date) -> dict:
    ctx, snap, rows = await _open_invoices(gw, rb)
    by_currency: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_currency[r["currency"]].append(r)
    agings = []
    for currency, group in sorted(by_currency.items()):
        projection = [{
            "tenant_id": gw.scope.tenant_id, "source_connection_id": snap.provider,
            "source_invoice_id": r["id"], "source_ref": f"{snap.provider}:invoice:{r['id']}",
            "customer_id": r["customer_id"], "currency": r["currency"], "date": r["date"],
            "due_date": r["due_date"], "total": r["total"], "balance": r["balance"], "status": r["status"],
        } for r in group]
        aging = age_invoice_balances(
            projection, as_of=as_of, tenant_id=gw.scope.tenant_id, source_connection_id=snap.provider,
            source_as_of=snap.fetched_at.date(), source_observed_at=snap.fetched_at,
            # The read happens after the request started; evaluate no earlier than the read.
            evaluated_at=max(rb.now, snap.fetched_at), source_complete=True,
            covers_all_open_invoices=snap.coverage == "all_open_invoices",
            currency_verified=currency != UNKNOWN_CURRENCY, currency=currency if currency != UNKNOWN_CURRENCY else None,
        )
        agings.append({k: aging[k] for k in ("status", "currency", "total_open", "buckets",
                                               "invoice_count", "exceptions", "reconciliation_status")})
    if any(a["status"] == "unavailable" for a in agings):
        rb.warn("some currencies could not be aged; see exceptions")
    total = MoneyTotals()
    for a in agings:
        if a["total_open"] is not None:
            total.add(a["currency"], a["total_open"])
    overdue = MoneyTotals()
    for a in agings:
        if a["buckets"]:
            overdue.add(a["currency"], sum((v for k, v in a["buckets"].items() if k not in ("current", "unaged")),
                                           Decimal("0")))
    answer = {
        "as_of": as_of,
        "meaning": "open invoice balances customers owe (accounts receivable per QuickBooks); "
                   "not reconciled to the general ledger",
        "total_owed": total.describe(), "overdue": overdue.describe(),
        "aging_by_currency": agings, "open_invoice_count": len(rows),
        "customers_with_balance": len({r["customer_id"] for r in rows}),
        "unapplied_credits_observed": False,
    }
    rb.note_missing("credit_memos_and_unapplied_payments", "not_read",
                    "open credits are not netted; customer balances may be lower than invoice totals")
    return rb.finish(answer)


def _customer_rollup(rows: list[dict], as_of: date) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        c = out.setdefault(r["customer_id"], {
            "customer_id": r["customer_id"], "customer_name": r["customer_name"],
            "open": MoneyTotals(), "overdue": MoneyTotals(), "oldest_days_overdue": 0,
            "overdue_invoices": 0, "worst_bucket_weight": Decimal("0"), "weighted": MoneyTotals(),
        })
        c["open"].add(r["currency"], r["balance"])
        due = r.get("due_date")
        days = (as_of - due).days if due else None
        if days is not None and days > 0:
            c["overdue"].add(r["currency"], r["balance"])
            c["overdue_invoices"] += 1
            c["oldest_days_overdue"] = max(c["oldest_days_overdue"], days)
            bucket = ("days_1_30" if days <= 30 else "days_31_60" if days <= 60
                      else "days_61_90" if days <= 90 else "days_91_plus")
            weight = _BUCKET_WEIGHT[bucket]
            c["weighted"].add(r["currency"], r["balance"] * weight)
            c["worst_bucket_weight"] = max(c["worst_bucket_weight"], weight)
    return out


def _view(c: dict) -> dict:
    return {"customer_id": c["customer_id"], "customer_name": c["customer_name"],
            "open_balance": c["open"].by_currency(), "overdue_balance": c["overdue"].by_currency(),
            "overdue_invoices": c["overdue_invoices"], "oldest_days_overdue": c["oldest_days_overdue"]}


async def overdue_customers(gw, rb: ResultBuilder, *, as_of: date, min_days_overdue: int = 1,
                            limit: int = 25) -> dict:
    if min_days_overdue < 1 or not 1 <= limit <= 200:
        raise ValueError("min_days_overdue must be >= 1 and limit between 1 and 200")
    _, _, rows = await _open_invoices(gw, rb)
    rollup = _customer_rollup(rows, as_of)
    hits = [c for c in rollup.values() if c["oldest_days_overdue"] >= min_days_overdue]
    hits.sort(key=lambda c: (-c["oldest_days_overdue"], c["customer_name"]))
    total = MoneyTotals()
    for c in hits:
        total = total.merge(c["overdue"])
    return rb.finish({"as_of": as_of, "customer_count": len(hits), "total_overdue": total.describe(),
                      "customers": [_view(c) for c in hits[:limit]], "truncated": len(hits) > limit})


async def upcoming_due(gw, rb: ResultBuilder, *, as_of: date, within_days: int = 14) -> dict:
    if not 0 <= within_days <= 120:
        raise ValueError("within_days must be between 0 and 120")
    _, _, rows = await _open_invoices(gw, rb)
    horizon = as_of + timedelta(days=within_days)
    due = sorted((r for r in rows if r.get("due_date") and as_of <= r["due_date"] <= horizon),
                 key=lambda r: (r["due_date"], r["id"]))
    total = MoneyTotals()
    for r in due:
        total.add(r["currency"], r["balance"])
    return rb.finish({
        "as_of": as_of, "through": horizon, "invoice_count": len(due), "total_due": total.describe(),
        "invoices": [{"id": r["id"], "doc_number": r["doc_number"], "customer_name": r["customer_name"],
                      "due_date": r["due_date"], "balance": r["balance"], "currency": r["currency"]}
                     for r in due[:200]],
        "missing_due_date_count": sum(1 for r in rows if r.get("due_date") is None),
    })


async def collection_priorities(gw, rb: ResultBuilder, *, as_of: date, limit: int = 15) -> dict:
    """Rank by overdue balance x age weight; explain every rank. Advisory only."""
    ctx, _, rows = await _open_invoices(gw, rb)
    rollup = _customer_rollup(rows, as_of)
    payments = rb.optional(await gw.payments(as_of - timedelta(days=179), as_of))
    last_paid: dict[str, date] = {}
    for p in dedupe(payments or (), label="payment", rb=rb):
        last_paid[p["customer_id"]] = max(last_paid.get(p["customer_id"], date.min), p["date"])
    contacts = {}
    if gw.scope.has("comms.metadata"):
        customers = rb.optional(await gw.customers()) or ()
        contacts = {c["id"]: (c.get("email") or "").strip().lower() for c in customers if c.get("email")}
        mail = rb.optional(await gw.messages(max_results=200))
    else:
        mail = None
    ranked = []
    for c in rollup.values():
        if not c["overdue"].currencies:
            continue
        code, weighted, _ = c["weighted"].single()
        reasons = [f"{c['overdue_invoices']} overdue invoice(s)", f"oldest {c['oldest_days_overdue']} days past due"]
        lp = last_paid.get(c["customer_id"])
        if payments is not None:
            reasons.append(f"last payment {lp.isoformat()}" if lp else "no payment in the last 180 days")
        correspondence = None
        if mail is not None and contacts.get(c["customer_id"]):
            addr = contacts[c["customer_id"]]
            correspondence = sum(1 for m in mail if addr and addr in str(m.get("from_email") or m.get("from") or "").lower())
            reasons.append(f"{correspondence} recent message(s) from their address in the sampled mailbox")
        ranked.append({**_view(c), "priority_score": weighted, "score_currency": code,
                       "last_payment_date": lp, "recent_messages_from_customer": correspondence,
                       "reasons": reasons})
    ranked.sort(key=lambda r: (r["priority_score"] is None, -(r["priority_score"] or 0), r["customer_name"]))
    answer = {
        "as_of": as_of, "method": "overdue balance weighted by age bucket (1-30:1, 31-60:2, 61-90:3, 90+:4)",
        "priorities": ranked[:limit],
        "next_step": "collection emails must be drafted and approved through the approval workflow",
    }
    return rb.finish(answer)


async def payment_reconciliation(gw, rb: ResultBuilder, *, start: date, end: date, as_of: date) -> dict:
    """Check payment allocations and invoice balance roll-forward with exact decimals."""
    if end > as_of:
        raise ValueError("Reconciliation window cannot end after as_of")
    ctx = await currency_context(gw, rb)
    invoices = await invoices_for(gw, rb, Window(start, end), ctx)
    payments = dedupe(rb.use(await gw.payments(start, as_of)), label="payment", rb=rb)
    findings: list[Finding] = []
    payment_checks = []
    unapplied = MoneyTotals()
    applied_by_invoice: dict[str, Decimal] = defaultdict(Decimal)
    for p in with_currency(payments, ctx):
        applied = sum((a["amount"] for a in p["applied"]), Decimal("0"))
        other = sum((o["amount"] for o in p.get("other_links", ())), Decimal("0"))
        for a in p["applied"]:
            applied_by_invoice[a["invoice_id"]] += a["amount"]
        if p["unapplied"] is None:
            state = "unapplied_amount_not_reported"
        elif applied + other + p["unapplied"] == p["total"]:
            state = "balanced"
        else:
            state = "out_of_balance"
            findings.append(Finding("Payment allocations do not add up to the payment total", "verified",
                                    {"payment_id": p["id"], "total": p["total"], "applied": applied,
                                     "other_links": other, "unapplied": p["unapplied"]}, ["qb.payments"]))
        if p["unapplied"]:
            unapplied.add(p["currency"], p["unapplied"])
        payment_checks.append({"payment_id": p["id"], "customer_id": p["customer_id"], "date": p["date"],
                               "total": p["total"], "applied_to_invoices": applied, "unapplied": p["unapplied"],
                               "state": state})
    invoice_checks = []
    for inv in invoices:
        applied = applied_by_invoice.get(inv["id"], Decimal("0"))
        expected = inv["total"] - applied
        if applied > inv["total"]:
            state = "overapplied"
            findings.append(Finding("Payments applied exceed the invoice total", "verified",
                                    {"invoice_id": inv["id"], "total": inv["total"], "applied": applied},
                                    ["qb.invoices", "qb.payments"]))
        elif expected == inv["balance"]:
            state = "reconciled"
        else:
            state = "unexplained_difference"
            findings.append(Finding(
                "Invoice balance differs from total minus observed payments "
                "(credit memos, discounts, journal entries or out-of-window payments are not read)",
                "hypothesis", {"invoice_id": inv["id"], "total": inv["total"], "payments_applied": applied,
                               "reported_balance": inv["balance"], "difference": expected - inv["balance"]},
                ["qb.invoices", "qb.payments"]))
        invoice_checks.append({"invoice_id": inv["id"], "total": inv["total"], "payments_applied": applied,
                               "reported_balance": inv["balance"], "state": state})
    summary = defaultdict(int)
    for c in invoice_checks:
        summary[c["state"]] += 1
    answer = {
        "invoice_window": {"from": start, "to": end}, "payments_through": as_of,
        "invoice_states": dict(summary), "payments_checked": len(payment_checks),
        "unapplied_cash": unapplied.describe(),
        "invoices": invoice_checks[:200], "payments": payment_checks[:200],
    }
    return rb.finish(answer, findings=findings)
