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
        # a clear "not done": the mail server could not even be reached
        fdb.adapter.send_email_reply = AsyncMock(side_effect=httpx.ConnectError("could not connect"))
        r = _approve(client)
        assert r.status_code == 200
        assert r.json()["status"] == "pending" and r.json()["execution_status"] == "failed"
        d = fdb.rows("drafts")[0]
        assert d["status"] == "pending" and d["execution_status"] == "failed"
        assert "was not sent" in d["execution_detail"]
        assert [e["event_type"] for e in fdb.events()] == ["draft_approved", "draft_execution_failed"]

    def test_failed_draft_can_be_retried(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.adapter.send_email_reply = AsyncMock(side_effect=[httpx.ConnectError("temporary"), {"status": "sent"}])
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
        assert kwargs["vendor_id"] == "V1"
        assert kwargs["line_items"] == [{"item_id": "i1", "description": "", "quantity": "40", "unit_cost": "5"}]

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


# ─────────────────────────────────────────────────────────────────────────────
# Findings 8 + 10 — staff accounts, passwords, and sessions that really end.
# ─────────────────────────────────────────────────────────────────────────────

OWNER_ID = "10000000-0000-0000-0000-000000000001"
STAFF_ID = "10000000-0000-0000-0000-000000000002"
OTHER_ID = "20000000-0000-0000-0000-000000000001"


@pytest.fixture
def adb():
    """One in-memory DB behind every way the auth code reaches the database."""
    from app.auth.jwt import hash_password
    db = FakeSupabase(
        users=[
            {"id": OWNER_ID, "company_id": COMPANY_A, "email": "owner@a.test", "role": "owner",
             "is_active": True, "password_hash": hash_password("Owner-pass1"), "name": "Olive Owner"},
            {"id": STAFF_ID, "company_id": COMPANY_A, "email": "staff@a.test", "role": "manager",
             "is_active": True, "password_hash": hash_password("Staff-pass1"), "name": "Sam Staff"},
            {"id": OTHER_ID, "company_id": COMPANY_B, "email": "owner@b.test", "role": "owner",
             "is_active": True, "password_hash": hash_password("Other-pass1"), "name": "Bea Other"},
        ],
        audit_events=[], companies=[{"id": COMPANY_A, "name": "A"}, {"id": COMPANY_B, "name": "B"}],
    )
    db.sent = []

    def fake_send(_db, *, company_id, to, subject, text=None, html=None, kind, actor="system"):
        db.sent.append({"to": to, "kind": kind, "text": text or html or ""})
        return True

    app.dependency_overrides[get_db] = lambda: db
    with patch("supabase.create_client", return_value=db), \
         patch("app.api.auth._db", return_value=db), \
         patch("app.api.deps.get_db", return_value=db), \
         patch("app.utils.mailer.send_system_email", side_effect=fake_send), \
         patch("app.api.users.send_system_email", side_effect=fake_send), \
         patch("app.api.users.email_configured", return_value=True):
        yield db
    app.dependency_overrides.clear()


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _link_token(db, kind):
    text = [m for m in db.sent if m["kind"] == kind][-1]["text"]
    return text.split("token=")[1].split()[0]


class TestLoginStillWorks:
    def test_correct_password_logs_in(self, client, adb):
        r = _login(client, "owner@a.test", "Owner-pass1")
        assert r.status_code == 200 and r.json()["role"] == "owner"
        assert client.get("/auth/me", headers=_bearer(r.json()["access_token"])).status_code == 200

    def test_wrong_password_and_unknown_email_look_the_same(self, client, adb):
        a = _login(client, "owner@a.test", "nope-nope1")
        b = _login(client, "nobody@a.test", "nope-nope1")
        assert a.status_code == b.status_code == 401 and a.json() == b.json()

    def test_password_hashes_from_the_old_library_still_verify(self):
        """A `$2b$` hash produced by passlib+bcrypt before the switch."""
        from app.auth.jwt import verify_password
        old = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"  # "secret"
        assert verify_password("secret", old) is True
        assert verify_password("Secret", old) is False

    def test_garbage_hash_never_matches(self):
        from app.auth.jwt import verify_password
        assert verify_password("x", "not-a-hash") is False
        assert verify_password("x", "") is False


class TestSessionsReallyEnd:
    def test_logout_kills_the_token_for_requests_and_refresh(self, client, adb):
        tok = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        assert client.post("/auth/logout", headers=_bearer(tok)).status_code == 200
        assert client.get("/auth/me", headers=_bearer(tok)).status_code == 401
        assert client.post("/auth/refresh", json={"access_token": tok}).status_code == 401

    def test_store_outage_refuses_instead_of_letting_everyone_in(self, client, adb):
        tok = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        import app.auth.jwt as jwt_mod
        broken = MagicMock()
        broken.exists.side_effect = ConnectionError("redis down")
        broken.get.side_effect = ConnectionError("redis down")
        broken.set.side_effect = ConnectionError("redis down")
        broken.setex.side_effect = ConnectionError("redis down")
        jwt_mod._redis_client = broken
        assert client.get("/auth/me", headers=_bearer(tok)).status_code == 503
        assert client.post("/auth/refresh", json={"access_token": tok}).status_code == 503
        assert client.post("/auth/logout", headers=_bearer(tok)).status_code == 503

    def test_operator_can_opt_into_fail_open(self, client, adb):
        tok = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        import app.auth.jwt as jwt_mod
        broken = MagicMock()
        broken.exists.side_effect = ConnectionError("redis down")
        broken.get.side_effect = ConnectionError("redis down")
        jwt_mod._redis_client = broken
        with patch.object(jwt_mod, "_fail_open", return_value=True):
            assert client.get("/auth/me", headers=_bearer(tok)).status_code == 200

    def test_refresh_token_is_single_use(self, client, adb):
        tok = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        first = client.post("/auth/refresh", json={"access_token": tok})
        assert first.status_code == 200
        assert client.post("/auth/refresh", json={"access_token": tok}).status_code == 401
        assert client.get("/auth/me", headers=_bearer(first.json()["access_token"])).status_code == 200

    def test_deactivated_user_is_out_immediately(self, client, adb):
        owner = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        staff = _login(client, "staff@a.test", "Staff-pass1").json()["access_token"]
        assert client.get("/auth/me", headers=_bearer(staff)).status_code == 200
        r = client.patch(f"/api/users/{STAFF_ID}", json={"is_active": False}, headers=_bearer(owner))
        assert r.status_code == 200 and r.json()["user"]["is_active"] is False
        assert client.get("/auth/me", headers=_bearer(staff)).status_code == 401
        assert client.post("/auth/refresh", json={"access_token": staff}).status_code == 401
        assert _login(client, "staff@a.test", "Staff-pass1").status_code == 403
        assert [e["event_type"] for e in adb.events() if e["event_type"].startswith("user_de")] == ["user_deactivated"]

    def test_demoted_user_loses_old_token_and_gets_new_role_on_login(self, client, adb):
        owner = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        staff = _login(client, "staff@a.test", "Staff-pass1").json()["access_token"]
        assert client.patch(f"/api/users/{STAFF_ID}", json={"role": "viewer"}, headers=_bearer(owner)).status_code == 200
        assert client.get("/auth/me", headers=_bearer(staff)).status_code == 401
        import time; time.sleep(0.01)
        assert _login(client, "staff@a.test", "Staff-pass1").json()["role"] == "viewer"

    def test_two_factor_guessing_is_throttled_per_account(self, client, adb):
        import pyotp
        from app.utils.encryption import encrypt
        from app.config import get_settings
        secret = pyotp.random_base32()
        adb.tables["users"][0].update(totp_enabled=True, totp_secret=encrypt(secret, get_settings().secret_key))
        codes = [_login_2fa(client, "000000") for _ in range(7)]
        assert codes[:5] == [401] * 5 and 429 in codes[5:]

    def test_two_factor_code_cannot_be_replayed(self, client, adb):
        import pyotp
        from app.utils.encryption import encrypt
        from app.config import get_settings
        secret = pyotp.random_base32()
        adb.tables["users"][0].update(totp_enabled=True, totp_secret=encrypt(secret, get_settings().secret_key))
        code = pyotp.TOTP(secret).now()
        assert _login_2fa(client, code) == 200
        assert _login_2fa(client, code) == 401


def _login_2fa(client, code):
    return client.post("/auth/login", json={"email": "owner@a.test", "password": "Owner-pass1", "totp_code": code}).status_code


class TestTeamManagement:
    def _owner(self, client):
        return _bearer(_login(client, "owner@a.test", "Owner-pass1").json()["access_token"])

    def test_list_shows_only_my_company_and_no_hashes(self, client, adb):
        r = client.get("/api/users", headers=self._owner(client))
        assert r.status_code == 200
        emails = {u["email"] for u in r.json()["users"]}
        assert emails == {"owner@a.test", "staff@a.test"}
        assert "password_hash" not in r.text and "$2b$" not in r.text

    @pytest.mark.parametrize("role", ["manager", "sales_rep", "back_office", "viewer"])
    def test_only_the_owner_manages_users(self, client, adb, role):
        hdr = _token(role=role, sub=STAFF_ID)
        assert client.get("/api/users", headers=hdr).status_code == 403
        assert client.post("/api/users/invite", json={"email": "x@a.test", "role": "viewer"}, headers=hdr).status_code == 403
        assert client.patch(f"/api/users/{OWNER_ID}", json={"role": "viewer"}, headers=hdr).status_code == 403

    def test_invite_then_accept_then_login(self, client, adb):
        hdr = self._owner(client)
        r = client.post("/api/users/invite", json={"email": "New@A.test", "role": "sales_rep", "name": "Nia New"}, headers=hdr)
        assert r.status_code == 201, r.text
        assert r.json()["email_sent"] is True and "invite_link" not in r.json()
        assert r.json()["user"]["pending_invite"] is True
        new = [u for u in adb.rows("users") if u["email"] == "new@a.test"][0]
        assert new["company_id"] == COMPANY_A and new["password_hash"] is None
        # cannot sign in before accepting
        assert _login(client, "new@a.test", "anything-1").status_code == 401

        token = _link_token(adb, "user_invite")
        # an invite link is not a login session
        assert client.get("/auth/me", headers=_bearer(token)).status_code == 401
        acc = client.post("/auth/accept-invite", json={"token": token, "password": "Brand-new1"})
        assert acc.status_code == 200 and acc.json()["role"] == "sales_rep"
        assert client.get("/auth/me", headers=_bearer(acc.json()["access_token"])).status_code == 200
        # the link works once
        assert client.post("/auth/accept-invite", json={"token": token, "password": "Brand-new2"}).status_code == 400
        assert _login(client, "new@a.test", "Brand-new1").status_code == 200
        types = [e["event_type"] for e in adb.events()]
        assert "user_invited" in types and "invite_accepted" in types

    def test_invite_link_is_returned_to_owner_when_email_is_not_set_up(self, client, adb):
        with patch("app.api.users.email_configured", return_value=False):
            r = client.post("/api/users/invite", json={"email": "p@a.test", "role": "viewer"}, headers=self._owner(client))
        assert r.status_code == 201 and r.json()["email_sent"] is False
        assert "/accept-invite?token=" in r.json()["invite_link"]

    def test_cannot_invite_an_existing_email_or_bad_role(self, client, adb):
        hdr = self._owner(client)
        assert client.post("/api/users/invite", json={"email": "owner@b.test", "role": "viewer"}, headers=hdr).status_code == 409
        assert client.post("/api/users/invite", json={"email": "z@a.test", "role": "admin"}, headers=hdr).status_code == 422

    def test_cannot_touch_another_companys_user(self, client, adb):
        hdr = self._owner(client)
        assert client.patch(f"/api/users/{OTHER_ID}", json={"is_active": False}, headers=hdr).status_code == 404
        assert client.post(f"/api/users/{OTHER_ID}/resend-invite", headers=hdr).status_code == 404
        assert [u for u in adb.rows("users") if u["id"] == OTHER_ID][0]["is_active"] is True

    def test_owner_cannot_lock_themselves_out(self, client, adb):
        hdr = self._owner(client)
        assert client.patch(f"/api/users/{OWNER_ID}", json={"is_active": False}, headers=hdr).status_code == 400
        assert client.patch(f"/api/users/{OWNER_ID}", json={"role": "viewer"}, headers=hdr).status_code == 400

    def test_last_owner_cannot_be_removed_by_a_second_owner(self, client, adb):
        hdr = self._owner(client)
        assert client.patch(f"/api/users/{STAFF_ID}", json={"role": "owner"}, headers=hdr).status_code == 200
        import time; time.sleep(0.01)
        second = _bearer(_login(client, "staff@a.test", "Staff-pass1").json()["access_token"])
        # two owners: one may demote the other …
        assert client.patch(f"/api/users/{OWNER_ID}", json={"role": "manager"}, headers=second).status_code == 200
        # … but the remaining owner cannot then be removed (by self-rule or count rule)
        assert client.patch(f"/api/users/{STAFF_ID}", json={"role": "manager"}, headers=second).status_code == 400


class TestPasswords:
    def test_forgot_password_answers_the_same_for_unknown_emails(self, client, adb):
        a = client.post("/auth/forgot-password", json={"email": "owner@a.test"})
        b = client.post("/auth/forgot-password", json={"email": "nobody@nowhere.test"})
        assert a.status_code == b.status_code == 200 and a.json() == b.json()
        assert [m["to"] for m in adb.sent] == ["owner@a.test"]

    def test_reset_changes_password_signs_out_and_link_dies(self, client, adb):
        old_session = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        client.post("/auth/forgot-password", json={"email": "owner@a.test"})
        token = _link_token(adb, "password_reset")
        assert client.get("/auth/me", headers=_bearer(token)).status_code == 401  # not a session
        r = client.post("/auth/reset-password", json={"token": token, "new_password": "Fresh-pass9"})
        assert r.status_code == 200, r.text
        assert client.get("/auth/me", headers=_bearer(old_session)).status_code == 401
        assert _login(client, "owner@a.test", "Owner-pass1").status_code == 401
        import time; time.sleep(0.01)
        assert _login(client, "owner@a.test", "Fresh-pass9").status_code == 200
        assert client.post("/auth/reset-password", json={"token": token, "new_password": "Again-pass9"}).status_code == 400
        assert "password_reset" in [e["event_type"] for e in adb.events()]

    def test_reset_link_dies_if_password_changed_another_way(self, client, adb):
        from app.auth.jwt import hash_password
        client.post("/auth/forgot-password", json={"email": "owner@a.test"})
        token = _link_token(adb, "password_reset")
        adb.tables["users"][0]["password_hash"] = hash_password("Changed-elsewhere1")
        assert client.post("/auth/reset-password", json={"token": token, "new_password": "Fresh-pass9"}).status_code == 400

    @pytest.mark.parametrize("scope_link", ["user_invite"])
    def test_a_link_of_one_kind_cannot_be_used_as_another(self, client, adb, scope_link):
        hdr = _bearer(_login(client, "owner@a.test", "Owner-pass1").json()["access_token"])
        client.post("/api/users/invite", json={"email": "k@a.test", "role": "viewer"}, headers=hdr)
        invite = _link_token(adb, "user_invite")
        assert client.post("/auth/reset-password", json={"token": invite, "new_password": "Fresh-pass9"}).status_code == 400
        assert client.post("/auth/verify-email", json={"token": invite}).status_code == 400

    def test_weak_password_is_rejected(self, client, adb):
        client.post("/auth/forgot-password", json={"email": "owner@a.test"})
        token = _link_token(adb, "password_reset")
        assert client.post("/auth/reset-password", json={"token": token, "new_password": "short"}).status_code == 422

    def test_change_password_keeps_this_device_and_drops_the_others(self, client, adb):
        here = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        elsewhere = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        bad = client.post("/auth/change-password", json={"current_password": "wrong-one1", "new_password": "Fresh-pass9"}, headers=_bearer(here))
        assert bad.status_code == 400
        r = client.post("/auth/change-password", json={"current_password": "Owner-pass1", "new_password": "Fresh-pass9"}, headers=_bearer(here))
        assert r.status_code == 200, r.text
        assert client.get("/auth/me", headers=_bearer(r.json()["access_token"])).status_code == 200
        assert client.get("/auth/me", headers=_bearer(elsewhere)).status_code == 401
        assert client.get("/auth/me", headers=_bearer(here)).status_code == 401

    def test_register_then_verify_email(self, client, adb):
        r = client.post("/auth/register", json={"company_name": "New Co", "email": "boss@new.test", "password": "Boss-pass1"})
        assert r.status_code == 201, r.text
        token = _link_token(adb, "email_verification")
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        row = [u for u in adb.rows("users") if u["email"] == "boss@new.test"][0]
        assert row["email_verified_at"] and row["role"] == "owner"

    def test_unverified_user_blocked_only_when_verification_is_required(self, client, adb):
        with patch("app.api.auth.settings") as st:
            st.require_email_verification = True
            st.secret_key = "test-secret-key-32-chars-long!!x"
            assert _login(client, "owner@a.test", "Owner-pass1").status_code == 403
        assert _login(client, "owner@a.test", "Owner-pass1").status_code == 200


class TestValidationErrorsDoNotCrash:
    def test_weak_password_at_signup_is_a_clear_422_not_a_500(self, client, adb):
        r = client.post("/auth/register", json={"company_name": "X", "email": "a@b.test", "password": "short"})
        assert r.status_code == 422
        assert r.json()["message"] == "Password must be at least 8 characters"
        assert "short" not in r.text  # the rejected password is not echoed back


class TestEncryptionKeySeparation:
    """Finding 11 — stored tokens no longer depend on the login-signing key."""
    SK = "test-secret-key-32-chars-long!!x"
    EK = "a-different-encryption-key-of-40-chars!!"

    def _settings(self, **kw):
        return MagicMock(secret_key=self.SK, encryption_key=kw.get("ek", ""), encryption_key_previous=kw.get("prev", ""))

    def test_without_a_dedicated_key_nothing_changes(self):
        from app.utils.encryption import decrypt, encrypt
        with patch("app.config.get_settings", return_value=self._settings()):
            assert decrypt(encrypt("tok", self.SK), self.SK) == "tok"

    def test_values_written_with_the_old_key_still_open_after_adding_a_key(self):
        from app.utils.encryption import decrypt, encrypt
        with patch("app.config.get_settings", return_value=self._settings()):
            old = encrypt("refresh-token", self.SK)
        with patch("app.config.get_settings", return_value=self._settings(ek=self.EK)):
            assert decrypt(old, self.SK) == "refresh-token"

    def test_new_values_cannot_be_opened_with_the_login_key_alone(self):
        from app.utils.encryption import decrypt, encrypt
        with patch("app.config.get_settings", return_value=self._settings(ek=self.EK)):
            new = encrypt("refresh-token", self.SK)
        with patch("app.config.get_settings", return_value=self._settings()):
            with pytest.raises(ValueError):
                decrypt(new, self.SK)

    def test_rotation_keeps_previous_key_readable(self):
        from app.utils.encryption import decrypt, encrypt
        with patch("app.config.get_settings", return_value=self._settings(ek=self.EK)):
            v = encrypt("t", self.SK)
        newer = "yet-another-encryption-key-of-40-chars!!"
        with patch("app.config.get_settings", return_value=self._settings(ek=newer, prev=self.EK)):
            assert decrypt(v, self.SK) == "t"

    def test_tampered_value_is_rejected_without_detail(self):
        from app.utils.encryption import decrypt, encrypt
        v = encrypt("t", self.SK)
        with pytest.raises(ValueError) as exc:
            decrypt(v[:-4] + "AAAA", self.SK)
        assert str(exc.value) == "Decryption failed"

    def test_same_value_for_both_keys_is_refused_at_startup(self):
        from app.config import Settings
        with pytest.raises(ValueError):
            Settings(secret_key=self.SK, encryption_key=self.SK, supabase_url="x", supabase_anon_key="x",
                     supabase_service_role_key="x", database_url="x", anthropic_api_key="x", _env_file=None)


class TestPaywall:
    """Finding 12 — the paywall exists, is one switch, and cannot strand customers."""

    def _sub(self, **kw):
        return {"status": "active", "updated_at": None, "trial_end": None, **kw}

    def test_rule(self):
        from datetime import datetime, timedelta, timezone
        from app.middleware.subscription_guard import subscription_allows_access as ok
        now = datetime.now(timezone.utc)
        iso = lambda d: (now + timedelta(days=d)).isoformat()
        assert ok(self._sub(status="active"))
        assert ok(self._sub(status="trialing", trial_end=iso(3)))
        assert not ok(self._sub(status="trialing", trial_end=iso(-1)))
        assert ok(self._sub(status="past_due", updated_at=iso(-3)))
        assert not ok(self._sub(status="canceled", updated_at=iso(-30)))
        assert not ok(None)

    def _client_with(self, enforced, subs):
        db = FakeSupabase(company_subscriptions=subs, feed_events=[], subscription_plans=[])
        app.dependency_overrides[get_db] = lambda: db
        return db

    def test_off_by_default_nothing_is_blocked(self, client):
        self._client_with(False, [])
        try:
            assert client.get("/api/feed/", headers=_token()).status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_when_on_unpaid_company_gets_402_but_can_still_reach_billing(self, client):
        self._client_with(True, [])
        try:
            with patch("app.config.get_settings") as gs:
                gs.return_value.billing_enforced = True
                assert client.get("/api/feed/", headers=_token()).status_code == 402
                assert client.get("/api/billing/subscription", headers=_token()).status_code == 200
                assert client.get("/api/billing/plans").status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_when_on_paid_and_trialing_companies_get_in_and_others_do_not(self, client):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        self._client_with(True, [
            {"company_id": COMPANY_A, "status": "trialing", "trial_end": future, "created_at": "2026-01-01"},
            {"company_id": COMPANY_B, "status": "canceled", "updated_at": "2026-01-01T00:00:00+00:00", "created_at": "2026-01-01"},
        ])
        try:
            with patch("app.config.get_settings") as gs:
                gs.return_value.billing_enforced = True
                assert client.get("/api/feed/", headers=_token(company_id=COMPANY_A)).status_code == 200
                # B's lapsed subscription does not ride on A's trial
                assert client.get("/api/feed/", headers=_token(company_id=COMPANY_B)).status_code == 402
        finally:
            app.dependency_overrides.clear()

    def test_cannot_switch_on_without_stripe(self):
        from app.config import Settings
        base = dict(secret_key="test-secret-key-32-chars-long!!x", supabase_url="x", supabase_anon_key="x",
                    supabase_service_role_key="x", database_url="x", anthropic_api_key="x", _env_file=None)
        with pytest.raises(ValueError):
            Settings(billing_enforced=True, **base)
        assert Settings(billing_enforced=True, stripe_secret_key="sk", stripe_webhook_secret="wh", **base).billing_enforced

    def test_registration_starts_a_trial(self, client, adb):
        r = client.post("/auth/register", json={"company_name": "Trial Co", "email": "t@trial.test", "password": "Trial-pass1"})
        assert r.status_code == 201
        subs = adb.rows("company_subscriptions")
        assert len(subs) == 1 and subs[0]["status"] == "trialing" and subs[0]["trial_end"]

    def test_no_pretend_checkout_in_production(self, client):
        db = FakeSupabase(subscription_plans=[{"id": "starter", "name": "Starter"}])
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.api.billing._stripe_available", False), patch("app.api.billing.settings") as st:
                st.debug = False
                r = client.post("/api/billing/checkout", json={"plan_id": "starter", "billing_period": "monthly"}, headers=_token())
            assert r.status_code == 503 and "mock" not in r.text
        finally:
            app.dependency_overrides.clear()


class TestWebhooksAndBackgroundJobs:
    """Finding 5 — notifications reach a job that exists, for the right company."""

    def test_every_dispatched_job_name_is_a_registered_job(self):
        """Approved POs and webhooks used to be sent to job names nobody handled."""
        import pathlib, re
        import celery_app  # registers every task module
        for mod in celery_app.app.conf.include:
            __import__(mod)
        registered = set(celery_app.app.tasks.keys())
        root = pathlib.Path(__file__).resolve().parent.parent
        used: dict[str, str] = {}
        for path in list((root / "app").rglob("*.py")) + list((root / "tasks").rglob("*.py")) + [root / "celery_app.py"]:
            if path.name == "base.py" and path.parent.name == "tasks":
                continue  # only mentions a test-patch path in a comment
            for name in re.findall(r'"(tasks\.[a-z_]+\.[a-z_]+)"', path.read_text()):
                used.setdefault(name, str(path.relative_to(root)))
        missing = {n: f for n, f in used.items() if n not in registered}
        assert not missing, f"jobs requested but never defined: {missing}"
        assert "tasks.data_refresh.refresh_company" in registered

    def _signed(self, body: bytes, token="verifier"):
        import base64, hashlib, hmac
        return base64.b64encode(hmac.new(token.encode(), body, hashlib.sha256).digest()).decode()

    def test_qbo_webhook_refreshes_only_the_matching_company(self, client):
        import json
        db = FakeSupabase(companies=[{"id": COMPANY_A, "qbo_realm_id": "111"}, {"id": COMPANY_B, "qbo_realm_id": "222"}])
        body = json.dumps({"eventNotifications": [
            {"realmId": "222", "dataChangeEvent": {"entities": [{"name": "Invoice", "id": "9"}, {"name": "Item", "id": "3"}]}},
            {"realmId": "999", "dataChangeEvent": {"entities": [{"name": "Invoice", "id": "1"}]}},
        ]}).encode()
        with patch("app.api.webhooks._db", return_value=db), patch("app.api.webhooks.settings") as st, \
             patch("app.api.webhooks._dispatch", return_value=True) as dispatch:
            st.intuit_webhook_verifier_token = "verifier"
            r = client.post("/webhooks/qbo", content=body,
                            headers={"intuit-signature": self._signed(body), "content-type": "application/json"})
        assert r.status_code == 200 and r.json()["refreshes_queued"] == 1
        dispatch.assert_called_once_with("tasks.data_refresh.refresh_company", company_id=COMPANY_B, reason="qbo_webhook")

    def test_qbo_webhook_with_bad_signature_is_rejected(self, client):
        with patch("app.api.webhooks.settings") as st, patch("app.api.webhooks._dispatch") as dispatch:
            st.intuit_webhook_verifier_token = "verifier"
            r = client.post("/webhooks/qbo", content=b"{}", headers={"intuit-signature": "nope"})
        assert r.status_code == 401
        dispatch.assert_not_called()

    def test_conductor_webhook_needs_its_own_secret_not_the_api_key(self, client):
        db = FakeSupabase(companies=[{"id": COMPANY_A, "conductor_end_user_id": "end_usr_1"}])
        payload = {"event": "sync.completed", "endUserId": "end_usr_1"}
        with patch("app.api.webhooks._db", return_value=db), patch("app.api.webhooks.settings") as st, \
             patch("app.api.webhooks._dispatch", return_value=True) as dispatch:
            st.conductor_api_key = "ck_master"
            st.conductor_webhook_secret = ""
            assert client.post("/webhooks/conductor", json=payload, headers={"X-Conductor-Secret": "ck_master"}).status_code == 401
            st.conductor_webhook_secret = "whsec_1"
            assert client.post("/webhooks/conductor", json=payload, headers={"X-Conductor-Secret": "ck_master"}).status_code == 401
            ok = client.post("/webhooks/conductor", json=payload, headers={"X-Conductor-Secret": "whsec_1"})
        assert ok.status_code == 200
        dispatch.assert_called_once_with("tasks.data_refresh.refresh_company", company_id=COMPANY_A, reason="conductor_webhook")

    def test_refresh_button_does_not_send_the_morning_email(self, client, db):
        with patch("tasks.data_refresh.refresh_company") as job, \
             patch("tasks.morning_briefing.send_morning_briefing_all") as briefing:
            r = client.post("/api/dashboard/refresh", headers=_token())
        assert r.status_code == 200
        job.apply_async.assert_called_once()
        briefing.apply_async.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Finding 6 — Outlook can be connected, stays connected, and replies thread.
# Microsoft is simulated at the HTTP level (httpx.MockTransport).
# ─────────────────────────────────────────────────────────────────────────────

import httpx  # noqa: E402


def _fake_microsoft(handler):
    """Patch the Outlook connector's HTTP client with a scripted Microsoft."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch("app.connectors.outlook.httpx.AsyncClient", side_effect=factory)


class TestOutlook:
    def test_connect_url_is_owner_only_and_needs_server_setup(self, client, db):
        assert client.get("/auth/outlook/connect-url", headers=_token(role="manager")).status_code == 403
        with patch("app.auth.oauth.settings") as st:
            st.ms_client_id = st.ms_client_secret = st.ms_redirect_uri = ""
            assert client.get("/auth/outlook/connect-url", headers=_token()).status_code == 503

    def test_connect_then_callback_stores_encrypted_tokens_for_the_right_company(self, client, adb):
        from app.utils.encryption import decrypt
        with patch("app.auth.oauth.settings") as st:
            st.ms_client_id, st.ms_client_secret = "cid", "csecret"
            st.ms_redirect_uri, st.ms_tenant = "https://api.test/auth/outlook/callback", "common"
            url = client.get("/auth/outlook/connect-url", headers=_token(company_id=COMPANY_A)).json()["url"]
        assert "login.microsoftonline.com/common" in url and "Mail.Send" in url and "offline_access" in url
        state = url.split("state=")[1].split("&")[0]

        tokens = {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600, "account_email": "ops@a.test"}
        with patch("app.auth.oauth.exchange_ms_code_for_tokens", new=AsyncMock(return_value=tokens)):
            r = client.get(f"/auth/outlook/callback?code=abc&state={state}", follow_redirects=False)
            assert r.status_code in (302, 307) and "outlook=connected" in r.headers["location"]
            # the state is single-use
            again = client.get(f"/auth/outlook/callback?code=abc&state={state}", follow_redirects=False)
            assert again.status_code == 400

        a = [c for c in adb.rows("companies") if c["id"] == COMPANY_A][0]
        b = [c for c in adb.rows("companies") if c["id"] == COMPANY_B][0]
        assert a["ms_access_token"] != "AT-1" and decrypt(a["ms_access_token"], "test-secret-key-32-chars-long!!x") == "AT-1"
        assert decrypt(a["ms_refresh_token"], "test-secret-key-32-chars-long!!x") == "RT-1"
        assert a["ms_account_email"] == "ops@a.test" and "ms_access_token" not in b
        assert any(e["event_type"] == "integration_connected" and e["metadata"]["integration"] == "outlook" for e in adb.events())

    def test_callback_with_unknown_state_is_rejected(self, client, adb):
        with patch("app.auth.oauth.exchange_ms_code_for_tokens", new=AsyncMock()) as ex:
            r = client.get("/auth/outlook/callback?code=abc&state=forged", follow_redirects=False)
        assert r.status_code == 400
        ex.assert_not_called()

    def test_a_google_state_cannot_be_used_on_the_outlook_callback(self, client, adb):
        with patch("app.api.auth.build_google_authorization_url", side_effect=lambda s: f"https://g/?state={s}"):
            url = client.get("/auth/gmail/connect-url", headers=_token()).json()["url"]
        state = url.split("state=")[1]
        r = client.get(f"/auth/outlook/callback?code=abc&state={state}", follow_redirects=False)
        assert r.status_code == 400

    def test_status_shows_outlook_and_disconnect_clears_tokens(self, client, adb):
        adb.tables["companies"][0].update(ms_access_token="enc", ms_refresh_token="enc", ms_account_email="ops@a.test")
        s = client.get("/api/settings/integrations", headers=_token()).json()
        assert s["outlook"] == {"connected": True, "connected_at": None, "account": "ops@a.test"}
        assert "enc" not in str(s)
        assert client.delete("/auth/outlook/disconnect", headers=_token(role="manager")).status_code == 403
        assert client.delete("/auth/outlook/disconnect", headers=_token()).status_code == 200
        assert adb.tables["companies"][0]["ms_access_token"] is None
        assert client.get("/api/settings/integrations", headers=_token()).json()["outlook"]["connected"] is False

    @pytest.mark.asyncio
    async def test_expired_token_is_refreshed_and_the_new_pair_is_saved(self):
        from app.connectors.outlook import OutlookConnector
        saved = {}
        calls = []

        def microsoft(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path, request.headers.get("authorization")))
            if request.url.host == "login.microsoftonline.com":
                assert b"refresh_token=RT-old" in request.content
                return httpx.Response(200, json={"access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 3600})
            if request.headers.get("authorization") != "Bearer AT-new":
                return httpx.Response(401, json={"error": "expired"})
            return httpx.Response(200, json={"value": [{
                "id": "m1", "conversationId": "c1", "subject": "PO 123", "isRead": False,
                "bodyPreview": "Please confirm", "receivedDateTime": "2026-11-10T10:00:00Z",
                "from": {"emailAddress": {"name": "Vera Vendor", "address": "vera@vendor.test"}}}]})

        conn = OutlookConnector("common", "cid", "cs", "AT-old", "RT-old",
                                on_token_refresh=lambda a, r, e: saved.update(access=a, refresh=r, expires=e))
        with _fake_microsoft(microsoft):
            emails = await conn.get_emails(query="is:unread")
        assert saved["access"] == "AT-new" and saved["refresh"] == "RT-new" and saved["expires"]
        assert emails == [{
            "id": "m1", "thread_id": "c1", "from": "Vera Vendor <vera@vendor.test>", "from_email": "vera@vendor.test",
            "subject": "PO 123", "snippet": "Please confirm", "date": "2026-11-10T10:00:00Z", "labels": ["UNREAD"]}]
        # first try with the old token, refresh, retry with the new one
        assert [c[2] for c in calls if c[1].endswith("/messages")] == ["Bearer AT-old", "Bearer AT-new"]

    @pytest.mark.asyncio
    async def test_revoked_connection_says_reconnect_instead_of_failing_silently(self):
        from app.connectors.outlook import OutlookAuthError, OutlookConnector

        def microsoft(request):
            if request.url.host == "login.microsoftonline.com":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(401)

        conn = OutlookConnector("common", "cid", "cs", "AT", "RT")
        with _fake_microsoft(microsoft), pytest.raises(OutlookAuthError):
            await conn.send_email_reply("a@b.test", "s", "b")

    @pytest.mark.asyncio
    async def test_reply_is_sent_in_the_original_conversation(self):
        import json
        from app.connectors.outlook import OutlookConnector
        seen = []

        def microsoft(request):
            seen.append((request.url.path, json.loads(request.content)))
            return httpx.Response(202)

        conn = OutlookConnector("common", "cid", "cs", "AT", "RT")
        with _fake_microsoft(microsoft):
            out = await conn.send_email_reply("vera@vendor.test", "Re: PO 123", "Confirmed.", reply_to_id="m1")
            new = await conn.send_email_reply("vera@vendor.test", "Hello", "Hi.")
        assert out == {"status": "sent", "mode": "reply"} and new["mode"] == "new"
        assert seen[0][0] == "/v1.0/me/messages/m1/reply" and seen[0][1]["comment"] == "Confirmed."
        assert seen[1][0] == "/v1.0/me/sendMail"
        assert seen[1][1]["message"]["toRecipients"][0]["emailAddress"]["address"] == "vera@vendor.test"

    def test_adapter_builds_outlook_from_decrypted_tokens_and_saves_rotations(self):
        from app.connectors.unified_adapter import UnifiedDataAdapter
        from app.utils.encryption import decrypt
        db = FakeSupabase(companies=[{"id": COMPANY_A}])
        adapter = UnifiedDataAdapter({"id": COMPANY_A, "ms_access_token": "AT", "ms_refresh_token": "RT",
                                      "ms_client_id": "cid", "ms_client_secret": "cs"})
        adapter._db = db
        assert adapter._outlook is not None
        adapter._persist_ms_tokens("AT2", "RT2", "2026-11-11T00:00:00+00:00")
        row = db.rows("companies")[0]
        assert decrypt(row["ms_refresh_token"], "test-secret-key-32-chars-long!!x") == "RT2"
        # no tokens → no Outlook connector
        assert UnifiedDataAdapter({"id": COMPANY_A})._outlook is None


# ─────────────────────────────────────────────────────────────────────────────
# Review follow-ups on the pull request.
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyUsesTheRealOrderValue:
    """A PO's value for policy checks comes from its lines, not a summary field."""
    DENY_OVER_10K = {"id": "11111111-2222-3333-4444-555555555555", "company_id": COMPANY_A, "is_active": True,
                     "priority": 1, "action_class": "commit", "effect": "deny", "min_amount": 10000,
                     "rule_name": "no_orders_over_10k"}

    def _po(self, **content):
        return _draft(action_type="purchase_order", content={"vendor_id": "V1", **content})

    def test_amount_is_computed_from_lines_when_no_total_is_given(self):
        from decimal import Decimal
        from app.api.actions import _content_amount
        content = {"vendor_id": "V1", "line_items": [{"item_id": "i", "quantity": 2000, "unit_cost": "50"}]}
        assert _content_amount(content, "purchase_order") == Decimal("100000")

    def test_understated_total_does_not_win(self):
        from decimal import Decimal
        from app.api.actions import _content_amount
        content = {"vendor_id": "V1", "total": 500, "line_items": [{"item_id": "i", "quantity": 2000, "unit_cost": 50}]}
        assert _content_amount(content, "purchase_order") == Decimal("100000")

    def test_large_order_without_a_total_is_blocked_by_the_amount_rule(self, client, fdb):
        fdb.tables["policy_rules"].append(self.DENY_OVER_10K)
        fdb.tables["drafts"].append(self._po(line_items=[{"item_id": "i", "quantity": 2000, "unit_cost": 50}]))
        assert _approve(client).status_code == 403
        fdb.adapter.create_purchase_order.assert_not_called()

    def test_large_order_with_a_small_stated_total_is_still_blocked(self, client, fdb):
        fdb.tables["policy_rules"].append(self.DENY_OVER_10K)
        fdb.tables["drafts"].append(self._po(total=99, line_items=[{"item_id": "i", "quantity": 2000, "unit_cost": 50}]))
        assert _approve(client).status_code == 403
        fdb.adapter.create_purchase_order.assert_not_called()

    def test_small_order_passes_the_same_rule(self, client, fdb):
        fdb.tables["policy_rules"].append(self.DENY_OVER_10K)
        fdb.tables["drafts"].append(self._po(line_items=[{"item_id": "i", "quantity": 10, "unit_cost": 50}]))
        assert _approve(client).json()["execution_status"] == "executed"

    def test_line_without_a_cost_is_never_created_automatically(self, client, fdb):
        """No cost → the order's value is unknown → a person enters it."""
        fdb.tables["drafts"].append(self._po(line_items=[{"item_id": "i", "quantity": 2000}]))
        assert _approve(client).json()["execution_status"] == "manual_required"
        fdb.adapter.create_purchase_order.assert_not_called()

    def test_edited_lines_are_the_ones_checked(self, client, fdb):
        fdb.tables["policy_rules"].append(self.DENY_OVER_10K)
        fdb.tables["drafts"].append(self._po(line_items=[{"item_id": "i", "quantity": 1, "unit_cost": 1}]))
        edited = {"vendor_id": "V1", "line_items": [{"item_id": "i", "quantity": 5000, "unit_cost": 50}]}
        assert _approve(client, body={"edited_content": edited}).status_code == 403
        fdb.adapter.create_purchase_order.assert_not_called()


class TestUncertainOutcomeIsNotRetried:
    """If we cannot tell whether it happened, it must not go back in the queue."""

    def _status_error(self, code):
        req = httpx.Request("POST", "https://api.example.test/x")
        return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))

    @pytest.mark.parametrize("exc", [
        httpx.ReadTimeout("no answer after sending"),
        httpx.RemoteProtocolError("connection dropped"),
        RuntimeError("something unexpected"),
    ])
    def test_po_with_unknown_outcome_is_closed_not_requeued(self, client, fdb, exc):
        fdb.tables["drafts"].append(_draft(action_type="purchase_order", content={
            "vendor_id": "V1", "line_items": [{"item_id": "i", "quantity": 1, "unit_cost": 5}]}))
        fdb.adapter.create_purchase_order = AsyncMock(side_effect=exc)
        r = _approve(client)
        assert r.json()["execution_status"] == "unknown"
        assert "before creating it again" in r.json()["execution_detail"]
        d = fdb.rows("drafts")[0]
        assert d["status"] == "approved" and d["execution_status"] == "unknown"
        # a second approval cannot create a second order
        assert _approve(client).json().get("already_processed") is True
        assert fdb.adapter.create_purchase_order.await_count == 1
        assert [e["event_type"] for e in fdb.events()] == ["draft_approved", "draft_execution_unknown"]

    def test_5xx_is_unknown(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.adapter.send_email_reply = AsyncMock(side_effect=self._status_error(502))
        r = _approve(client)
        assert r.json()["execution_status"] == "unknown" and "Sent folder" in r.json()["execution_detail"]
        assert fdb.rows("drafts")[0]["status"] == "approved"

    @pytest.mark.parametrize("make", [
        lambda self: self._status_error(400),
        lambda self: self._status_error(403),
        lambda self: httpx.ConnectError("could not connect"),
        lambda self: ValueError("No email connector configured"),
    ])
    def test_clear_rejections_are_retryable(self, client, fdb, make):
        fdb.tables["drafts"].append(_draft())
        fdb.adapter.send_email_reply = AsyncMock(side_effect=make(self))
        r = _approve(client)
        assert r.json()["execution_status"] == "failed"
        assert fdb.rows("drafts")[0]["status"] == "pending"

    def test_internal_error_text_is_not_shown_to_the_user(self, client, fdb):
        fdb.tables["drafts"].append(_draft())
        fdb.adapter.send_email_reply = AsyncMock(side_effect=RuntimeError("secret-internal-detail at 10.0.0.5"))
        assert "secret-internal-detail" not in _approve(client).text


class TestNoSessionSurvivesAnAccountChange:
    """A sign-in that is mid-flight while access is reduced gets no usable token."""

    def test_login_racing_with_deactivation_is_refused(self, client, adb):
        """The cut-off lands after the login read the (still active) row."""
        import app.api.auth as auth_mod
        from app.auth.jwt import revoke_user_sessions
        real = auth_mod._refuse_if_account_changed

        def account_changes_mid_login(user_id, read_at):
            revoke_user_sessions(user_id)       # what the owner's change does
            return real(user_id, read_at)

        with patch.object(auth_mod, "_refuse_if_account_changed", side_effect=account_changes_mid_login):
            r = _login(client, "staff@a.test", "Staff-pass1")
        assert r.status_code == 401 and "access_token" not in r.text

    def test_refresh_racing_with_demotion_is_refused(self, client, adb):
        import app.api.auth as auth_mod
        from app.auth.jwt import revoke_user_sessions
        tok = _login(client, "staff@a.test", "Staff-pass1").json()["access_token"]
        real = auth_mod._refuse_if_account_changed

        def account_changes_mid_refresh(user_id, read_at):
            revoke_user_sessions(user_id)
            return real(user_id, read_at)

        with patch.object(auth_mod, "_refuse_if_account_changed", side_effect=account_changes_mid_refresh):
            r = client.post("/auth/refresh", json={"access_token": tok})
        assert r.status_code == 401

    def test_token_minted_before_the_second_cutoff_is_dead(self, client, adb):
        """The other interleaving: token issued during the update, before cut-off two."""
        import time
        from app.auth.jwt import create_access_token
        owner = _login(client, "owner@a.test", "Owner-pass1").json()["access_token"]
        import app.api.users as users_mod
        real_revoke = users_mod.revoke_user_sessions
        calls = []
        stale = {}

        def revoke_and_slip_a_token_in(user_id):
            real_revoke(user_id)
            calls.append(user_id)
            if len(calls) == 1:   # between cut-off one and the database update
                time.sleep(0.003)
                stale["token"] = create_access_token({"sub": STAFF_ID, "company_id": COMPANY_A, "role": "manager"})
                time.sleep(0.003)

        with patch.object(users_mod, "revoke_user_sessions", side_effect=revoke_and_slip_a_token_in):
            assert client.patch(f"/api/users/{STAFF_ID}", json={"role": "viewer"}, headers=_bearer(owner)).status_code == 200
        assert len(calls) == 2  # before AND after the update
        assert client.get("/auth/me", headers=_bearer(stale["token"])).status_code == 401

    def test_ordinary_login_after_an_old_change_still_works(self, client, adb):
        import time
        from app.auth.jwt import revoke_user_sessions
        revoke_user_sessions(STAFF_ID)
        time.sleep(0.01)
        assert _login(client, "staff@a.test", "Staff-pass1").status_code == 200

    def test_no_token_if_the_store_is_down_at_issue_time(self, client, adb):
        import app.auth.jwt as jwt_mod
        broken = MagicMock()
        broken.get.side_effect = ConnectionError("redis down")
        jwt_mod._redis_client = broken
        r = _login(client, "staff@a.test", "Staff-pass1")
        assert r.status_code == 503 and "access_token" not in r.text


class TestInputSafetyHelpers:
    @pytest.mark.parametrize("good", ["a@b.co", "first.last+tag@sub.example.com"])
    def test_valid_emails(self, good):
        from app.utils.safe import is_valid_email
        assert is_valid_email(good)

    @pytest.mark.parametrize("bad", ["", "a", "a@b", "@b.co", "a@.co", "a@b.", "a b@c.co", "a@b@c.co", "a@b.co\n", None, 5])
    def test_invalid_emails(self, bad):
        from app.utils.safe import is_valid_email
        assert not is_valid_email(bad)

    def test_crafted_long_input_is_rejected_quickly(self):
        """The old regex took quadratic time on input like this."""
        import time
        from app.utils.safe import is_valid_email
        evil = "!@" + "!." * 200_000
        start = time.perf_counter()
        assert not is_valid_email(evil)
        assert time.perf_counter() - start < 0.05

    def test_log_values_cannot_forge_new_lines(self):
        from app.utils.safe import log_safe
        assert "\n" not in log_safe("x\nERROR forged entry\r\nmore") and "\r" not in log_safe("a\rb")

    def test_oauth_error_is_mapped_to_a_fixed_code(self, client, adb):
        r = client.get("/auth/outlook/callback?error=access_denied", follow_redirects=False)
        assert r.headers["location"].endswith("outlook=error&reason=access_denied")
        r = client.get("/auth/qbo/callback?error=https://evil.example/%0d%0aSet-Cookie:x", follow_redirects=False)
        assert r.headers["location"].endswith("qbo=error&reason=error")
        assert "evil" not in r.headers["location"]


# ─────────────────────────────────────────────────────────────────────────────
# Sales reps see only their own accounts.
# ─────────────────────────────────────────────────────────────────────────────

REP_ID = "30000000-0000-0000-0000-000000000003"
REP2_ID = "30000000-0000-0000-0000-000000000004"


@pytest.fixture
def accdb(mock_adapter):
    db = FakeSupabase(
        users=[
            {"id": OWNER_ID, "company_id": COMPANY_A, "email": "o@a.test", "name": "Olive", "role": "owner", "is_active": True},
            {"id": REP_ID, "company_id": COMPANY_A, "email": "r@a.test", "name": "Rita Rep", "role": "sales_rep", "is_active": True},
            {"id": REP2_ID, "company_id": COMPANY_A, "email": "r2@a.test", "name": "Ravi Rep", "role": "sales_rep", "is_active": True},
            {"id": OTHER_ID, "company_id": COMPANY_B, "email": "o@b.test", "name": "Bea", "role": "sales_rep", "is_active": True},
        ],
        account_assignments=[{"company_id": COMPANY_A, "qb_customer_id": "QB001", "user_id": REP_ID}],
        audit_events=[],
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_adapter] = lambda: mock_adapter
    yield db
    app.dependency_overrides.clear()


def _ids(resp):
    return sorted(a["id"] for a in resp.json()["accounts"])


class TestSalesRepsSeeOnlyTheirAccounts:
    def test_owner_and_back_office_see_all(self, client, accdb):
        assert _ids(client.get("/api/accounts/", headers=_token(role="owner", sub=OWNER_ID))) == ["QB001", "QB002", "QB003"]
        assert len(_ids(client.get("/api/accounts/", headers=_token(role="back_office", sub=OWNER_ID)))) == 3

    def test_rep_sees_only_assigned_accounts(self, client, accdb):
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP_ID))) == ["QB001"]

    def test_rep_with_no_accounts_sees_none(self, client, accdb):
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP2_ID))) == []

    def test_rep_cannot_open_someone_elses_account(self, client, accdb):
        hdr = _token(role="sales_rep", sub=REP_ID)
        assert client.get("/api/accounts/QB001", headers=hdr).status_code == 200
        assert client.get("/api/accounts/QB002", headers=hdr).status_code == 404

    def test_if_assignments_cannot_be_read_a_rep_sees_nothing(self, client, accdb):
        accdb.fail_tables.add("account_assignments")
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP_ID))) == []
        assert client.get("/api/accounts/QB001", headers=_token(role="sales_rep", sub=REP_ID)).status_code == 404

    def test_another_companys_assignment_does_not_count(self, client, accdb):
        accdb.tables["account_assignments"].append({"company_id": COMPANY_B, "qb_customer_id": "QB002", "user_id": REP_ID})
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP_ID))) == ["QB001"]

    def test_manager_assigns_and_the_rep_then_sees_it(self, client, accdb):
        mgr = _token(role="manager", sub=OWNER_ID)
        r = client.put("/api/accounts/QB002/rep", json={"user_id": REP2_ID}, headers=mgr)
        assert r.status_code == 200 and r.json()["assigned_rep"] == "Ravi Rep"
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP2_ID))) == ["QB002"]
        listed = {a["id"]: a for a in client.get("/api/accounts/", headers=mgr).json()["accounts"]}
        assert listed["QB002"]["assigned_rep"] == "Ravi Rep" and listed["QB003"]["assigned_rep"] is None
        # reassigning moves it (one rep per account)
        client.put("/api/accounts/QB002/rep", json={"user_id": REP_ID}, headers=mgr)
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP2_ID))) == []
        # clearing
        assert client.put("/api/accounts/QB002/rep", json={"user_id": None}, headers=mgr).status_code == 200
        assert _ids(client.get("/api/accounts/", headers=_token(role="sales_rep", sub=REP_ID))) == ["QB001"]
        assert [e["event_type"] for e in accdb.events()] == ["account_assigned", "account_assigned", "account_unassigned"]

    @pytest.mark.parametrize("role", ["sales_rep", "back_office", "viewer"])
    def test_only_owner_or_manager_can_assign(self, client, accdb, role):
        assert client.put("/api/accounts/QB002/rep", json={"user_id": REP_ID}, headers=_token(role=role, sub=REP_ID)).status_code == 403
        assert client.get("/api/accounts/reps", headers=_token(role=role, sub=REP_ID)).status_code == 403

    def test_cannot_assign_to_another_companys_user_or_unknown_account(self, client, accdb):
        mgr = _token(role="manager", sub=OWNER_ID)
        assert client.put("/api/accounts/QB002/rep", json={"user_id": OTHER_ID}, headers=mgr).status_code == 404
        assert client.put("/api/accounts/NOPE/rep", json={"user_id": REP_ID}, headers=mgr).status_code == 404
        assert len(accdb.rows("account_assignments")) == 1

    def test_reps_list_is_this_company_only(self, client, accdb):
        reps = client.get("/api/accounts/reps", headers=_token(role="owner", sub=OWNER_ID)).json()["reps"]
        assert {r["name"] for r in reps} == {"Olive", "Rita Rep", "Ravi Rep"}
        assert reps[0]["role"] == "sales_rep"

    @pytest.mark.asyncio
    async def test_chat_only_loads_the_reps_own_customers(self, mock_adapter):
        from app.api.chat import _fetch_data_for_intent
        mine = await _fetch_data_for_intent("account_status", "how are my accounts?", mock_adapter,
                                            role="sales_rep", allowed_customers={"QB001"})
        assert [a["name"] for a in mine["accounts"]] == ["Acme Corp"]
        everyone = await _fetch_data_for_intent("account_status", "how are my accounts?", mock_adapter,
                                                role="owner", allowed_customers=None)
        assert len(everyone["accounts"]) == 3

    @pytest.mark.asyncio
    async def test_chat_cannot_be_asked_about_another_reps_account_by_name(self, mock_adapter):
        from app.api.chat import _fetch_data_for_intent
        data = await _fetch_data_for_intent("account_status", "what is the balance of Global Imports LLC?", mock_adapter,
                                            role="sales_rep", allowed_customers={"QB001"})
        assert "account" not in data and "Global Imports" not in str(data)


