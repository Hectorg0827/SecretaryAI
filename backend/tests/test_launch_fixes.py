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
