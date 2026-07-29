"""
Tests for RBAC + action-safety hardening:
- #29 role gate on single-account read.
- Idempotent draft approval/rejection (double-approve must not double-execute).
"""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_db, get_adapter
from app.auth.jwt import create_access_token


def _token(role: str, company_id: str = "co-1") -> str:
    return create_access_token({"sub": f"u-{role}", "company_id": company_id, "role": role})


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


class TestAccountRoleGate:
    def test_viewer_cannot_read_account_detail(self, client):
        # get_adapter is resolved as a dependency; stub it so the role check runs.
        app.dependency_overrides[get_adapter] = lambda: MagicMock()
        r = client.get(
            "/api/accounts/some-id",
            headers={"Authorization": f"Bearer {_token('viewer')}"},
        )
        assert r.status_code == 403, r.text


def _draft_db(status: str):
    db = MagicMock()
    exec_mock = MagicMock(data=[{"id": "d1", "company_id": "co-1", "status": status,
                                 "action_type": "email"}])
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = exec_mock
    db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    return db


class TestApprovalIdempotency:
    def test_approving_already_approved_draft_does_not_execute(self, client):
        app.dependency_overrides[get_db] = lambda: _draft_db("approved")
        app.dependency_overrides[get_adapter] = lambda: MagicMock()
        with patch("app.api.actions._execute_approved_draft", new=AsyncMock()) as exec_spy:
            r = client.post(
                "/api/actions/approve/d1",
                json={},
                headers={"Authorization": f"Bearer {_token('owner')}"},
            )
        assert r.status_code == 200, r.text
        assert r.json().get("already_processed") is True
        exec_spy.assert_not_called()  # no second send/dispatch

    def test_rejecting_already_resolved_draft_is_noop(self, client):
        app.dependency_overrides[get_db] = lambda: _draft_db("rejected")
        r = client.post(
            "/api/actions/reject/d1",
            json={"reason": "x"},
            headers={"Authorization": f"Bearer {_token('owner')}"},
        )
        assert r.status_code == 200, r.text
        assert r.json().get("already_processed") is True


# ── #31 PolicyEngine on the approval path ─────────────────────────────────────
class TestPolicyGate:
    def test_policy_deny_blocks_approval(self, client):
        app.dependency_overrides[get_db] = lambda: _draft_db("pending")
        app.dependency_overrides[get_adapter] = lambda: MagicMock()

        class _Deny:
            async def evaluate(self, proposal):
                return MagicMock(effect="deny")

        with patch("app.api.actions.PolicyEngine", return_value=_Deny()), \
             patch("app.api.actions._execute_approved_draft", new=AsyncMock()) as exec_spy:
            r = client.post(
                "/api/actions/approve/d1",
                json={},
                headers={"Authorization": f"Bearer {_token('owner')}"},
            )
        assert r.status_code == 403, r.text
        exec_spy.assert_not_called()  # denied → never executes


# ── #32 alert-email recipient allow-list ──────────────────────────────────────
class TestAlertRecipientAllowlist:
    def test_invalid_recipient_rejected(self):
        from app.actions.email_actions import _recipient_allowed
        ok, _ = _recipient_allowed("not-an-email")
        assert ok is False

    def test_valid_recipient_when_no_allowlist(self):
        from app.actions import email_actions
        email_actions.settings.alert_email_allowlist = ""
        ok, _ = email_actions._recipient_allowed("owner@acme.com")
        assert ok is True

    def test_offdomain_recipient_rejected_when_allowlist_set(self):
        from app.actions import email_actions
        email_actions.settings.alert_email_allowlist = "acme.com"
        try:
            ok_in, _ = email_actions._recipient_allowed("owner@acme.com")
            ok_out, _ = email_actions._recipient_allowed("attacker@evil.com")
            assert ok_in is True
            assert ok_out is False
        finally:
            email_actions.settings.alert_email_allowlist = ""


# ── #32 typed action-payload validation ───────────────────────────────────────
class TestActionPayloadSchemas:
    def test_rejects_negative_quantity(self):
        from app.actions.schemas import validate_action_payload, ActionValidationError
        with pytest.raises(ActionValidationError):
            validate_action_payload("update_inventory_count", {"item_id": "SKU1", "quantity": -3})

    def test_rejects_nonnumeric_quantity(self):
        from app.actions.schemas import validate_action_payload, ActionValidationError
        with pytest.raises(ActionValidationError):
            validate_action_payload("update_inventory_count", {"item_id": "SKU1", "quantity": "lots"})

    def test_rejects_missing_item_id(self):
        from app.actions.schemas import validate_action_payload, ActionValidationError
        with pytest.raises(ActionValidationError):
            validate_action_payload("update_inventory_count", {"quantity": 5})

    def test_coerces_and_preserves_extras(self):
        from app.actions.schemas import validate_action_payload
        out = validate_action_payload(
            "update_inventory_count", {"item_id": "SKU1", "quantity": "5", "note": "keep"}
        )
        assert out["quantity"] == 5          # coerced str → int
        assert out["note"] == "keep"          # extra field preserved

    def test_unmapped_action_passes_through(self):
        from app.actions.schemas import validate_action_payload
        p = {"anything": 1}
        assert validate_action_payload("run_sync_check", p) is p


# ── Autonomy enforcement (core action-safety control) ─────────────────────────
class TestAutonomyEnforcement:
    def _engine(self):
        from app.actions.engine import ActionEngine
        return ActionEngine(MagicMock())

    async def test_prohibited_action_is_blocked(self):
        from app.actions.engine import ActionProhibitedError
        eng = self._engine()
        with patch("app.actions.engine.log_action", new=AsyncMock()):
            with pytest.raises(ActionProhibitedError):
                await eng.process("delete_qb_data", {}, "co-1", "user-1")

    async def test_unknown_action_defaults_to_prohibited(self):
        from app.actions.engine import ActionProhibitedError
        eng = self._engine()
        with patch("app.actions.engine.log_action", new=AsyncMock()):
            with pytest.raises(ActionProhibitedError):
                await eng.process("totally_unknown_action", {}, "co-1", "user-1")

    async def test_autonomous_action_with_invalid_payload_rejected(self):
        # update_inventory_count is autonomous (no human approval) — an invalid
        # payload must be rejected before the DB write (#32 wired into process).
        from app.actions.schemas import ActionValidationError
        eng = self._engine()
        with patch("app.actions.engine.log_action", new=AsyncMock()):
            with pytest.raises(ActionValidationError):
                await eng.process(
                    "update_inventory_count", {"item_id": "SKU1", "quantity": -5},
                    "co-1", "user-1",
                )
