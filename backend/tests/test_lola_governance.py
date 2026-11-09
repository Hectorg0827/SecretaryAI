"""Lola governed actions: idempotency, policy fail-closed, separation of duties, verified receipts."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from lola_fakes import (
    BACK_OFFICE, CO_A, EXEC_MGR, OWNER, REP1, REP2, SALES_MGR, FakeAdapter, seed, user,
)

from app.lola.authorization import Authorizer, build_principal
from app.lola.contracts import BusinessEntity, EntityType, LolaRequest, LolaStatus, RiskTier
from app.lola.governance import GovernedActionService, check_lola_approval
from app.lola.orchestrator import LolaOrchestrator


def svc(db, uid):
    return GovernedActionService(db, Authorizer(build_principal(db, CO_A, uid), db))


def acme():
    return BusinessEntity(entity_type=EntityType.CUSTOMER, entity_id="QB-1", display_name="Acme Foods")


def cedar():
    return BusinessEntity(entity_type=EntityType.CUSTOMER, entity_id="QB-3", display_name="Cedar Trading")


async def ask(db, uid, msg, **kw):
    return await LolaOrchestrator(db, FakeAdapter()).handle(user(uid), LolaRequest(message=msg, **kw))


class TestProposals:
    async def test_rep_email_draft_waits_for_approval_and_sends_nothing(self):
        db = seed()
        r = await ask(db, REP1, "Draft an email to Acme Foods about their reorder", client_request_id="k1")
        assert r.status == LolaStatus.PENDING_APPROVAL
        (receipt,) = r.receipts
        assert receipt.status == LolaStatus.PENDING_APPROVAL and receipt.verified is False
        (draft,) = db.tables["drafts"]
        assert draft["status"] == "pending" and draft["id"] == receipt.draft_id
        assert draft["content"]["_lola"]["requested_by"] == REP1
        assert "Nothing has been sent" in r.answer

    async def test_duplicate_request_returns_same_receipt_and_one_draft(self):
        db = seed()
        a = await ask(db, REP1, "Draft an email to Acme Foods about their reorder", client_request_id="k1")
        b = await ask(db, REP1, "Draft an email to Acme Foods about their reorder", client_request_id="k1",
                      conversation_id=a.conversation_id)
        assert a.receipts[0].receipt_id == b.receipts[0].receipt_id
        assert len(db.tables["drafts"]) == 1 and len(db.tables["lola_action_receipts"]) == 1

    async def test_new_client_request_id_is_a_new_action(self):
        db = seed()
        a = await ask(db, REP1, "Draft an email to Acme Foods", client_request_id="k1")
        await ask(db, REP1, "Draft an email to Acme Foods", client_request_id="k2",
                  conversation_id=a.conversation_id)
        assert len(db.tables["drafts"]) == 2

    async def test_cannot_draft_for_hidden_account(self):
        db = seed()
        out = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=cedar())
        assert out.status == LolaStatus.DENIED and db.tables["drafts"] == []

    async def test_answer_only_mode_never_drafts(self):
        db = seed()
        r = await ask(db, REP1, "Draft an email to Acme Foods", allow_actions=False)
        assert r.proposals == [] and db.tables["drafts"] == []

    @pytest.mark.parametrize("action", ["access_payroll", "modify_financial_records", "send_external_email_direct",
                                        "delete_qb_data", "share_data_externally", "something_new"])
    async def test_prohibited_and_unknown_actions_are_refused(self, action):
        db = seed()
        out = await svc(db, OWNER).propose(action, {})
        assert out.status == LolaStatus.DENIED and db.tables["drafts"] == []
        assert db.tables["lola_action_receipts"] == []

    async def test_policy_outage_blocks_and_queues_nothing(self):
        db = seed()
        db.fail.add("policy_rules")
        out = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        assert out.status == LolaStatus.FAILED and "policy" in out.message
        assert db.tables["drafts"] == [] and db.tables["lola_action_receipts"] == []

    async def test_policy_deny_rule_is_enforced(self):
        db = seed()
        db.tables["policy_rules"].append({"id": "r1", "company_id": CO_A, "rule_name": "no_email",
                                          "action_class": "commit", "effect": "deny", "priority": 1,
                                          "is_active": True, "capabilities_match": ["email"]})
        out = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        assert out.status == LolaStatus.DENIED and db.tables["drafts"] == []

    async def test_receipt_store_outage_queues_nothing(self):
        db = seed()
        db.fail.add("lola_action_receipts")
        out = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        assert out.status == LolaStatus.FAILED and db.tables["drafts"] == []

    async def test_queue_outage_marks_receipt_failed(self):
        db = seed()
        db.fail.add("drafts")
        out = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        assert out.status == LolaStatus.FAILED and out.receipt.error_code == "queue_unavailable"

    async def test_large_purchase_order_is_high_risk(self):
        db = seed()
        out = await svc(db, BACK_OFFICE).propose("draft_purchase_order", {"instruction": "500 cases"},
                                                 amount=Decimal("25000"))
        assert out.proposal.risk_tier == RiskTier.HIGH_RISK
        assert out.proposal.requires_independent_approval is True

    async def test_amount_over_personal_limit_is_high_risk(self):
        db = seed()  # SALES_MGR action_limit = 5000
        out = await svc(db, SALES_MGR).propose("draft_purchase_order", {"instruction": "x"},
                                               amount=Decimal("6000"))
        assert out.proposal.risk_tier == RiskTier.HIGH_RISK

    async def test_routine_report_completes_with_verification(self):
        db = seed()
        out = await svc(db, BACK_OFFICE).propose("generate_internal_report", {"report_type": "weekly"})
        assert out.status == LolaStatus.COMPLETED
        assert out.receipt.verified is True and out.receipt.external_reference
        assert db.tables["drafts"] == []


class TestSafeRetry:
    async def test_retry_requeues_only_if_draft_missing(self):
        db = seed()
        s = svc(db, REP1)
        out = await s.propose("draft_customer_email", {"instruction": "x"}, entity=acme(), client_request_id="r")
        row = db.tables["lola_action_receipts"][0]
        # Simulate a crash after the receipt was written but before the queue.
        row["status"] = "draft"
        db.tables["drafts"].clear()
        again = await s.propose("draft_customer_email", {"instruction": "x"}, entity=acme(), client_request_id="r")
        assert again.duplicate and again.status == LolaStatus.PENDING_APPROVAL
        assert len(db.tables["drafts"]) == 1 and db.tables["drafts"][0]["id"] == out.receipt.draft_id
        # And with the draft present, a retry recovers without a second draft.
        row["status"] = "draft"
        await s.propose("draft_customer_email", {"instruction": "x"}, entity=acme(), client_request_id="r")
        assert len(db.tables["drafts"]) == 1


class TestApprovalRules:
    async def _draft(self, db, uid, action="draft_customer_email", amount=None, entity="acme"):
        ent = acme() if entity == "acme" else None
        out = await svc(db, uid).propose(action, {"instruction": "x"}, entity=ent, amount=amount,
                                         client_request_id=str(uuid.uuid4()))
        return db.rows("drafts", id=out.receipt.draft_id)[0]

    async def test_non_lola_drafts_are_unaffected(self):
        assert check_lola_approval(seed(), {"company_id": CO_A, "content": {"to": "x"}}, user(OWNER)) is None

    async def test_owner_can_approve_rep_draft(self):
        db = seed()
        draft = await self._draft(db, REP1)
        assert check_lola_approval(db, draft, user(OWNER)).allowed

    async def test_requester_cannot_self_approve_high_risk(self):
        db = seed()
        draft = await self._draft(db, OWNER, "draft_purchase_order", Decimal("25000"), entity=None)
        d = check_lola_approval(db, draft, user(OWNER))
        assert not d.allowed and "someone other" in d.reason

    async def test_high_risk_needs_leadership(self):
        db = seed()
        draft = await self._draft(db, BACK_OFFICE, "draft_purchase_order", Decimal("25000"), entity=None)
        assert not check_lola_approval(db, draft, user(SALES_MGR)).allowed   # department manager
        assert check_lola_approval(db, draft, user(EXEC_MGR)).allowed

    async def test_approver_limit_enforced(self):
        db = seed()
        draft = await self._draft(db, BACK_OFFICE, "draft_purchase_order", Decimal("6000"), entity=None)
        d = check_lola_approval(db, draft, user(SALES_MGR))   # limit 5000
        assert not d.allowed and "limit" in d.reason

    async def test_approver_must_see_the_account(self):
        db = seed()
        draft = await self._draft(db, REP1)  # Acme — not in OTHER_MGR's tree
        from lola_fakes import OTHER_MGR
        assert not check_lola_approval(db, draft, user(OTHER_MGR)).allowed

    async def test_role_without_approval_rights_cannot_approve(self):
        db = seed()
        draft = await self._draft(db, REP1)
        assert not check_lola_approval(db, draft, user(REP2)).allowed

    async def test_stale_requester_permissions_block_approval(self):
        db = seed()
        draft = await self._draft(db, REP1)
        # Acme is reassigned away from REP1 after the draft was made.
        db.rows("accounts", id="acc-1")[0].update(assigned_rep_id=REP2, territory="North")
        d = check_lola_approval(db, draft, user(OWNER))
        assert not d.allowed and "requester" in d.reason

    async def test_deactivated_requester_blocks_approval(self):
        db = seed()
        draft = await self._draft(db, REP1)
        db.rows("users", id=REP1)[0]["is_active"] = False
        assert not check_lola_approval(db, draft, user(OWNER)).allowed


class TestApprovalEndpoint:
    """The EXISTING /api/actions/approve endpoint enforces Lola's rules."""

    def _client(self, db):
        from app.api.deps import get_adapter, get_db
        from app.main import app
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_adapter] = lambda: FakeAdapter()
        return app, TestClient(app)

    def _token(self, uid, role):
        from app.auth.jwt import create_access_token
        return create_access_token({"sub": uid, "company_id": CO_A, "role": role})

    async def test_self_approval_of_high_risk_refused_and_nothing_runs(self):
        db = seed()
        out = await svc(db, OWNER).propose("draft_purchase_order", {"instruction": "x"}, amount=Decimal("25000"))
        app, client = self._client(db)
        try:
            r = client.post(f"/api/actions/approve/{out.receipt.draft_id}", json={},
                            headers={"Authorization": f"Bearer {self._token(OWNER, 'owner')}"})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 403
        assert db.rows("drafts", id=out.receipt.draft_id)[0]["status"] == "pending"

    async def test_approved_lola_draft_is_never_reported_as_executed(self):
        db = seed()
        out = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        app, client = self._client(db)
        try:
            r = client.post(f"/api/actions/approve/{out.receipt.draft_id}", json={},
                            headers={"Authorization": f"Bearer {self._token(OWNER, 'owner')}"})
        finally:
            app.dependency_overrides.clear()
        # Durable execution is not enabled for this action type: refused, nothing sent.
        assert r.status_code == 409
        receipt = db.tables["lola_action_receipts"][0]
        assert receipt["status"] == "pending_approval" and receipt["verified"] is False


