"""
Regression tests for the pre-launch audit fixes.

Each class maps to one finding from the launch-readiness review so a future
change that re-opens a hole fails loudly here.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

from app.main import app  # noqa: E402
from app.api.deps import get_db  # noqa: E402
from app.auth.jwt import create_access_token  # noqa: E402

COMPANY_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
COMPANY_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _token(role: str = "owner", company_id: str = COMPANY_A, sub: str = "user-1") -> dict:
    tok = create_access_token({"sub": sub, "company_id": company_id, "role": role})
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def db():
    mock = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock
    yield mock
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Finding 1 — a company must not be able to attach another company's
# QuickBooks Desktop (Conductor) end-user ID.
# ─────────────────────────────────────────────────────────────────────────────

class TestQuickBooksDesktopOwnership:
    def _post(self, client, end_user_id="end_usr_victim", role="owner"):
        return client.post(
            "/api/settings/integrations/qbd",
            json={"end_user_id": end_user_id},
            headers=_token(role=role),
        )

    def test_id_belonging_to_another_company_is_rejected(self, client, db):
        with patch("app.api.setup.settings") as st, \
             patch("app.api.setup._conductor_get", new=AsyncMock(return_value={"id": "end_usr_victim", "sourceId": COMPANY_B})):
            st.conductor_api_key = "ck_test"
            r = self._post(client)
        assert r.status_code == 403
        db.table.return_value.update.assert_not_called()

    def test_unverifiable_id_is_rejected(self, client, db):
        """Conductor down / unknown ID → fail closed, never save."""
        with patch("app.api.setup.settings") as st, \
             patch("app.api.setup._conductor_get", new=AsyncMock(side_effect=RuntimeError("boom"))):
            st.conductor_api_key = "ck_test"
            r = self._post(client)
        assert r.status_code == 403
        db.table.return_value.update.assert_not_called()

    def test_no_operator_key_means_no_manual_attach(self, client, db):
        with patch("app.api.setup.settings") as st:
            st.conductor_api_key = ""
            r = self._post(client)
        assert r.status_code == 403

    def test_path_tampering_id_is_rejected_without_calling_conductor(self, client, db):
        getter = AsyncMock(return_value={"sourceId": COMPANY_A})
        with patch("app.api.setup.settings") as st, patch("app.api.setup._conductor_get", new=getter):
            st.conductor_api_key = "ck_test"
            r = self._post(client, end_user_id="x/../end-users/other")
        assert r.status_code == 403
        getter.assert_not_called()

    def test_own_id_is_accepted(self, client, db):
        db.table.return_value.select.return_value.eq.return_value.neq.return_value.execute.return_value.data = []
        with patch("app.api.setup.settings") as st, \
             patch("app.api.setup._conductor_get", new=AsyncMock(return_value={"id": "end_usr_mine", "sourceId": COMPANY_A})):
            st.conductor_api_key = "ck_test"
            r = self._post(client, end_user_id="end_usr_mine")
        assert r.status_code == 200
        saved = db.table.return_value.update.call_args[0][0]
        assert saved["conductor_end_user_id"] == "end_usr_mine"
        assert saved["qb_type"] == "desktop"

    def test_id_already_used_by_another_company_is_rejected(self, client, db):
        db.table.return_value.select.return_value.eq.return_value.neq.return_value.execute.return_value.data = [{"id": COMPANY_B}]
        with patch("app.api.setup.settings") as st, \
             patch("app.api.setup._conductor_get", new=AsyncMock(return_value={"sourceId": COMPANY_A})):
            st.conductor_api_key = "ck_test"
            r = self._post(client, end_user_id="end_usr_mine")
        assert r.status_code == 403
        db.table.return_value.update.assert_not_called()

    def test_manager_cannot_attach(self, client, db):
        assert self._post(client, role="manager").status_code == 403

    @pytest.mark.parametrize("role,visible", [("owner", True), ("manager", False), ("viewer", False)])
    def test_end_user_id_only_shown_to_integration_managers(self, client, db, role, visible):
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
            "qb_type": "desktop", "qb_connection_status": "connected",
            "conductor_end_user_id": "end_usr_mine",
        }]
        r = client.get("/api/settings/integrations", headers=_token(role=role))
        assert r.status_code == 200
        qbd = r.json()["quickbooks_desktop"]
        assert qbd["connected"] is True
        assert (qbd["end_user_id"] == "end_usr_mine") is visible

    def test_wizard_marks_company_as_desktop_when_conductor_is_active(self, client, db):
        db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "conductor_end_user_id": "end_usr_mine"}

        async def fake_get(path, params=None):
            if path.endswith("/integration-connections"):
                return {"data": [{"integrationSlug": "quickbooks-desktop", "status": "active"}]}
            return {"id": "end_usr_mine", "sourceId": COMPANY_A}

        with patch("app.api.setup.settings") as st, patch("app.api.setup._conductor_get", new=fake_get):
            st.conductor_api_key = "ck_test"
            r = client.get("/api/setup/qb-desktop/status", headers=_token())
        assert r.status_code == 200 and r.json()["connected"] is True
        saved = db.table.return_value.update.call_args[0][0]
        assert saved == {"qb_type": "desktop", "qb_connection_status": "connected"}


# ─────────────────────────────────────────────────────────────────────────────
# Finding 9 — roles: no wildcard, owner-only account powers.
# ─────────────────────────────────────────────────────────────────────────────

class TestRoles:
    @pytest.mark.parametrize("perm", ["manage_users", "manage_billing", "manage_integrations"])
    def test_manager_lacks_owner_only_powers(self, perm):
        from app.auth.rbac import has_permission, require_permission
        assert has_permission("owner", perm)
        assert not has_permission("manager", perm)
        with pytest.raises(HTTPException) as exc:
            require_permission(perm)(user={"role": "manager"})
        assert exc.value.status_code == 403

    def test_unknown_permission_is_denied_even_for_owner(self):
        from app.auth.rbac import has_permission
        assert not has_permission("owner", "no_such_permission")

    def test_unknown_role_gets_viewer_or_less(self):
        from app.auth.rbac import has_permission
        assert not has_permission("intern", "view_dashboard")
        assert not has_permission(None, "approve_actions")

    def test_manager_keeps_day_to_day_powers(self):
        from app.auth.rbac import has_permission
        for perm in ("approve_actions", "trigger_actions", "view_financials", "manage_inventory", "view_inventory"):
            assert has_permission("manager", perm), perm

    @pytest.mark.parametrize("method,path", [
        ("get", "/auth/qbo/connect-url"),
        ("delete", "/auth/qbo/disconnect"),
        ("get", "/auth/gmail/connect-url"),
        ("delete", "/auth/gmail/disconnect"),
        ("delete", "/api/settings/integrations/qbd"),
        ("post", "/api/connectors/generate-install-secret"),
        ("post", "/api/billing/portal"),
    ])
    @pytest.mark.parametrize("role", ["viewer", "sales_rep", "back_office", "manager"])
    def test_non_owner_cannot_change_integrations_or_billing(self, client, db, method, path, role):
        r = getattr(client, method)(path, headers=_token(role=role))
        assert r.status_code == 403, (role, path, r.status_code)

    @pytest.mark.parametrize("role", ["viewer", "sales_rep", "back_office"])
    def test_non_approver_cannot_create_po_draft(self, client, db, role):
        r = client.post("/api/actions/draft-po", json={"item_id": "i", "product_name": "p"}, headers=_token(role=role))
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Finding 7 — screen control is off by default and approver-only.
# ─────────────────────────────────────────────────────────────────────────────

class TestComputerUseLockedDown:
    def test_disabled_by_default(self):
        from app.config import Settings
        assert Settings.model_fields["computer_use_enabled"].default is False

    @pytest.mark.parametrize("role", ["viewer", "sales_rep", "back_office"])
    @pytest.mark.parametrize("path,body", [
        ("/api/computer-use/start", {"app_name": "x", "task": "y"}),
        ("/api/computer-use/approve", {"session_id": "s", "approval_id": "a"}),
        ("/api/computer-use/reject", {"session_id": "s", "approval_id": "a"}),
    ])
    def test_non_approver_cannot_use_any_endpoint(self, client, db, role, path, body):
        assert client.post(path, json=body, headers=_token(role=role)).status_code == 403

    def test_start_refused_when_disabled(self, client, db):
        with patch("app.api.computer_use.settings") as st:
            st.computer_use_enabled = False
            r = client.post("/api/computer-use/start", json={"app_name": "x", "task": "y"}, headers=_token())
        assert r.status_code == 403


class TestChatDoesNotLeakFinancials:
    """Finding 9 — a sales rep must not get company revenue through chat."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role,expect_money", [("owner", True), ("manager", True), ("sales_rep", False)])
    async def test_sales_summary_only_for_financial_roles(self, mock_adapter, role, expect_money):
        from app.api.chat import _fetch_data_for_intent
        data = await _fetch_data_for_intent("sales_report", "how are sales?", mock_adapter, role=role)
        assert ("sales_summary" in data) is expect_money


