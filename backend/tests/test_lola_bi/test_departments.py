"""Deterministic results for Franklin, Wildora, AP, Raul and Sales/CRM (hand-checked numbers)."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.lola_bi.common import Window, month_to_date, shift_year, trailing_windows
from app.lola_bi.entities import resolve

from .synthetic import CUSTOMERS, REP1, FakeReads, service


async def run(name, args=None, role="owner", **kw):
    svc, reads = service(role, **kw)
    return await svc.invoke(name, args or {})


# ── Franklin ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cash_position_sums_exactly_per_currency():
    r = await run("finance_cash_position")
    assert r["answer"]["cash"]["by_currency"] == {"CAD": "1000.00", "USD": "15000.00"}
    assert r["answer"]["cash"]["total"] is None  # mixed currencies are not added
    assert r["answer"]["cash"]["fx_missing_for"] == ["CAD"]


@pytest.mark.asyncio
async def test_cash_position_with_explicit_fx_rate():
    from app.lola_bi.money import FxRate
    from .synthetic import NOW, FakeAdapter, FakeDB, clock, scope_for
    from app.lola_bi.gateway import BusinessDataGateway
    from app.lola_bi.tools import LolaBIService
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=FakeReads(), db=FakeDB(), clock=clock)
    svc = LolaBIService(gw, clock=clock, fx_rates=[FxRate("CAD", "USD", D("0.7300"), "test-rate", NOW)])
    r = await svc.invoke("finance_cash_position", {})
    assert r["answer"]["cash"]["converted_total"] == "15730.000000"
    assert r["answer"]["cash"]["fx_rates_used"][0]["source"] == "test-rate"


@pytest.mark.asyncio
async def test_cash_flow_schedule_rolls_forward_exactly():
    r = await run("finance_cash_flow_projection", {"weeks": 13})
    a = r["answer"]
    assert r["status"] == "partial" and a["not_a_forecast"] is True
    assert a["starting_cash"] == "15000.00" and a["ending_cash"] == "3500.00"
    opening = D(a["starting_cash"])
    for week in a["weeks"]:
        assert D(week["opening"]) == opening
        opening = D(week["opening"]) + D(week["receivables_due"]) - D(week["payables_due"])
        assert D(week["closing"]) == opening
    assert a["outside_schedule"]["overdue_receivables_timing_unknown"] == "1300.50"
    assert a["outside_schedule"]["overdue_payables_already_due"] == "750.00"
    assert a["excluded_other_currencies"] == {"CAD": "1700.00"}
    assert "payroll and payroll taxes" in a["not_included"]


@pytest.mark.asyncio
async def test_cash_flow_requires_known_home_currency():
    reads = FakeReads(data={"currency": {"home_currency": None, "multicurrency_enabled": None, "source": "x"}})
    r = await run("finance_cash_flow_projection", reads=reads)
    assert r["status"] == "unavailable" and r["answer"] is None


@pytest.mark.asyncio
async def test_profit_and_loss_subtotals_reconcile_and_prior_period_compared():
    r = await run("finance_profit_and_loss")
    a = r["answer"]
    assert a["lines"]["net_income"] == "1600.00" and a["gross_margin_pct"] == "40.0"
    assert all(c["consistent"] for c in a["subtotal_checks"]) and len(a["subtotal_checks"]) == 3
    assert a["prior_period"]["period"] == {"from": "2026-10-23", "to": "2026-10-31"}  # 9 days, like Nov 1-9
    assert a["prior_period"]["change_pct"]["income"] == "25.0"


@pytest.mark.asyncio
async def test_profit_and_loss_flags_inconsistent_report():
    reads = FakeReads()
    reads.data["pnl"]["gross_profit"] = D("3999.00")
    r = await run("finance_profit_and_loss", {"compare_prior": False}, reads=reads)
    assert r["status"] == "partial"
    assert any("does not reconcile" in w for w in r["warnings"])


@pytest.mark.asyncio
async def test_brand_profitability_uses_current_cost_and_isolates_unknowns():
    r = await run("finance_profitability_by_brand", {"start": "2026-08-12", "end": "2026-11-09"})
    brands = {b["brand"]: b for b in r["answer"]["brands"]}
    # Lion USD lines: I-104 20 x P1 = 440 (cost 200); I-203 10 x P1 = 90 (cost 100)
    assert brands["Lion"]["revenue"] == "530.00" and brands["Lion"]["estimated_gross_profit"] == "230.00"
    # Tigre: I-202 30 x P3 = 800 (cost 450); I-205 5 x P3 = 150 (cost 75); void I-204 excluded
    assert brands["Tigre"]["revenue"] == "950.00" and brands["Tigre"]["estimated_gross_profit"] == "425.00"
    # Mezcal has no cost and no brand: revenue reported, margin unknown, never 100%
    assert brands["Unassigned"]["estimated_margin_pct"] is None
    assert brands["Unassigned"]["revenue_without_cost_data"] == "225.00"
    assert r["answer"]["excluded_lines"]["non_home_currency"] == 1  # CAD invoice I-302


@pytest.mark.asyncio
async def test_anomalies_separate_verified_from_hypotheses():
    r = await run("finance_anomalies")
    statements = {(f["statement"], f["basis"]) for f in r["findings"]}
    assert ("Customer carries a credit balance (unapplied credit or overpayment)", "verified") in statements
    assert ("Possible duplicate vendor bill (same_vendor_and_number)", "verified") in statements


# ── Wildora ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_receivables_aging_equals_sum_of_open_balances():
    r = await run("ar_receivables_summary")
    usd = next(a for a in r["answer"]["aging_by_currency"] if a["currency"] == "USD")
    assert usd["total_open"] == "1800.50"
    assert sum(D(v) for v in usd["buckets"].values()) == D("1800.50")
    assert usd["buckets"]["days_61_90"] == "1000.00" and usd["buckets"]["days_1_30"] == "300.50"
    assert r["answer"]["overdue"]["by_currency"]["USD"] == "1300.50"


@pytest.mark.asyncio
async def test_overdue_customers_and_upcoming_due():
    r = await run("ar_overdue_customers")
    names = [c["customer_name"] for c in r["answer"]["customers"]]
    assert names == ["Sunrise Distributors", "Delta Beverage Co"]
    assert r["answer"]["customers"][0]["oldest_days_overdue"] == 69
    up = await run("ar_upcoming_due_invoices", {"within_days": 14})
    assert [i["id"] for i in up["answer"]["invoices"]] == ["I-051"]
    up0 = await run("ar_upcoming_due_invoices", {"within_days": 0})
    assert up0["answer"]["invoice_count"] == 0


@pytest.mark.asyncio
async def test_payment_reconciliation_is_exact():
    r = await run("ar_payment_reconciliation", {"start": "2026-09-01", "end": "2026-11-09"})
    states = {i["invoice_id"]: i["state"] for i in r["answer"]["invoices"]}
    assert states["I-202"] == "reconciled"  # 800.00 - 499.50 == 300.50
    assert states["I-104"] == "reconciled"
    assert states["I-203"] == "unexplained_difference"  # paid in QB, no payment observed
    assert r["answer"]["unapplied_cash"]["total"] == "100.00"
    hyp = [f for f in r["findings"] if f["evidence"].get("invoice_id") == "I-203"]
    assert hyp and hyp[0]["basis"] == "hypothesis"


@pytest.mark.asyncio
async def test_out_of_balance_payment_is_a_verified_finding():
    reads = FakeReads()
    reads.data["payments"][0]["unapplied"] = D("1.00")  # 499.50 applied + 1.00 != 499.50
    r = await run("ar_payment_reconciliation", {"start": "2026-09-01", "end": "2026-11-09"}, reads=reads)
    bad = [f for f in r["findings"] if f["evidence"].get("payment_id") == "PMT-1"]
    assert bad and bad[0]["basis"] == "verified"


# ── Accounts payable ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_bills_bucket_and_duplicates():
    r = await run("ap_open_bills", {"due_within_days": 7})
    a = r["answer"]
    assert a["total_open"]["total"] == "12750.00"
    assert a["overdue"]["total"] == "750.00" and a["due_within_7_days"]["total"] == "10000.00"
    assert a["possible_duplicates"][0]["bill_ids"] == ["B1", "B2"]


@pytest.mark.asyncio
async def test_payment_proposal_respects_cash_and_holds_duplicates():
    r = await run("ap_payment_proposal", {"horizon_days": 30, "cash_reserve": "12500"})
    a = r["answer"]
    assert a["executed"] is False and a["requires_approval"] is True
    assert a["available_for_payments"] == "2500.00"
    assert [p["bill_id"] for p in a["proposed_payments"]] == ["B4"]
    assert [d["bill_id"] for d in a["deferred"]] == ["B3"]
    assert {h["bill_id"] for h in a["held_for_review"]} == {"B1", "B2"}
    assert all(p["submitted"] is False and p["action_class"] == "commit" for p in a["action_proposals_for_approval"])


# ── Raul ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_product_margins_and_below_cost_pricing():
    r = await run("pricing_product_margins", {"start": "2026-08-12", "end": "2026-11-09"})
    top = r["answer"]["highest_margin"]
    assert top[0]["name"] == "Tequila Blanco" and top[0]["estimated_margin_pct"] == "44.7"
    assert "Mezcal Artesanal" in r["answer"]["products_without_cost"]
    d = await run("pricing_price_discrepancies", {"start": "2026-08-12", "end": "2026-11-09"})
    below = d["answer"]["sold_below_current_cost"]
    assert [(b["invoice_id"], b["unit_price"]) for b in below] == [("I-203", "9.00")]


@pytest.mark.asyncio
async def test_billbacks_are_unavailable_not_zero():
    r = await run("pricing_billbacks_and_promotions")
    assert r["status"] == "unavailable" and r["answer"]["available"] is False


# ── Sales / CRM ──────────────────────────────────────────────────────────────

def test_date_window_helpers_handle_edges():
    cur, prior = trailing_windows(date(2026, 11, 9), 90)
    assert (cur.start, cur.end, prior.start, prior.end) == (
        date(2026, 8, 12), date(2026, 11, 9), date(2026, 5, 14), date(2026, 8, 11))
    assert cur.days == prior.days == 90
    assert shift_year(date(2028, 2, 29), -1) == date(2027, 2, 28)
    assert month_to_date(date(2026, 3, 1)) == Window(date(2026, 3, 1), date(2026, 3, 1))
    with pytest.raises(ValueError):
        trailing_windows(date(2026, 1, 1), 3)
    with pytest.raises(ValueError):
        Window(date(2026, 1, 2), date(2026, 1, 1))


@pytest.mark.asyncio
async def test_month_to_date_vs_last_year_and_prior_month():
    r = await run("sales_period_comparison")
    a = r["answer"]
    assert a["month_to_date"]["billed"]["total"] == "315.00"  # I-203; void I-204 excluded
    assert a["vs_same_period_last_year"]["from"] == "2025-11-01"
    assert a["vs_same_period_last_year"]["to"] == "2025-11-09"
    assert a["vs_same_period_last_year"]["prior"]["total"] == "500.00"  # I-091 (Nov 20) is outside
    assert a["vs_same_period_last_year"]["change_pct"] == "-37.0"
    prior_month = a["vs_prior_month_same_days"]
    assert prior_month["comparable"] is False  # Oct 1-9 includes a CAD invoice: no mixed total
    assert prior_month["by_currency_change"]["USD"] == {
        "current": "315.00", "prior": "150.00", "change_amount": "165.00", "change_pct": "110.0"}


@pytest.mark.asyncio
async def test_last_year_failure_is_missing_not_zero_growth():
    class FailingLastYear(FakeReads):
        async def invoices_between(self, a, b):
            if a.year == 2025:
                raise RuntimeError("provider outage")
            return await super().invoices_between(a, b)
    r = await run("sales_period_comparison", reads=FailingLastYear())
    assert r["status"] == "partial" and r["answer"]["vs_same_period_last_year"] is None


@pytest.mark.asyncio
async def test_declining_accounts_and_underperforming_products():
    r = await run("sales_declining_accounts")
    [acct] = r["answer"]["accounts"]
    assert acct["customer_name"] == "Sunrise Distributors"
    assert (acct["current"], acct["prior"], acct["change_pct"]) == ("440.00", "3000.00", "-85.3")
    p = await run("sales_underperforming_products")
    products = {x["name"]: x for x in p["answer"]["products"]}
    assert products["Spiced Rum 1L"]["reason"] == "sales_declined"


@pytest.mark.asyncio
async def test_territory_decomposition_reconciles_and_excludes_other_currency():
    r = await run("sales_territory_drivers", {"territory": "north"})
    a = r["answer"]
    assert a["decomposition_reconciles"] is True and a["change_amount"] == "-2560.00"
    assert [x["customer_id"] for x in a["accounts_excluded"]] == ["C3"]  # CAD account: excluded, not zero
    assert a["by_product"][0]["name"] == "Spiced Rum 1L"
    assert any(f["basis"] == "hypothesis" and "out of stock" in f["statement"] for f in r["findings"])


@pytest.mark.asyncio
async def test_unknown_territory_lists_known_ones():
    r = await run("sales_territory_drivers", {"territory": "West"})
    assert r["answer"]["found"] is False and r["answer"]["known_territories"] == ["North", "South"]


@pytest.mark.asyncio
async def test_rep_attention_flags_declining_rep_book():
    r = await run("sales_rep_attention")
    reps = {x["rep_name"]: x for x in r["answer"]["reps"]}
    assert reps["Ana Rep"]["needs_attention"] and reps["Ana Rep"]["overdue_balance"] == "1000.00"
    assert reps["Ana Rep"]["accounts_excluded_other_currency"] == 1
    assert reps["Ben Rep"]["current"] == "1265.00" and reps["Ben Rep"]["change_pct"] == "321.7"
    assert reps["Ben Rep"]["reasons"] == ["overdue balances in book"]  # growing, but has overdue AR


@pytest.mark.asyncio
async def test_projection_method_is_explicit():
    r = await run("sales_projection")
    a = r["answer"]
    # trailing window 2026-08-03..2026-10-31 (90 days), USD billed = 440+800+150 = 1390 → 15.44/day
    assert a["trailing_daily_average"] == "15.44" and a["remaining_days"] == 21
    assert D(a["projected_month_total"]) == D("315.00") + D("15.44") * 21
    assert "uncalibrated" in a["method"]


@pytest.mark.asyncio
async def test_customer_profile_combines_systems():
    r = await run("crm_customer_profile", {"customer": "buyer@sunrise.example"})
    a = r["answer"]
    assert a["customer"]["id"] == "C1" and a["match"]["method"] == "exact_email"
    assert a["open_balance"]["total"] == "1500.00" and a["overdue_balance"]["total"] == "1000.00"
    assert a["territory"] == "North" and a["assigned_rep"] == "Ana Rep"
    assert a["recent_messages_from_customer"] == 1


@pytest.mark.asyncio
async def test_rep_sees_their_book_only_in_period_totals():
    r = await run("sales_period_comparison", role="sales_rep", user=REP1, accounts=frozenset({"C1", "C3"}))
    assert r["answer"]["month_to_date"]["invoice_count"] == 0  # Delta's November invoices are not theirs
    assert r["answer"]["vs_same_period_last_year"]["prior"]["total"] == "500.00"


# ── Entity matching & investigation ──────────────────────────────────────────

def test_entity_resolution_never_guesses():
    assert resolve("C2", CUSTOMERS).entity["name"] == "Delta Beverage Co"
    assert resolve("delta", CUSTOMERS).method == "all_tokens"
    assert resolve("DELTA BEVERAGE CO.", CUSTOMERS).method == "exact_name"
    ambiguous = resolve("Sunrise", CUSTOMERS)
    assert ambiguous.status == "ambiguous" and {c["id"] for c in ambiguous.candidates} == {"C1", "C4"}
    assert resolve("Sunrse Distributors", CUSTOMERS).status == "not_found"  # no fuzzy guessing
    assert resolve("", CUSTOMERS).status == "not_found"


@pytest.mark.asyncio
async def test_ambiguous_investigation_asks_instead_of_guessing():
    r = await run("investigate_account_decline", {"customer": "Sunrise"})
    assert r["status"] == "partial" and r["answer"]["match"]["status"] == "ambiguous"
    assert r["findings"] == []


@pytest.mark.asyncio
async def test_investigation_separates_verified_causes_from_hypotheses():
    r = await run("investigate_account_decline", {"customer": "Sunrise Distributors"})
    verified = [f["statement"] for f in r["findings"] if f["basis"] == "verified"]
    hypotheses = [f["statement"] for f in r["findings"] if f["basis"] == "hypothesis"]
    assert any("440.00" in s and "3000.00" in s for s in verified)
    assert "Spiced Rum 1L has no stock on hand now" in verified
    assert any("rose 10.0%" in s for s in verified)
    assert any("overdue invoice" in s for s in verified)
    assert any("stock-out" in s for s in hypotheses)
    assert any("account-specific" in s for s in hypotheses)
    assert all(f["sources"] for f in r["findings"])
    assert r["answer"]["causality_note"]


@pytest.mark.asyncio
async def test_growing_account_reports_no_decline():
    r = await run("investigate_account_decline", {"customer": "Delta Beverage Co"})
    assert r["answer"]["declined"] is False
    assert all(f["basis"] == "verified" for f in r["findings"])