class TestReceiptVerification:
    async def _pending(self, db):
        s = svc(db, REP1)
        out = await s.propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        return s, db.tables["lola_action_receipts"][0], out.receipt.draft_id

    async def test_approved_is_not_completed(self):
        db = seed()
        s, row, draft_id = await self._pending(db)
        db.rows("drafts", id=draft_id)[0].update(status="approved", reviewed_by=OWNER)
        assert s.refresh(row)["status"] == "approved"

    @pytest.mark.parametrize("state,ref,expected,verified", [
        ("queued", None, "executing", False),
        ("running", None, "executing", False),
        ("completed", None, "needs_reconciliation", False),
        ("completed", "gmail:msg-123", "completed", True),
        ("failed", None, "failed", False),
    ])
    async def test_status_comes_from_execution_ledger(self, state, ref, expected, verified):
        db = seed()
        s, row, draft_id = await self._pending(db)
        db.rows("drafts", id=draft_id)[0].update(status="approved", reviewed_by=OWNER)
        db.tables["agent_executions"].append({"id": "ex1", "company_id": CO_A, "draft_id": draft_id,
                                              "state": state, "external_reference": ref})
        new = s.refresh(row)
        assert new["status"] == expected and bool(new.get("verified")) is verified

    async def test_rejected_and_expired(self):
        db = seed()
        s, row, draft_id = await self._pending(db)
        db.rows("drafts", id=draft_id)[0]["status"] = "expired"
        assert s.refresh(row)["error_code"] == "approval_expired"

    async def test_receipts_are_private_to_requester_unless_approver(self):
        db = seed()
        _, row, _ = await self._pending(db)
        assert svc(db, REP2).get_receipt(row["id"]) is None
        assert svc(db, OWNER).get_receipt(row["id"]) is not None
        assert [r["id"] for r in svc(db, REP2).list_receipts()] == []

    async def test_history_is_append_only_trail(self):
        db = seed()
        s, row, draft_id = await self._pending(db)
        db.rows("drafts", id=draft_id)[0].update(status="approved", reviewed_by=OWNER)
        new = s.refresh(row)
        events = [h["event"] for h in new["history"]]
        assert events[0] == "proposed" and events[-1] == "verified_from_ledger"


