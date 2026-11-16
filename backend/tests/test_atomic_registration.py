"""Owner-registration atomicity and no-cascade regressions (provider free).

These tests never contact a real Supabase or production database.
PostgreSQL integration uses a separate disposable CI fixture script.
"""
import os
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.auth.registration import create_company_owner_transactionally
from app.domain.policy import _default_rules


COMPANY = UUID("11111111-1111-4111-8111-111111111121")
OWNER = UUID("22222222-2222-4222-8222-222222222221")


class FakeTransaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.transactions += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.rollbacks += bool(exc_type)
        self.db.commits += not bool(exc_type)
        return False


class FakeConnection:
    def __init__(self, fail_at=""):
        self.fail_at = fail_at
        self.events = []
        self.transactions = 0
        self.rollbacks = 0
        self.commits = 0
        self.closed = False

    def transaction(self):
        return FakeTransaction(self)

    async def fetchval(self, sql, *args):
        self.events.append((sql, args))
        if "INSERT INTO public.companies" in sql:
            return COMPANY
        if "INSERT INTO public.users" in sql:
            if self.fail_at == "owner":
                raise RuntimeError("fixture failed owner insert")
            return OWNER
        raise AssertionError("Unexpected query")

    async def execute(self, sql, *args):
        self.events.append((sql, args))
        if self.fail_at == "features" and "company_features" in sql:
            raise RuntimeError("fixture failed feature seed")
        if self.fail_at == "rule" and "policy_rules" in sql:
            raise RuntimeError("fixture failed policy seed")
        return "INSERT 0 1"

    async def close(self):
        self.closed = True


def run_registration():
    return {
        "database_url": "postgresql://ci:ci@localhost/fixture",
        "company_name": "CI tenant",
        "email": "test@example.invalid",
        "hashed_password": "synthetic-ci-hash",
        "full_name": "CI owner",
        "policy_rules_factory": _default_rules,
    }


@pytest.mark.asyncio
async def test_atomic_registration_commits_all_safety_records_and_owner():
    db = FakeConnection()
    with patch("asyncpg.connect", new_callable=AsyncMock, return_value=db) as connect:
        result = await create_company_owner_transactionally(**run_registration())
    assert result == {"id": str(OWNER), "company_id": str(COMPANY)}
    assert (db.transactions, db.commits, db.rollbacks, db.closed) == (1, 1, 0, True)
    assert connect.await_args.kwargs["statement_cache_size"] == 0
    sql = "\n".join(q for q, _ in db.events)
    assert len(db.events) == 8  # company, features, 5 rules, owner
    assert sql.count("INSERT INTO public.policy_rules") == len(_default_rules(str(COMPANY))) == 5
    assert "DELETE " not in sql.upper()
    owner_args = db.events[-1][1]
    assert owner_args[:3] == (COMPANY, "test@example.invalid", "synthetic-ci-hash")
    assert "VALUES ($1, $2, $3, $4, 'owner', TRUE)" in db.events[-1][0]


@pytest.mark.parametrize("failed_step", ["features", "rule", "owner"])
@pytest.mark.asyncio
async def test_failure_never_attempts_company_cleanup_delete(failed_step):
    db = FakeConnection(fail_at=failed_step)
    with patch("asyncpg.connect", new_callable=AsyncMock, return_value=db):
        with pytest.raises(RuntimeError, match="fixture failed"):
            await create_company_owner_transactionally(**run_registration())
    assert (db.transactions, db.commits, db.rollbacks, db.closed) == (1, 0, 1, True)
    assert all("DELETE " not in sql.upper() for sql, _ in db.events)


@pytest.mark.asyncio
async def test_empty_or_untrusted_policy_template_rolls_back():
    for factory in (lambda _: [], lambda x: [{"company_id": x, "rule_name": "read",
                         "action_class": "read", "effect": "allow", "injected_sql": "oops"}]):
        db = FakeConnection()
        kwargs = run_registration()
        kwargs["policy_rules_factory"] = factory
        with patch("asyncpg.connect", new_callable=AsyncMock, return_value=db):
            with pytest.raises(RuntimeError, match="policy"):
                await create_company_owner_transactionally(**kwargs)
        assert (db.commits, db.rollbacks, db.closed) == (0, 1, True)


@pytest.mark.asyncio
async def test_register_endpoint_uses_only_atomic_helper_and_keeps_response_contract():
    from app.api import auth

    body = auth.RegisterRequest(company_name="CI tenant", email="TEST@EXAMPLE.INVALID",
                                password="Somepass123", full_name="CI owner")
    row = {"id": str(OWNER), "company_id": str(COMPANY)}
    with (patch("app.auth.registration.create_company_owner_transactionally",
                new_callable=AsyncMock, return_value=row) as create,
          patch.object(auth, "hash_password", return_value="fake-hash"),
          patch.object(auth, "password_session_version", return_value="pwd-session"),
          patch.object(auth, "create_access_token", return_value="signed-ci-token"),
          patch("supabase.create_client", return_value=MagicMock()),
          patch("app.utils.audit.write_audit_event")):
        result = await auth.register(body)
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["email"] == "test@example.invalid"
    assert kwargs["policy_rules_factory"] is _default_rules
    assert result == {"access_token": "signed-ci-token", "token_type": "bearer",
                      "company_id": str(COMPANY), "role": "owner"}


@pytest.mark.asyncio
async def test_duplicate_email_is_409_and_other_constraints_fail_closed():
    from app.api import auth

    class UniqueViolation(Exception):
        def __init__(self, constraint_name):
            self.constraint_name = constraint_name

    body = auth.RegisterRequest(company_name="CI tenant", email="test@example.invalid",
                                password="Somepass123")
    for constraint, expected_status in (("users_email_key", 409),
                                        ("policy_rules_other_key", 503)):
        with (patch("asyncpg.UniqueViolationError", UniqueViolation),
              patch("app.auth.registration.create_company_owner_transactionally",
                    new_callable=AsyncMock, side_effect=UniqueViolation(constraint)),
              patch.object(auth, "hash_password", return_value="fake-hash")):
            with pytest.raises(auth.HTTPException) as exc:
                await auth.register(body)
        assert exc.value.status_code == expected_status