# ─────────────────────────────────────────────────────────────────────────────
# Findings 3 + 4 — the audit trail really records, and approval is honest.
# These use an in-memory database (tests/fake_supabase.py) so the assertions
# are about what ended up stored, not about which mocks were called.
# ─────────────────────────────────────────────────────────────────────────────

from fake_supabase import FakeSupabase  # noqa: E402
from app.api.deps import get_adapter  # noqa: E402

USER_ID = "99999999-9999-9999-9999-999999999999"


def _draft(action_type="email_reply", content=None, status="pending", company_id=COMPANY_A, draft_id="d-1"):
    return {
        "id": draft_id, "company_id": company_id, "created_by": "ai", "action_type": action_type,
        "status": status,
        "content": content if content is not None else {"to": "buyer@acme.com", "subject": "Re: order", "body": "Thanks!"},
    }


@pytest.fixture
def fdb():
    """In-memory DB wired into the app, with an adapter spy."""
    db = FakeSupabase(drafts=[], audit_events=[], policy_rules=[], company_features=[])
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(return_value={"status": "sent"})
    adapter.create_purchase_order = AsyncMock(return_value=MagicMock(qb_id="PO-77"))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_adapter] = lambda: adapter
    db.adapter = adapter
    yield db
    app.dependency_overrides.clear()