class TestReviewerScope:
    """Receipts and pending Lola drafts carry business data: reviewers see only their team's."""

    async def test_department_manager_sees_own_team_only(self):
        from lola_fakes import OTHER_MGR, REP3
        db = seed()
        mine = await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        cedar_out = await svc(db, REP3).propose("draft_customer_email", {"instruction": "y"}, entity=cedar())
        assert {r["id"] for r in svc(db, SALES_MGR).list_receipts()} == {mine.receipt.receipt_id}
        assert {r["id"] for r in svc(db, OTHER_MGR).list_receipts()} == {cedar_out.receipt.receipt_id}
        assert len(svc(db, OWNER).list_receipts()) == 2
        assert svc(db, SALES_MGR).get_receipt(cedar_out.receipt.receipt_id) is None

    async def test_pending_list_filters_lola_drafts_keeps_legacy(self):
        from lola_fakes import REP3
        from app.lola.governance import visible_lola_drafts
        db = seed()
        await svc(db, REP3).propose("draft_customer_email", {"instruction": "y"}, entity=cedar())
        legacy = {"id": "legacy", "company_id": CO_A, "content": {"to": "a@b.c"}, "status": "pending"}
        drafts = db.tables["drafts"] + [legacy]
        assert [d["id"] for d in visible_lola_drafts(db, user(SALES_MGR), drafts)] == ["legacy"]
        assert len(visible_lola_drafts(db, user(OWNER), drafts)) == 2

    async def test_pending_list_hides_lola_drafts_when_authorization_down(self):
        from app.lola.governance import visible_lola_drafts
        db = seed()
        await svc(db, REP1).propose("draft_customer_email", {"instruction": "x"}, entity=acme())
        db.fail.add("users")
        assert visible_lola_drafts(db, user(OWNER), list(db.tables["drafts"])) == []

    async def test_manager_cannot_approve_other_teams_request(self):
        from lola_fakes import OTHER_MGR
        db = seed()
        out = await svc(db, BACK_OFFICE).propose("draft_purchase_order", {"instruction": "x"},
                                                 amount=Decimal("100"))
        draft = db.rows("drafts", id=out.receipt.draft_id)[0]
        d = check_lola_approval(db, draft, user(OTHER_MGR))
        assert not d.allowed and "team" in d.reason
        assert check_lola_approval(db, draft, user(EXEC_MGR)).allowed
