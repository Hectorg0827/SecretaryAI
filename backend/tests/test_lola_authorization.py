"""Lola organisational hierarchy, record/attribute-level access, delegations, fail-closed."""
from __future__ import annotations

import pytest

from lola_fakes import (
    BACK_OFFICE, CO_A, CO_B, EXEC_MGR, FINANCE, HR, INACTIVE, OTHER_MGR, OWNER, OWNER_B, REP1, REP2, REP3,
    SALES_MGR, VIEWER, FakeAdapter, delegate, seed,
)

from app.lola.authorization import (
    AuthorizationDenied, AuthorizationUnavailable, Authorizer, build_principal,
)
from app.lola.contracts import OrgLevel, ResourceType
from app.lola.tools import ToolContext, default_registry


def auth_for(db, uid, company=CO_A):
    return Authorizer(build_principal(db, company, uid), db)


def visible(db, uid, company=CO_A):
    a = auth_for(db, uid, company)
    return sorted(k for k in ("QB-1", "QB-2", "QB-3", "QB-4") if a.can_see_account(k))


async def run_tool(db, uid, tool, args=None, adapter=None):
    a = auth_for(db, uid)
    ctx = ToolContext(principal=a.p, authorizer=a, db=db, adapter=adapter or FakeAdapter())
    return await default_registry().get(tool).run(ctx, args or {})


# ── hierarchy & record level ────────────────────────────────────────────────

class TestHierarchy:
    def test_owner_sees_every_account(self):
        assert visible(seed(), OWNER) == ["QB-1", "QB-2", "QB-3", "QB-4"]

    def test_rep_sees_assigned_and_territory_only(self):
        # QB-1 assigned to REP1, QB-4 unassigned but in REP1's West territory.
        assert visible(seed(), REP1) == ["QB-1", "QB-4"]

    def test_rep_without_territory_sees_only_assigned(self):
        assert visible(seed(), REP2) == ["QB-2"]

    def test_department_manager_sees_reporting_line_not_peer_team(self):
        assert visible(seed(), SALES_MGR) == ["QB-1", "QB-2"]
        assert visible(seed(), OTHER_MGR) == ["QB-3"]

    def test_manager_without_profile_keeps_legacy_company_wide_access(self):
        assert visible(seed(), EXEC_MGR) == ["QB-1", "QB-2", "QB-3", "QB-4"]

    def test_viewer_sees_no_accounts(self):
        assert visible(seed(), VIEWER) == []
        assert not auth_for(seed(), VIEWER).can(ResourceType.CUSTOMER_ACCOUNT).allowed

    def test_reporting_tree_is_transitive_and_cycle_safe(self):
        db = seed()
        # Introduce a cycle OWNER → SALES_MGR → OWNER; must terminate.
        for m in db.tables["lola_org_members"]:
            if m["user_id"] == OWNER:
                m["manager_user_id"] = SALES_MGR
        p = build_principal(db, CO_A, OWNER)
        assert {SALES_MGR, REP1, REP2}.issubset(p.reports)
        assert OWNER not in p.reports


# ── privilege escalation & stale permissions ────────────────────────────────

