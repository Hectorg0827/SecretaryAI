"""Lola memory isolation (company/user/team/sensitivity/stale access) and planner behaviour."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lola_fakes import (
    CO_A, CO_B, EXEC_MGR, FakeAdapter, HR, OWNER, OWNER_B, REP1, REP2, SALES_MGR, seed, user,
)

from app.lola.authorization import Authorizer, build_principal
from app.lola.contracts import LolaRequest
from app.lola.memory import LolaMemoryStore, MemoryRejected, MemoryUnavailable
from app.lola.orchestrator import LolaOrchestrator
from app.lola.planning import build_plan, detect_intents, parse_amount


def store(db, uid, company=CO_A):
    return LolaMemoryStore(db, Authorizer(build_principal(db, company, uid), db))


def contents(db, uid, company=CO_A):
    return [m["content"] for m in store(db, uid, company).recall()]


class TestIsolation:
    def test_user_memory_is_private(self):
        db = seed()
        store(db, REP1).remember("I prefer weekly summaries on Monday")
        assert contents(db, REP1) == ["I prefer weekly summaries on Monday"]
        assert contents(db, REP2) == [] and contents(db, OWNER) == []

    def test_team_memory_flows_up_the_reporting_line_only(self):
        db = seed()
        store(db, REP1).remember("Team goal: call every West account this week", scope="team")
        assert contents(db, SALES_MGR) and contents(db, OWNER)
        assert contents(db, REP2) == []

    def test_company_memory_requires_leadership(self):
        db = seed()
        with pytest.raises(MemoryRejected):
            store(db, REP1).remember("We close early on Fridays", scope="company")
        store(db, OWNER).remember("We close early on Fridays", scope="company")
        assert contents(db, REP2) == ["We close early on Fridays"]

    def test_confidential_memory_is_forced_private(self):
        db = seed()
        row = store(db, OWNER).remember("Our margin on olive oil is 42%", scope="company")
        assert row["scope"] == "user" and row["sensitivity"] == "confidential"
        assert contents(db, REP1) == [] and contents(db, EXEC_MGR) == []

    def test_restricted_memory_requires_hr_clearance_to_write(self):
        db = seed()
        with pytest.raises(MemoryRejected):
            store(db, SALES_MGR).remember("Ravi's salary is 50k")
        row = store(db, HR).remember("Ravi's salary review is in March")
        assert row["sensitivity"] == "restricted" and row["scope"] == "user"

    def test_restricted_memory_hidden_after_clearance_removed(self):
        db = seed()
        store(db, HR).remember("Payroll cutoff for contractors is the 25th")
        assert contents(db, HR)
        for m in db.tables["lola_org_members"]:
            if m["user_id"] == HR:
                m["hr_clearance"] = False
        assert contents(db, HR) == []

    def test_account_memory_hidden_after_reassignment(self):
        db = seed()
        store(db, REP1).remember("Acme wants deliveries before 9am", scope="team", entity_type="customer",
                                 entity_id="QB-1")
        assert contents(db, SALES_MGR)
        # Move Acme to the East team: SALES_MGR (and REP1) lose it.
        db.rows("accounts", id="acc-1")[0].update(assigned_rep_id=None, territory="East")
        assert contents(db, SALES_MGR) == [] and contents(db, REP1) == []

    def test_cannot_attach_memory_to_hidden_account(self):
        with pytest.raises(MemoryRejected):
            store(seed(), REP1).remember("Cedar is unhappy", entity_type="customer", entity_id="QB-3")

    def test_tenants_never_share_memory(self):
        db = seed()
        store(db, OWNER).remember("Company A pricing strategy notes", scope="company")
        assert contents(db, OWNER_B, CO_B) == []

    def test_forged_cross_tenant_row_is_ignored(self):
        db = seed()
        db.tables["lola_memories"].append({"id": "x", "company_id": CO_B, "owner_user_id": REP1, "scope": "user",
                                           "sensitivity": "internal", "content": "leak", "kind": "preference"})
        assert contents(db, REP1) == []

    def test_secrets_are_never_stored(self):
        with pytest.raises(MemoryRejected):
            store(seed(), REP1).remember("QuickBooks password: hunter2")

    def test_expired_and_deleted_memory_hidden(self):
        db = seed()
        s = store(db, REP1)
        row = s.remember("temp note")
        db.tables["lola_memories"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert contents(db, REP1) == []
        row2 = s.remember("second note")
        assert store(db, REP2).forget(row2["id"]) is False      # not the owner
        assert s.forget(row2["id"]) is True and contents(db, REP1) == []
        assert row["id"]

    def test_outage_is_reported(self):
        db = seed()
        db.fail.add("lola_memories")
        with pytest.raises(MemoryUnavailable):
            store(db, REP1).recall()


class TestMemoryThroughLola:
    async def test_remember_then_recall_in_conversation(self):
        db = seed()
        orch = LolaOrchestrator(db, FakeAdapter())
        r1 = await orch.handle(user(REP1), LolaRequest(message="Remember that I prefer bullet points"))
        assert "remember" in r1.answer.lower()
        r2 = await orch.handle(user(REP1), LolaRequest(message="What do you remember about my preferences?"))
        assert "bullet points" in r2.answer and r2.memory_used == 1
        r3 = await orch.handle(user(REP2), LolaRequest(message="What do you remember?"))
        assert "bullet points" not in r3.answer


class TestPlanning:
    def _auth(self, uid=OWNER):
        db = seed()
        return Authorizer(build_principal(db, CO_A, uid), db)

    @pytest.mark.parametrize("msg,expected", [
        ("Draft an email to Acme about their reorder", ["draft_email"]),
        ("Prepare a purchase order for 50 cases of olive oil", ["draft_purchase_order"]),
        ("What's our payroll this month?", ["payroll"]),
        ("Who is overdue?", ["receivables"]),
        ("good morning", ["general"]),
    ])
    def test_intents(self, msg, expected):
        found = detect_intents(msg)
        for e in expected:
            assert e in found
        if expected == ["draft_email"]:
            assert "draft_purchase_order" not in found

    def test_amount_parsing(self):
        assert parse_amount("a PO for $25k") == 25000
        assert parse_amount("about $1,250.50 total") == 1250.50
        assert parse_amount("no money here") is None

    def test_multi_intent_plan_has_one_step_per_tool(self):
        plan = build_plan("Sales and customers for Acme Foods, plus inventory and receivables", self._auth())
        tools = [s.tool for s in plan.steps]
        assert len(tools) == len(set(tools))
        assert {"sales.performance", "sales.customer_accounts", "operations.inventory",
                "finance.receivables"} <= set(tools)
        assert all(s.args.get("entity_ids") == ["QB-1"] for s in plan.steps
                   if s.tool in ("sales.performance", "finance.receivables"))

    def test_entities_only_resolved_within_visibility(self):
        plan = build_plan("How are Acme Foods and Cedar Trading doing?", self._auth(REP1))
        assert [e.display_name for e in plan.entities] == ["Acme Foods"]
        assert "Cedar Trading" in plan.unresolved_names
