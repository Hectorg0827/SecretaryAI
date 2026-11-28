"""Gate A regressions for the staging auth schema and safe operational logging.

No live Supabase, Redis, email, or financial connectors are used by these tests.
"""
import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import auth, dashboard
from app.utils.log_safety import configure_safe_transport_logging
from app.scheduler.dashboard_snapshot import (
    SnapshotPersistenceUnavailable,
    SnapshotSourceUnavailable,
    store_snapshot,
)


def test_http_transport_success_logging_does_not_record_oauth_token_urls(caplog):
    """HTTPX request URLs may contain token-bearing PostgREST filters."""
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    original_levels = (httpx_logger.level, httpcore_logger.level)
    try:
        httpx_logger.setLevel(logging.INFO)
        httpcore_logger.setLevel(logging.INFO)
        configure_safe_transport_logging()
        with caplog.at_level(logging.INFO):
            httpx_logger.info("PATCH /companies?qbo_refresh_token=synthetic-secret")
            httpcore_logger.info("PATCH /companies?qbo_refresh_token=synthetic-secret")
        assert httpx_logger.getEffectiveLevel() >= logging.WARNING
        assert httpcore_logger.getEffectiveLevel() >= logging.WARNING
        assert not any("synthetic-secret" in record.getMessage() for record in caplog.records)
    finally:
        httpx_logger.setLevel(original_levels[0])
        httpcore_logger.setLevel(original_levels[1])


@pytest.mark.asyncio
async def test_login_reads_staging_password_column():
    db = MagicMock()
    users_table = db.table.return_value
    users_table.select.return_value.eq.return_value.execute.return_value.data = [{
        "id": "synthetic-owner",
        "company_id": "synthetic-tenant",
        "role": "owner",
        "hashed_password": "synthetic-staging-hash",
        "is_active": True,
        "totp_enabled": False,
        "totp_secret": None,
    }]
    with patch("supabase.create_client", return_value=db), \
         patch.object(auth.login_email_limiter, "is_allowed", return_value=True), \
         patch.object(auth, "verify_password", return_value=True) as verify, \
         patch.object(auth, "create_access_token", return_value="synthetic-jwt"), \
         patch("app.utils.audit.write_audit_event"):
        response = await auth.login(
            auth.LoginRequest(email="owner@example.test", password="SyntheticPass123"),
            _=None,
        )

    users_table.select.assert_called_once_with(
        "id, company_id, role, hashed_password, is_active, totp_enabled, totp_secret"
    )
    verify.assert_called_once_with("SyntheticPass123", "synthetic-staging-hash")
    assert response["access_token"] == "synthetic-jwt"
    assert response["company_id"] == "synthetic-tenant"


@pytest.mark.asyncio
async def test_profile_reads_staging_full_name_column():
    db = MagicMock()
    users_table = db.table.return_value
    expected = {
        "id": "synthetic-owner", "full_name": "Sample Owner",
        "email": "owner@example.test", "role": "owner", "totp_enabled": False,
    }
    users_table.select.return_value.eq.return_value.execute.return_value.data = [expected]
    with patch("supabase.create_client", return_value=db):
        profile = await auth.get_me(user={"sub": "synthetic-owner"})

    users_table.select.assert_called_once_with(
        "id, full_name, email, role, totp_enabled"
    )
    assert profile == expected


@pytest.mark.asyncio
async def test_registration_writes_staging_user_columns():
    """The endpoint passes the supported user columns to one atomic DB writer."""
    row = {"id": "synthetic-owner", "company_id": "synthetic-tenant"}
    with (patch("app.auth.registration.create_company_owner_transactionally",
                new_callable=AsyncMock, return_value=row) as create,
          patch.object(auth, "hash_password", return_value="synthetic-hash"),
          patch.object(auth, "create_access_token", return_value="synthetic-jwt"),
          patch.object(auth, "password_session_version", return_value="synthetic-pwd-session"),
          patch("supabase.create_client", return_value=MagicMock()),
          patch("app.utils.audit.write_audit_event"),
          patch.object(auth, "_seed_company_defaults", new_callable=AsyncMock) as legacy_seed):
        response = await auth.register(
            auth.RegisterRequest(
                company_name="Synthetic Company", email="owner@example.test",
                password="SyntheticPass123", full_name="Sample Owner",
            ), _=None,
        )

    create.assert_awaited_once()
    args = create.await_args.kwargs
    assert args["hashed_password"] == "synthetic-hash"
    assert args["full_name"] == "Sample Owner"
    assert args["email"] == "owner@example.test"
    assert args["company_name"] == "Synthetic Company"
    assert args["policy_rules_factory"].__name__ == "_default_rules"
    assert "password_hash" not in args and "name" not in args
    legacy_seed.assert_not_awaited()
    assert response["company_id"] == "synthetic-tenant"
    assert response["access_token"] == "synthetic-jwt"


