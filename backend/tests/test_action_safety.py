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