# ─────────────────────────────────────────────────────────────────────────────
# Purchase-order drafts carry a real vendor and lines.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def podb():
    from app.connectors.base import InventoryItem, Vendor
    from decimal import Decimal
    db = FakeSupabase(drafts=[], audit_events=[], policy_rules=[], company_features=[])
    adapter = MagicMock()
    adapter.get_inventory_merged = AsyncMock(return_value=[
        {"qb_id": "ITEM1", "product_name": "Cola 12pk", "total_qty": 20, "weekly_sell_rate": 10.0, "purchase_cost": 5.0},
        {"qb_id": "ITEM2", "product_name": "No-cost item", "total_qty": 0, "weekly_sell_rate": 0, "purchase_cost": 0},
    ])
    adapter.get_inventory_qb = AsyncMock(return_value=[
        InventoryItem(id="ITEM1", qb_id="ITEM1", name="Cola 12pk", sku=None, quantity_on_hand=Decimal(20),
                      unit_price=Decimal(9), purchase_cost=Decimal(5), reorder_point=None, preferred_vendor_id="V2"),
    ])
    adapter.get_vendors = AsyncMock(return_value=[
        Vendor(id="V1", qb_id="V1", name="Zed Supply"), Vendor(id="V2", qb_id="V2", name="Acme Bottling")])
    adapter.create_purchase_order = AsyncMock(return_value=MagicMock(qb_id="PO-9"))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_adapter] = lambda: adapter
    db.adapter = adapter
    yield db
    app.dependency_overrides.clear()