class TestEscalation:
    def test_role_comes_from_database_not_token(self):
        db = seed()
        # Whatever the token says, REP1 is a sales_rep in the database.
        assert build_principal(db, CO_A, REP1).role == "sales_rep"

    def test_demotion_takes_effect_immediately(self):
        db = seed()
        assert visible(db, EXEC_MGR) == ["QB-1", "QB-2", "QB-3", "QB-4"]
        db.rows("users", id=EXEC_MGR)[0]["role"] = "sales_rep"
        assert visible(db, EXEC_MGR) == []

    def test_deactivated_user_is_denied(self):
        with pytest.raises(AuthorizationDenied):
            build_principal(seed(), CO_A, INACTIVE)

    def test_cross_tenant_identity_is_denied(self):
        with pytest.raises(AuthorizationDenied):
            build_principal(seed(), CO_B, REP1)   # REP1 is not a member of company B

    def test_unknown_role_is_denied(self):
        db = seed()
        db.rows("users", id=REP1)[0]["role"] = "superadmin"
        with pytest.raises(AuthorizationDenied):
            build_principal(db, CO_A, REP1)

    def test_org_profile_cannot_lift_role(self):
        db = seed()
        for m in db.tables["lola_org_members"]:
            if m["user_id"] == REP1:
                m["org_level"] = "executive"
        p = build_principal(db, CO_A, REP1)
        assert p.org_level == OrgLevel.EMPLOYEE
        assert visible(db, REP1) == ["QB-1", "QB-4"]

    def test_unreadable_action_limit_means_no_authority(self):
        db = seed()
        for m in db.tables["lola_org_members"]:
            if m["user_id"] == SALES_MGR:
                m["action_limit"] = "lots"
        assert build_principal(db, CO_A, SALES_MGR).action_limit == 0


# ── fail closed ─────────────────────────────────────────────────────────────

class TestFailClosed:
    def test_user_source_outage_raises_unavailable(self):
        db = seed()
        db.fail.add("users")
        with pytest.raises(AuthorizationUnavailable):
            build_principal(db, CO_A, OWNER)

    def test_org_profile_outage_applies_least_privilege(self):
        db = seed()
        db.fail.add("lola_org_members")
        p = build_principal(db, CO_A, EXEC_MGR)
        assert p.org_profile_available is False and p.org_level == OrgLevel.EMPLOYEE
        assert visible(db, EXEC_MGR) == []          # manager narrowed to own (none)
        assert visible(db, OWNER) == ["QB-1", "QB-2", "QB-3", "QB-4"]  # owner stays owner

    def test_account_assignment_outage_hides_accounts_from_non_company_wide(self):
        db = seed()
        db.fail.add("accounts")
        assert visible(db, REP1) == []
        assert visible(db, OWNER) == ["QB-1", "QB-2", "QB-3", "QB-4"]

    def test_delegation_outage_ignores_delegations(self):
        db = seed()
        delegate(db, REP2, REP3, ["customer_account"])
        db.fail.add("lola_delegations")
        assert visible(db, REP3) == ["QB-3"]


# ── financial / HR / payroll ────────────────────────────────────────────────

class TestSensitiveData:
    @pytest.mark.parametrize("uid,allowed", [
        (OWNER, True), (EXEC_MGR, True), (FINANCE, True),
        (REP1, False), (BACK_OFFICE, False), (VIEWER, False), (HR, False),
    ])
    def test_receivables_access(self, uid, allowed):
        assert auth_for(seed(), uid).can(ResourceType.RECEIVABLES).allowed is allowed

    @pytest.mark.parametrize("uid,allowed", [
        (OWNER, True), (HR, True), (EXEC_MGR, False), (FINANCE, False), (REP1, False),
    ])
    def test_hr_access(self, uid, allowed):
        assert auth_for(seed(), uid).can(ResourceType.HR).allowed is allowed

    def test_hr_clearance_requires_hr_department(self):
        db = seed()
        for m in db.tables["lola_org_members"]:
            if m["user_id"] == HR:
                m["department"] = "sales"
        assert not auth_for(db, HR).can(ResourceType.HR).allowed

    def test_payroll_only_for_owner_with_hr_clearance(self):
        db = seed()
        assert auth_for(db, OWNER).can(ResourceType.PAYROLL).allowed
        assert not auth_for(db, HR).can(ResourceType.PAYROLL).allowed
        for m in db.tables["lola_org_members"]:
            if m["user_id"] == OWNER:
                m["hr_clearance"] = False
        assert not auth_for(db, OWNER).can(ResourceType.PAYROLL).allowed

    async def test_denied_tool_never_reads_the_source(self):
        adapter = FakeAdapter()
        r = await run_tool(seed(), REP1, "finance.receivables", adapter=adapter)
        assert r.status.value == "denied" and adapter.calls == []

    async def test_inventory_cost_redacted_without_financial_access(self):
        rep = await run_tool(seed(), REP1, "operations.inventory")
        fin = await run_tool(seed(), FINANCE, "operations.inventory")
        assert all("purchase_cost" not in i for i in rep.data["items"])
        assert "purchase_cost" in rep.authorization.redacted_fields
        assert all("purchase_cost" in i for i in fin.data["items"])

    async def test_customer_balances_redacted_for_rep(self):
        r = await run_tool(seed(), REP1, "sales.customer_accounts")
        names = [a["name"] for a in r.data["accounts"]]
        assert names == ["Acme Foods", "Delta Supply"]
        assert all("balance" not in a for a in r.data["accounts"])

    async def test_rep_sales_figures_only_cover_their_accounts(self):
        r = await run_tool(seed(), REP1, "sales.performance")
        assert r.data["order_count"] == 2 and r.data["total_revenue"] == 1200.0
        assert r.data["scope"] == "own"

    async def test_payroll_tool_is_unavailable_even_when_authorised(self):
        r = await run_tool(seed(), OWNER, "hr.payroll")
        assert r.status.value == "unavailable"

    async def test_source_failure_is_unavailable_not_empty(self):
        r = await run_tool(seed(), OWNER, "sales.customer_accounts", adapter=FakeAdapter(fail=True))
        assert r.status.value == "unavailable" and r.data is None

    async def test_every_tool_result_is_fully_structured(self):
        for tool in default_registry().names():
            r = await run_tool(seed(), OWNER, tool)
            assert r.status and r.authorization and r.started_at and r.finished_at
            if r.status.value == "answered":
                assert r.evidence, tool