def _approve(client, draft_id="d-1", body=None, role="owner", company_id=COMPANY_A):
    return client.post(f"/api/actions/approve/{draft_id}", json=body or {},
                       headers=_token(role=role, company_id=company_id, sub=USER_ID))


class TestAuditWriter:
    def test_row_matches_the_audit_table(self):
        """Every key written must be a real audit_events column."""
        from app.utils.audit import write_audit_event
        db = FakeSupabase()
        write_audit_event(db, company_id=COMPANY_A, event_type="draft_approved", actor_id=USER_ID,
                          action_class="commit", approved_by=USER_ID, metadata={"draft_id": "d"})
        row = db.rows("audit_events")[0]
        allowed = {"id", "company_id", "event_type", "description", "metadata", "created_at", "actor_id",
                   "user_id", "action_class", "capability", "path_used", "before_snapshot",
                   "after_snapshot", "policy_rule_id", "approved_by"}
        assert set(row) <= allowed
        assert row["description"] and row["user_id"] == USER_ID

    def test_action_type_is_never_written_as_action_class(self):
        """The old bug: 'email_reply' in action_class broke the DB check constraint."""
        from app.utils.audit import write_audit_event
        db = FakeSupabase()
        write_audit_event(db, company_id=COMPANY_A, event_type="x", action_class="email_reply")
        row = db.rows("audit_events")[0]
        assert "action_class" not in row
        assert row["metadata"]["action_type"] == "email_reply"

    def test_system_actor_is_not_written_to_the_uuid_column(self):
        from app.utils.audit import write_audit_event
        db = FakeSupabase()
        write_audit_event(db, company_id=COMPANY_A, event_type="email_sent", actor_id="scheduler")
        row = db.rows("audit_events")[0]
        assert row["actor_id"] == "scheduler" and "user_id" not in row

    def test_required_write_failure_raises(self):
        from app.utils.audit import AuditWriteError, write_audit_event
        db = FakeSupabase()
        db.fail_tables.add("audit_events")
        with pytest.raises(AuditWriteError):
            write_audit_event(db, company_id=COMPANY_A, event_type="draft_approved", required=True)

    def test_optional_write_failure_returns_none(self):
        from app.utils.audit import write_audit_event
        db = FakeSupabase()
        db.fail_tables.add("audit_events")
        assert write_audit_event(db, company_id=COMPANY_A, event_type="user_login") is None


