"""Hierarchy, sales-rep restriction, cross-tenant isolation and cache safety."""
import json
from datetime import date

import pytest

from app.lola_bi.contracts import BIAccessDenied, TenantMismatch
from app.lola_bi.gateway import BusinessDataGateway, SnapshotCache
from app.lola_bi.scope import AssignmentScopeProvider, Cap, ScopeUnavailable
from app.lola_bi.tools import TOOLS

from .synthetic import (
    OTHER_TENANT, REP1, REP2, TENANT, FakeAdapter, FakeDB, FakeReads, clock, scope_for, service,
)

REP1_ACCOUNTS = frozenset({"C1", "C3"})


@pytest.mark.asyncio
async def test_assignment_provider_scopes_rep_to_assigned_accounts():
    scope = await AssignmentScopeProvider(FakeDB()).resolve(
        {"company_id": TENANT, "sub": REP1, "role": "sales_rep"})
    assert scope.record_scope == "assigned_accounts"
    assert scope.allowed_account_ids == REP1_ACCOUNTS
    assert scope.territories == frozenset({"North"})
    assert not scope.can_view_costs
    assert not scope.has(Cap.FINANCE_CASH, Cap.PRICING_READ, Cap.AP_READ, Cap.COMMS_METADATA, Cap.SALES_TEAM)


@pytest.mark.asyncio
async def test_assignment_rows_from_another_tenant_or_rep_never_widen_scope():
    rows = [{"qb_customer_id": "X9", "assigned_rep_id": REP1, "company_id": OTHER_TENANT, "territory": "Z"},
            {"qb_customer_id": "C2", "assigned_rep_id": REP2, "company_id": TENANT, "territory": "South"}]

    class LeakyDB(FakeDB):  # a query that ignores filters must still not widen scope
        def table(self, name):
            q = super().table(name)
            q.eq = lambda *_a: q
            return q
    scope = await AssignmentScopeProvider(LeakyDB(accounts=rows)).resolve(
        {"company_id": TENANT, "sub": REP1, "role": "sales_rep"})
    assert scope.allowed_account_ids == frozenset()


@pytest.mark.asyncio
async def test_assignment_outage_fails_closed():
    with pytest.raises(ScopeUnavailable):
        await AssignmentScopeProvider(FakeDB(fail=RuntimeError("db down"))).resolve(
            {"company_id": TENANT, "sub": REP1, "role": "sales_rep"})


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [
    {"company_id": TENANT, "sub": "u", "role": "viewer"},
    {"company_id": TENANT, "sub": "u", "role": "superadmin"},
    {"company_id": "", "sub": "u", "role": "owner"},
    {"company_id": TENANT, "role": "owner"},
])
async def test_unknown_roles_and_unverified_identity_get_no_access(user):
    with pytest.raises(BIAccessDenied):
        await AssignmentScopeProvider(FakeDB()).resolve(user)


@pytest.mark.asyncio
async def test_claims_can_narrow_but_never_widen_capabilities():
    scope = await AssignmentScopeProvider(FakeDB()).resolve(
        {"company_id": TENANT, "sub": "u", "role": "back_office",
         "bi_capabilities": [Cap.AR_COMPANY, Cap.FINANCE_CASH, Cap.PRICING_READ]})
    assert scope.capabilities == frozenset({Cap.AR_COMPANY})


def test_department_staff_lack_company_finance_and_cost_access():
    svc, _ = service("back_office")
    names = {t["name"] for t in svc.tool_definitions()}
    assert "ar_receivables_summary" in names and "ap_open_bills" in names
    for forbidden in ("finance_cash_position", "finance_profit_and_loss", "finance_cash_flow_projection",
                      "pricing_product_margins", "ap_payment_proposal", "finance_profitability_by_brand"):
        assert forbidden not in names


def test_every_tool_is_reachable_by_owner_and_none_by_viewer_capabilities():
    owner, _ = service("owner")
    assert {t["name"] for t in owner.tool_definitions()} == {t.name for t in TOOLS}


@pytest.mark.asyncio
async def test_sales_rep_only_sees_assigned_accounts_in_every_customer_bound_tool():
    svc, _ = service("sales_rep", user=REP1, accounts=REP1_ACCOUNTS)
    for name, args in [("ar_overdue_customers", {}), ("ar_upcoming_due_invoices", {}),
                       ("ar_collection_priorities", {}), ("sales_declining_accounts", {}),
                       ("ar_receivables_summary", {}), ("sales_period_comparison", {})]:
        result = await svc.invoke(name, args)
        assert result["status"] in ("ok", "partial"), (name, result)
        text = json.dumps(result)
        assert "C2" not in text and "Delta" not in text and "C4" not in text, name
        assert result["scope"]["record_scope"] == "assigned_accounts"