# ── delegation ──────────────────────────────────────────────────────────────

class TestDelegation:
    def test_active_delegation_grants_grantors_accounts(self):
        db = seed()
        delegate(db, REP2, REP3, ["customer_account"])
        assert visible(db, REP3) == ["QB-2", "QB-3"]

    def test_expired_delegation_is_ignored(self):
        db = seed()
        delegate(db, REP2, REP3, ["customer_account"], days=-1)
        assert visible(db, REP3) == ["QB-3"]

    def test_revoked_delegation_is_ignored(self):
        db = seed()
        delegate(db, REP2, REP3, ["customer_account"], revoked=True)
        assert visible(db, REP3) == ["QB-3"]

    def test_delegation_dies_with_inactive_grantor(self):
        db = seed()
        delegate(db, REP2, REP3, ["customer_account"])
        db.rows("users", id=REP2)[0]["is_active"] = False
        assert visible(db, REP3) == ["QB-3"]

    def test_delegation_cannot_exceed_grantor(self):
        db = seed()
        # REP1 tries to hand over Cedar (QB-3), which REP1 cannot see.
        delegate(db, REP1, REP2, ["customer_account"], account_ids=["QB-3"])
        assert visible(db, REP2) == ["QB-2"]

    def test_delegation_cannot_grant_hr_or_payroll(self):
        db = seed()
        delegate(db, OWNER, REP1, ["hr", "payroll"])
        a = auth_for(db, REP1)
        assert not a.can(ResourceType.HR).allowed and not a.can(ResourceType.PAYROLL).allowed

    def test_financial_delegation_bounded_by_grantor_access(self):
        db = seed()
        delegate(db, FINANCE, REP1, ["receivables"])
        assert auth_for(db, REP1).can(ResourceType.RECEIVABLES).allowed
        # A grantor without financial access delegates nothing.
        db2 = seed()
        delegate(db2, REP2, REP1, ["receivables"])
        assert not auth_for(db2, REP1).can(ResourceType.RECEIVABLES).allowed

    def test_cross_tenant_delegation_is_ignored(self):
        db = seed()
        delegate(db, OWNER_B, REP1, ["customer_account", "receivables"], company=CO_B)
        assert not auth_for(db, REP1).can(ResourceType.RECEIVABLES).allowed
        assert visible(db, REP1) == ["QB-1", "QB-4"]