class TestApprovalIsHonest:
    def test_approved_email_is_sent_logged_and_marked(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        r = _approve(client)
        assert r.status_code == 200, r.text
        assert r.json()["execution_status"] == "executed"
        fdb.adapter.send_email_reply.assert_awaited_once_with(to="buyer@acme.com", subject="Re: order", body="Thanks!")
        d = fdb.rows("drafts")[0]
        assert d["status"] == "approved" and d["execution_status"] == "executed" and d["reviewed_by"] == USER_ID
        assert [e["event_type"] for e in fdb.events()] == ["draft_approved", "draft_executed"]
        assert fdb.events("draft_approved")[0]["action_class"] == "commit"

    @pytest.mark.parametrize("action_type", ["email_reply", "draft_customer_email", "send_customer_email", "send_vendor_email"])
    def test_every_email_draft_name_is_executed(self, client, fdb, action_type):
        """AI-created drafts used names the approval code ignored — approving did nothing."""
        fdb.tables["drafts"].append(_draft(action_type=action_type))
        assert _approve(client).json()["execution_status"] == "executed"
        fdb.adapter.send_email_reply.assert_awaited_once()

    def test_failed_send_is_not_shown_as_approved(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.adapter.send_email_reply = AsyncMock(side_effect=RuntimeError("mailbox not connected"))
        r = _approve(client)
        assert r.status_code == 200
        assert r.json()["status"] == "pending" and r.json()["execution_status"] == "failed"
        d = fdb.rows("drafts")[0]
        assert d["status"] == "pending" and d["execution_status"] == "failed"
        assert "mailbox not connected" in d["execution_detail"]
        assert [e["event_type"] for e in fdb.events()] == ["draft_approved", "draft_execution_failed"]

    def test_failed_draft_can_be_retried(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.adapter.send_email_reply = AsyncMock(side_effect=[RuntimeError("temporary"), {"status": "sent"}])
        assert _approve(client).json()["execution_status"] == "failed"
        assert _approve(client).json()["execution_status"] == "executed"
        assert fdb.rows("drafts")[0]["status"] == "approved"

    def test_email_without_recipient_is_not_sent(self, client, fdb):
        fdb.tables["drafts"].append(_draft(content={"subject": "x", "body": "y"}))
        r = _approve(client)
        assert r.json()["execution_status"] == "failed"
        fdb.adapter.send_email_reply.assert_not_called()
        assert fdb.rows("drafts")[0]["status"] == "pending"

    def test_nothing_is_sent_when_the_audit_log_is_down(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.fail_tables.add("audit_events")
        r = _approve(client)
        assert r.status_code == 503
        fdb.adapter.send_email_reply.assert_not_called()
        assert fdb.rows("drafts")[0]["status"] == "pending"

    def test_nothing_is_approved_when_policy_cannot_be_checked(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.fail_tables.add("policy_rules")
        r = _approve(client)
        assert r.status_code == 503
        fdb.adapter.send_email_reply.assert_not_called()
        assert fdb.rows("drafts")[0]["status"] == "pending"

    def test_deny_rule_blocks_and_is_logged(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.tables["policy_rules"].append({
            "id": "11111111-2222-3333-4444-555555555555", "company_id": COMPANY_A, "is_active": True,
            "priority": 1, "action_class": "commit", "effect": "deny", "rule_name": "no_commits"})
        r = _approve(client)
        assert r.status_code == 403
        fdb.adapter.send_email_reply.assert_not_called()
        assert fdb.rows("drafts")[0]["status"] == "pending"
        assert [e["event_type"] for e in fdb.events()] == ["policy_blocked"]

    def test_rule_can_restrict_approval_to_owner(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.tables["policy_rules"].append({
            "id": "11111111-2222-3333-4444-555555555555", "company_id": COMPANY_A, "is_active": True,
            "priority": 1, "action_class": "commit", "effect": "require_approval",
            "approval_roles": ["owner"], "rule_name": "owner_only"})
        assert _approve(client, role="manager").status_code == 403
        fdb.adapter.send_email_reply.assert_not_called()
        assert _approve(client, role="owner").json()["execution_status"] == "executed"

    def test_second_approval_does_not_send_twice(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        assert _approve(client).json()["execution_status"] == "executed"
        again = _approve(client).json()
        assert again.get("already_processed") is True
        assert fdb.adapter.send_email_reply.await_count == 1

    def test_losing_the_race_does_not_send(self, client, fdb):
        """Two approvals read 'pending' at once: the claim lets only one through."""
        fdb.tables["drafts"].append(_draft())
        from app.actions.approval_queue import ApprovalQueue
        with patch.object(ApprovalQueue, "claim", new=AsyncMock(return_value=False)):
            r = _approve(client)
        assert r.json().get("already_processed") is True
        fdb.adapter.send_email_reply.assert_not_called()
        assert fdb.events() == []

    def test_claim_is_a_compare_and_set_on_pending(self):
        import asyncio
        from app.actions.approval_queue import ApprovalQueue
        db = FakeSupabase(drafts=[_draft()])
        q = ApprovalQueue(db)
        assert asyncio.run(q.claim("d-1", COMPANY_A, USER_ID)) is True
        assert asyncio.run(q.claim("d-1", COMPANY_A, USER_ID)) is False
        assert asyncio.run(q.claim("d-1", COMPANY_B, USER_ID)) is False

    def test_other_companys_draft_is_invisible(self, client, fdb):
        fdb.tables["drafts"].append(_draft(company_id=COMPANY_B))
        assert _approve(client).status_code == 404
        fdb.adapter.send_email_reply.assert_not_called()
        assert fdb.rows("drafts")[0]["status"] == "pending"

    def test_edited_content_is_what_gets_sent_and_stored(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        edited = {"to": "new@acme.com", "subject": "S", "body": "B"}
        r = _approve(client, body={"edited_content": edited})
        assert r.json()["status"] == "edited_and_approved"
        fdb.adapter.send_email_reply.assert_awaited_once_with(to="new@acme.com", subject="S", body="B")
        assert fdb.rows("drafts")[0]["content"] == edited

    def test_po_without_vendor_is_not_created_and_says_so(self, client, fdb):
        fdb.tables["drafts"].append(_draft(action_type="purchase_order",
                                           content={"item_id": "i1", "product_name": "Cola 12pk", "qty": 40}))
        r = _approve(client)
        assert r.json()["execution_status"] == "manual_required"
        assert "NOT created" in r.json()["execution_detail"]
        fdb.adapter.create_purchase_order.assert_not_called()
        assert fdb.rows("drafts")[0]["status"] == "approved"

    def test_fully_specified_po_is_created_in_quickbooks(self, client, fdb):
        fdb.tables["drafts"].append(_draft(action_type="draft_purchase_order", content={
            "vendor_id": "V1", "line_items": [{"item_id": "i1", "quantity": 40, "unit_cost": 5}]}))
        r = _approve(client)
        assert r.json()["execution_status"] == "executed"
        kwargs = fdb.adapter.create_purchase_order.await_args.kwargs
        assert kwargs["vendor_id"] == "V1" and kwargs["line_items"][0]["quantity"] == 40

    def test_unknown_action_type_never_runs_anything(self, client, fdb):
        fdb.tables["drafts"].append(_draft(action_type="cu_submit_form", content={}))
        r = _approve(client)
        assert r.json()["execution_status"] == "manual_required"
        fdb.adapter.send_email_reply.assert_not_called()
        fdb.adapter.create_purchase_order.assert_not_called()

    def test_reject_is_logged_and_cannot_hit_an_executing_draft(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        r = client.post("/api/actions/reject/d-1", json={"reason": "no"}, headers=_token(sub=USER_ID))
        assert r.json()["status"] == "rejected"
        assert [e["event_type"] for e in fdb.events()] == ["draft_rejected"]
        fdb.tables["drafts"][0]["status"] = "executing"
        r = client.post("/api/actions/reject/d-1", json={}, headers=_token(sub=USER_ID))
        assert r.json().get("already_processed") is True
        assert fdb.rows("drafts")[0]["status"] == "executing"

    def test_creating_a_draft_stores_it_and_logs_it(self, client, fdb):
        r = client.post("/api/actions/draft-po", json={"item_id": "i1", "product_name": "Cola", "qty": 3},
                        headers=_token(sub=USER_ID))
        assert r.status_code == 200
        d = fdb.rows("drafts")[0]
        assert d["status"] == "pending" and d["expires_at"]
        assert [e["event_type"] for e in fdb.events()] == ["draft_created"]


class TestAutomaticSendsAreRecorded:
    """Findings 4 + 12 — automatic emails go through the gate and are logged."""

    def test_system_email_is_audited_on_success(self):
        from app.utils import mailer
        db = FakeSupabase()
        fake_sg = MagicMock()
        fake_sg.return_value.send.return_value = MagicMock(status_code=202)
        with patch.object(mailer, "get_settings") as gs, patch("sendgrid.SendGridAPIClient", fake_sg):
            gs.return_value.sendgrid_api_key = "SG.x"
            gs.return_value.from_email = "s@x.test"
            assert mailer.send_system_email(db, company_id=COMPANY_A, to="o@acme.com", subject="Hi",
                                            text="body", kind="morning_briefing", actor="scheduler") is True
        ev = db.events("email_sent")[0]
        assert ev["actor_id"] == "scheduler" and ev["metadata"]["kind"] == "morning_briefing"
        assert ev["metadata"]["to"] == "o@acme.com"
        assert "body" not in ev["metadata"]  # message text is never copied into the log

    def test_system_email_failure_is_audited_and_reported(self):
        from app.utils import mailer
        db = FakeSupabase()
        with patch.object(mailer, "get_settings") as gs:
            gs.return_value.sendgrid_api_key = ""
            assert mailer.send_system_email(db, company_id=COMPANY_A, to="o@acme.com", subject="Hi",
                                            text="b", kind="low_stock_alert") is False
        assert [e["event_type"] for e in db.events()] == ["email_send_failed"]

    @pytest.mark.asyncio
    async def test_low_stock_alert_goes_through_the_engine_and_is_logged(self):
        from app.actions.engine import ActionEngine
        from app.scheduler.inventory_alert_check import check_and_alert_inventory
        db = FakeSupabase()
        adapter = AsyncMock()
        adapter.get_inventory_merged.return_value = [
            {"product_name": "Cola 12pk", "warehouse_qty": 0, "qb_qty": 2, "weekly_sell_rate": 10}]
        with patch("app.utils.mailer.send_system_email", return_value=True) as send:
            result = await check_and_alert_inventory(COMPANY_A, adapter, ActionEngine(db), "scheduler",
                                                     alert_email="owner@acme.com")
        assert result["alerts_fired"] == 1
        assert send.call_args.kwargs["to"] == "owner@acme.com"
        statuses = [r["status"] for r in db.rows("action_log")]
        assert statuses == ["pending", "executed"]

    @pytest.mark.asyncio
    async def test_undelivered_alert_is_not_logged_as_executed(self):
        from app.actions.engine import ActionEngine
        db = FakeSupabase()
        with patch("app.utils.mailer.send_system_email", return_value=False):
            await ActionEngine(db).process("send_low_stock_alert",
                                           {"to_email": "o@acme.com", "subject": "s", "body": "b"}, COMPANY_A, "scheduler")
        assert [r["status"] for r in db.rows("action_log")] == ["pending", "failed"]

    @pytest.mark.asyncio
    async def test_engine_refuses_to_act_when_it_cannot_log(self):
        from app.actions.engine import ActionEngine
        db = FakeSupabase()
        db.fail_tables.add("action_log")
        with patch("app.utils.mailer.send_system_email", return_value=True) as send:
            with pytest.raises(Exception):
                await ActionEngine(db).process("send_low_stock_alert",
                                               {"to_email": "o@acme.com", "subject": "s", "body": "b"}, COMPANY_A, "scheduler")
        send.assert_not_called()

    def test_direct_email_send_is_audited_before_and_after(self, client, fdb):
        r = client.post("/api/dashboard/emails/send-reply",
                        json={"email_id": "m1", "to": "buyer@acme.com", "subject": "Re", "body": "Hello"},
                        headers=_token(sub=USER_ID))
        assert r.status_code == 200, r.text
        assert [e["event_type"] for e in fdb.events()] == ["email_send_requested", "email_sent"]

    def test_direct_email_not_sent_when_audit_log_is_down(self, client, fdb):
        fdb.fail_tables.add("audit_events")
        r = client.post("/api/dashboard/emails/send-reply",
                        json={"email_id": "m1", "to": "buyer@acme.com", "subject": "Re", "body": "Hello"},
                        headers=_token(sub=USER_ID))
        assert r.status_code == 503
        fdb.adapter.send_email_reply.assert_not_called()

    def test_direct_email_rejects_bad_recipient(self, client, fdb):
        r = client.post("/api/dashboard/emails/send-reply",
                        json={"email_id": "m1", "to": "not-an-address", "subject": "Re", "body": "Hello"},
                        headers=_token(sub=USER_ID))
        assert r.status_code == 422
        fdb.adapter.send_email_reply.assert_not_called()
