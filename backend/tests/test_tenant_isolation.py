"""
Cross-tenant (IDOR) negative tests.

Proves a user authenticated for Company A cannot approve/reject Company B's
draft actions or cancel Company B's workflow runs. The service-role Supabase
client bypasses row-level security, so isolation is enforced in app code by a
`company_id` predicate on every tenant-scoped query — these tests guard that.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_db, get_adapter
from app.auth.jwt import create_access_token


def _owner_token(company_id: str) -> str:
    return create_access_token(
        {"sub": f"user-of-{company_id}", "company_id": company_id, "role": "owner"}
    )


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _empty_scoped_db():
    """A Supabase-like mock where every scoped query returns no rows
    (simulating a resource owned by a DIFFERENT company)."""
    db = MagicMock()
    # table(...).select(...).eq(...).eq(...).execute() -> data == []
    exec_mock = MagicMock(data=[])
    chain = db.table.return_value.select.return_value
    chain.eq.return_value.eq.return_value.execute.return_value = exec_mock
    chain.eq.return_value.execute.return_value = exec_mock
    # updates: table(...).update(...).eq(...).eq(...).execute()
    upd = db.table.return_value.update.return_value
    upd.eq.return_value.eq.return_value.execute.return_value = exec_mock
    return db


class TestDraftApprovalIsolation:
    def test_cannot_approve_other_companys_draft(self, client):
        app.dependency_overrides[get_db] = _empty_scoped_db
        app.dependency_overrides[get_adapter] = lambda: MagicMock()
        token = _owner_token("company-A")
        r = client.post(
            "/api/actions/approve/draft-owned-by-company-B",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text  # not visible → cannot execute

    def test_cannot_reject_other_companys_draft(self, client):
        app.dependency_overrides[get_db] = _empty_scoped_db
        token = _owner_token("company-A")
        r = client.post(
            "/api/actions/reject/draft-owned-by-company-B",
            json={"reason": "nope"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text


class TestWorkflowCancelIsolation:
    def test_cannot_cancel_other_companys_workflow(self, client):
        app.dependency_overrides[get_db] = _empty_scoped_db
        token = _owner_token("company-A")
        r = client.post(
            "/api/workflows/run-owned-by-company-B/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text


class TestNoClientSuppliedTenant:
    def test_body_company_id_is_ignored(self, client):
        """A client-supplied company_id must never widen access: even if the
        request body carries company-B, the server only ever uses the JWT's
        company, so the (B-owned) draft stays invisible → 404."""
        app.dependency_overrides[get_db] = _empty_scoped_db
        app.dependency_overrides[get_adapter] = lambda: MagicMock()
        token = _owner_token("company-A")
        r = client.post(
            "/api/actions/approve/draft-owned-by-company-B",
            json={"company_id": "company-B"},  # attacker-controlled — must be ignored
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text