@pytest.mark.asyncio
async def test_registration_rejects_partial_safety_setup_before_creating_owner():
    """Rollback happens in the atomic writer; no service-role company DELETE."""
    db = MagicMock()
    with (patch("app.auth.registration.create_company_owner_transactionally",
                new_callable=AsyncMock,
                side_effect=RuntimeError("policy database error")) as create,
          patch.object(auth, "hash_password", return_value="synthetic-hash"),
          patch.object(auth, "create_access_token") as mint_token,
          patch("supabase.create_client", return_value=db) as client):
        with pytest.raises(HTTPException) as caught:
            await auth.register(
                auth.RegisterRequest(
                    company_name="Synthetic Company", email="owner@example.test",
                    password="aaaaaaaa1",
                ), _=None,
            )

    create.assert_awaited_once()
    assert caught.value.status_code == 503
    assert "policy database error" not in caught.value.detail
    mint_token.assert_not_called()
    client.assert_not_called()
    db.table.return_value.delete.assert_not_called()


@pytest.mark.parametrize("module_path", [
    "app/api/dashboard.py",
    "app/domain/policy.py",
    "app/scheduler/dashboard_snapshot.py",
])
def test_operational_logs_have_no_untrusted_interpolated_fields(module_path):
    """Guard against reintroducing CR/LF-capable tenant/source IDs in log lines.

    A constant log event keeps useful failure classification without writing
    potentially malicious tenant IDs, email IDs, report types or exception
    strings. Structured correlation must be supplied by trusted telemetry.
    """
    source = (Path(__file__).resolve().parents[1] / module_path).read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "log"
            and func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
        ):
            continue
        assert len(call.args) == 1 and not call.keywords, (
            f"{module_path}:{call.lineno} interpolates an untrusted log field"
        )
        assert isinstance(call.args[0], ast.Constant) and isinstance(
            call.args[0].value, str
        ), f"{module_path}:{call.lineno} must use a literal log event"


@pytest.mark.asyncio
async def test_dashboard_source_failure_cannot_forge_log_entries(caplog):
    malicious = "synthetic-tenant\nFORGED_LOG_EVENT"
    adapter = AsyncMock()
    with patch.object(dashboard, "read_snapshot", return_value=None), \
         patch.object(
             dashboard, "compute_dashboard_payload",
             new_callable=AsyncMock,
             side_effect=SnapshotSourceUnavailable("source\r\nFORGED_LOG_EVENT"),
         ):
        with caplog.at_level(logging.INFO):
            with pytest.raises(HTTPException) as error:
                await dashboard.get_dashboard_summary(
                    user={"sub": "owner-a", "company_id": malicious, "role": "owner"},
                    adapter=adapter,
                    db=MagicMock(),
                )

    assert error.value.status_code == 503
    assert not any("FORGED_LOG_EVENT" in record.getMessage()
                   for record in caplog.records)


def test_snapshot_failure_cannot_forge_log_entries(caplog):
    db = MagicMock()
    db.table.return_value.upsert.return_value.execute.side_effect = RuntimeError(
        "backend\r\nFORGED_LOG_EVENT"
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SnapshotPersistenceUnavailable):
            store_snapshot(
                db, "synthetic-tenant\nFORGED_LOG_EVENT",
                "dashboard_summary\r\nFORGED_LOG_EVENT", {},
            )
    assert not any("FORGED_LOG_EVENT" in record.getMessage()
                   for record in caplog.records)