@pytest.mark.asyncio
async def test_rep_cannot_profile_or_investigate_someone_elses_account():
    svc, _ = service("sales_rep", user=REP1, accounts=REP1_ACCOUNTS)
    profile = await svc.invoke("crm_customer_profile", {"customer": "Delta Beverage Co"})
    assert profile["answer"]["match"]["status"] == "not_found"
    by_id = await svc.invoke("investigate_account_decline", {"customer": "C2"})
    assert by_id["answer"]["match"]["status"] == "not_found"
    assert "1265" not in json.dumps(by_id)


@pytest.mark.asyncio
async def test_rep_receives_no_cost_or_mailbox_data():
    svc, _ = service("sales_rep", user=REP1, accounts=REP1_ACCOUNTS)
    result = await svc.invoke("investigate_account_decline", {"customer": "C1"})
    assert "SECRET BODY TEXT" not in json.dumps(result)
    assert "mail.messages" not in {s["source"] for s in result["sources"]}
    products = await svc._gw.products()
    assert all(p["cost"] is None and p["cost_hidden"] for p in products.records)


@pytest.mark.asyncio
async def test_denied_tool_performs_no_provider_read():
    svc, reads = service("sales_rep", user=REP1, accounts=REP1_ACCOUNTS)
    for name in ("finance_cash_position", "ap_open_bills", "pricing_product_margins", "sales_rep_attention"):
        result = await svc.invoke(name, {})
        assert result["status"] == "denied" and result["answer"] is None
    assert reads.calls == []


@pytest.mark.asyncio
async def test_gateway_refuses_out_of_scope_source_without_reading():
    reads = FakeReads()
    gw = BusinessDataGateway(FakeAdapter(), scope_for("sales_rep", user=REP1, accounts=REP1_ACCOUNTS),
                             reads=reads, db=FakeDB(), clock=clock)
    for snap in (await gw.bank_accounts(), await gw.open_bills(),
                 await gw.profit_and_loss(date(2026, 11, 1), date(2026, 11, 9))):
        assert snap.status.value == "denied" and snap.records == ()
    assert reads.calls == []


def test_connector_from_another_tenant_is_rejected():
    with pytest.raises(TenantMismatch):
        BusinessDataGateway(FakeAdapter(tenant=OTHER_TENANT), scope_for("owner"), reads=FakeReads())
    with pytest.raises(TenantMismatch):
        BusinessDataGateway(None, scope_for("owner"), reads=FakeReads())


@pytest.mark.asyncio
async def test_shared_cache_never_leaks_across_tenants_or_scopes():
    cache = SnapshotCache()
    owner = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=FakeReads(), db=FakeDB(),
                                cache=cache, clock=clock)
    full = await owner.open_invoices()
    assert {r["customer_id"] for r in full.records} == {"C1", "C2", "C3"}

    rep_reads = FakeReads()
    rep = BusinessDataGateway(FakeAdapter(), scope_for("sales_rep", user=REP1, accounts=REP1_ACCOUNTS),
                              reads=rep_reads, db=FakeDB(), cache=cache, clock=clock)
    scoped = await rep.open_invoices()
    assert rep_reads.calls == []  # served from the tenant cache …
    assert {r["customer_id"] for r in scoped.records} == {"C1", "C3"}  # … but re-filtered for the rep

    other_reads = FakeReads(data={"open_invoices": []})
    other = BusinessDataGateway(FakeAdapter(tenant=OTHER_TENANT), scope_for("owner", tenant=OTHER_TENANT),
                                reads=other_reads, db=FakeDB(), cache=cache, clock=clock)
    theirs = await other.open_invoices()
    assert other_reads.calls == ["open_invoices"] and theirs.records == ()
    with pytest.raises(TenantMismatch):
        cache.put(TENANT, "x", theirs, clock())


@pytest.mark.asyncio
async def test_failed_reads_are_not_cached():
    cache = SnapshotCache()
    reads = FakeReads(fail={"open_bills": RuntimeError("boom")})
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=reads, db=FakeDB(), cache=cache, clock=clock)
    assert not (await gw.open_bills()).usable
    reads.fail.clear()
    assert (await gw.open_bills()).usable
    assert reads.calls == ["open_bills", "open_bills"]


@pytest.mark.asyncio
async def test_account_directory_rejects_cross_tenant_rows():
    rows = [{"qb_customer_id": "C1", "company_id": OTHER_TENANT, "territory": "N", "assigned_rep_id": None}]

    class LeakyDB(FakeDB):
        def table(self, name):
            q = super().table(name)
            q.eq = lambda *_a: q
            return q
    gw = BusinessDataGateway(FakeAdapter(), scope_for("owner"), reads=FakeReads(), db=LeakyDB(accounts=rows),
                             clock=clock)
    snap = await gw.account_directory()
    assert not snap.usable and snap.records == ()