class TestPurchaseOrderDrafts:
    def test_options_offer_vendors_cost_and_a_suggested_quantity(self, client, podb):
        r = client.get("/api/actions/po-options?item_id=ITEM1", headers=_token(sub=USER_ID)).json()
        assert r["item"] == {"id": "ITEM1", "name": "Cola 12pk", "unit_cost": 5.0, "on_hand": 20.0,
                             "weekly_sell_rate": 10.0, "suggested_qty": 60}   # 8 weeks × 10 − 20 on hand
        assert [v["name"] for v in r["vendors"]] == ["Acme Bottling", "Zed Supply"]
        assert r["preferred_vendor_id"] == "V2"

    def test_draft_with_vendor_then_approval_creates_the_order_in_quickbooks(self, client, podb):
        r = client.post("/api/actions/draft-po", json={"item_id": "ITEM1", "vendor_id": "V2", "qty": 40},
                        headers=_token(sub=USER_ID))
        assert r.status_code == 200 and r.json()["ready_for_quickbooks"] is True
        d = podb.rows("drafts")[0]
        assert d["content"]["vendor_name"] == "Acme Bottling" and d["content"]["total"] == 200.0
        assert d["content"]["line_items"] == [{"item_id": "ITEM1", "product_name": "Cola 12pk",
                                               "description": "Cola 12pk", "quantity": 40, "unit_cost": 5.0}]
        out = _approve(client, draft_id=d["id"]).json()
        assert out["execution_status"] == "executed" and "PO-9" in out["execution_detail"]
        kw = podb.adapter.create_purchase_order.await_args.kwargs
        assert kw["vendor_id"] == "V2" and kw["line_items"][0]["quantity"] == "40" and kw["line_items"][0]["unit_cost"] == "5.0"

    def test_the_cost_comes_from_quickbooks_unless_the_user_overrides_it(self, client, podb):
        client.post("/api/actions/draft-po", json={"item_id": "ITEM1", "vendor_id": "V1", "qty": 10, "unit_cost": 4.5},
                    headers=_token(sub=USER_ID))
        assert podb.rows("drafts")[0]["content"]["total"] == 45.0

    def test_unknown_vendor_is_refused(self, client, podb):
        r = client.post("/api/actions/draft-po", json={"item_id": "ITEM1", "vendor_id": "V999", "qty": 1}, headers=_token(sub=USER_ID))
        assert r.status_code == 422 and podb.rows("drafts") == []

    def test_without_a_vendor_the_draft_says_it_needs_manual_entry(self, client, podb):
        r = client.post("/api/actions/draft-po", json={"item_id": "ITEM1"}, headers=_token(sub=USER_ID))
        assert r.json()["ready_for_quickbooks"] is False and "by hand" in r.json()["message"]
        d = podb.rows("drafts")[0]
        assert "line_items" not in d["content"] and d["content"]["qty"] == 60
        assert _approve(client, draft_id=d["id"]).json()["execution_status"] == "manual_required"
        podb.adapter.create_purchase_order.assert_not_called()

    def test_item_with_no_cost_on_file_is_never_sent_at_zero(self, client, podb):
        client.post("/api/actions/draft-po", json={"item_id": "ITEM2", "vendor_id": "V1", "qty": 5}, headers=_token(sub=USER_ID))
        c = podb.rows("drafts")[0]["content"]
        assert "line_items" not in c and "no unit cost" in c["incomplete_reason"]

    def test_spending_rule_applies_to_the_drafted_total(self, client, podb):
        podb.tables["policy_rules"].append({"id": "11111111-2222-3333-4444-555555555555", "company_id": COMPANY_A,
                                            "is_active": True, "priority": 1, "action_class": "commit",
                                            "effect": "deny", "min_amount": 1000, "rule_name": "cap"})
        client.post("/api/actions/draft-po", json={"item_id": "ITEM1", "vendor_id": "V2", "qty": 500}, headers=_token(sub=USER_ID))
        assert _approve(client, draft_id=podb.rows("drafts")[0]["id"]).status_code == 403
        podb.adapter.create_purchase_order.assert_not_called()
