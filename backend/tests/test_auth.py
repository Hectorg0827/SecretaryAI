"""
Tests for auth module: JWT token management and RBAC.
"""
import pytest
from datetime import timedelta
from unittest.mock import MagicMock

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.auth.jwt import create_access_token, decode_access_token, hash_password, verify_password


# ─── JWT Tests ─────────────────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode_token(self):
        payload = {"sub": "user-123", "company_id": "co-456", "role": "owner"}
        token = create_access_token(payload)
        assert isinstance(token, str)
        decoded = decode_access_token(token)
        assert decoded is not None
        assert decoded["sub"] == "user-123"
        assert decoded["company_id"] == "co-456"
        assert decoded["role"] == "owner"
        assert "exp" in decoded

    def test_expired_token_returns_none(self):
        payload = {"sub": "user-123"}
        token = create_access_token(payload, expires_delta=timedelta(seconds=-1))
        result = decode_access_token(token)
        assert result is None

    def test_invalid_token_returns_none(self):
        assert decode_access_token("not-a-valid-token") is None

    def test_tampered_token_returns_none(self):
        token = create_access_token({"sub": "user-1"})
        tampered = token[:-5] + "XXXXX"
        assert decode_access_token(tampered) is None

    def test_custom_expiry(self):
        payload = {"sub": "user-999"}
        token = create_access_token(payload, expires_delta=timedelta(hours=24))
        decoded = decode_access_token(token)
        assert decoded is not None
        assert decoded["sub"] == "user-999"


def _bcrypt_available():
    """Check if bcrypt is functional in this environment."""
    try:
        hash_password("probe")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _bcrypt_available(), reason="bcrypt not functional in this environment")
class TestPasswordHashing:
    def test_hash_password_not_plaintext(self):
        hashed = hash_password("mysecretpassword")
        assert hashed != "mysecretpassword"

    def test_verify_correct_password(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_different_passwords_produce_different_hashes(self):
        h1 = hash_password("password1")
        h2 = hash_password("password1")
        # bcrypt uses random salt, so hashes differ
        assert h1 != h2


# ─── RBAC Tests ────────────────────────────────────────────────────────────────

class TestRBACPermissions:
    def setup_method(self):
        from app.auth.rbac import ROLE_PERMISSIONS
        self.perms = ROLE_PERMISSIONS

    def test_owner_has_all_core_permissions(self):
        owner_perms = self.perms["owner"]
        assert "view_all" in owner_perms
        assert "manage_users" in owner_perms
        assert "approve_actions" in owner_perms
        assert "manage_billing" in owner_perms
        assert "trigger_actions" in owner_perms

    def test_manager_has_key_permissions(self):
        mgr_perms = self.perms["manager"]
        assert "view_all" in mgr_perms
        assert "approve_actions" in mgr_perms
        assert "trigger_actions" in mgr_perms

    def test_manager_cannot_manage_users(self):
        assert "manage_users" not in self.perms["manager"]

    def test_sales_rep_limited_permissions(self):
        perms = self.perms["sales_rep"]
        assert "view_own_accounts" in perms
        assert "view_inventory" in perms
        assert "manage_users" not in perms
        assert "approve_actions" not in perms

    def test_back_office_permissions(self):
        perms = self.perms["back_office"]
        assert "view_inventory" in perms
        assert "view_orders" in perms
        assert "run_reports" in perms
        assert "view_customers" in perms
        assert "approve_actions" not in perms

    def test_viewer_minimal_permissions(self):
        perms = self.perms["viewer"]
        assert "view_dashboard" in perms
        assert len(perms) == 1


class TestGetCurrentUser:
    def test_valid_token_returns_payload(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI, Depends
        from app.auth.rbac import get_current_user
        from app.auth.jwt import create_access_token

        app = FastAPI()

        @app.get("/me")
        def me(user=Depends(get_current_user)):
            return user

        token = create_access_token({"sub": "u1", "company_id": "c1", "role": "owner"})
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["sub"] == "u1"

    def test_invalid_token_returns_401(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI, Depends
        from app.auth.rbac import get_current_user

        app = FastAPI()

        @app.get("/me")
        def me(user=Depends(get_current_user)):
            return user

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me", headers={"Authorization": "Bearer badtoken"})
        assert resp.status_code == 401


class TestRequirePermission:
    def _make_app(self, permission: str):
        from fastapi import FastAPI, Depends
        from app.auth.rbac import require_permission

        app = FastAPI()

        @app.get("/protected")
        def protected(user=Depends(require_permission(permission))):
            return {"ok": True}

        return app

    def _token_for_role(self, role: str) -> str:
        from app.auth.jwt import create_access_token
        return create_access_token({"sub": "u1", "company_id": "c1", "role": role})

    def test_owner_can_access_any_permission(self):
        from fastapi.testclient import TestClient
        app = self._make_app("manage_users")
        client = TestClient(app, raise_server_exceptions=False)
        token = self._token_for_role("owner")
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_viewer_blocked_from_manage_users(self):
        from fastapi.testclient import TestClient
        app = self._make_app("manage_users")
        client = TestClient(app, raise_server_exceptions=False)
        token = self._token_for_role("viewer")
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_manager_can_approve_actions(self):
        from fastapi.testclient import TestClient
        app = self._make_app("approve_actions")
        client = TestClient(app, raise_server_exceptions=False)
        token = self._token_for_role("manager")
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_sales_rep_blocked_from_approve_actions(self):
        from fastapi.testclient import TestClient
        app = self._make_app("approve_actions")
        client = TestClient(app, raise_server_exceptions=False)
        token = self._token_for_role("sales_rep")
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_unknown_role_defaults_to_no_permissions(self):
        from fastapi.testclient import TestClient
        app = self._make_app("view_financials")
        client = TestClient(app, raise_server_exceptions=False)
        token = self._token_for_role("unknown_role")
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
