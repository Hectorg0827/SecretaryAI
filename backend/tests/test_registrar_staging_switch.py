"""Staging-only registrar switch: zero fallback to privileged registration.

Synthetic mocks and sample DSNs only. Never provisions credentials or touches
a live Supabase/Railway database.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import auth
from app.config import Settings

PROJECT = "xerktvdgjvjozvckjyfp"
REGISTRAR_DSN = (
    f"postgresql://secretary_registrar:synthetic-only"
    f"@db.{PROJECT}.supabase.co/postgres"
)


def valid_settings(**updates):
    data = dict(
        secret_key="ci-secret-" * 5,
        supabase_url=f"https://{PROJECT}.supabase.co",
        supabase_anon_key="synthetic-anon",
        supabase_service_role_key="synthetic-service-role",
        database_url=f"postgresql://postgres:synthetic@db.{PROJECT}.supabase.co/postgres",
        secretaryai_deployment_env="staging",
        secretaryai_expected_supabase_project_ref=PROJECT,
        intuit_environment="sandbox",
        computer_use_enabled=False,
        billing_enabled=False,
        secretaryai_restricted_registrar_enabled=True,
        secretaryai_registrar_database_url=REGISTRAR_DSN,
    )
    data.update(updates)
    return Settings(**data)


def test_restricted_registrar_settings_accept_only_expected_staging_identity():
    s = valid_settings()
    assert s.secretaryai_restricted_registrar_enabled
    assert s.secretaryai_registrar_database_url == REGISTRAR_DSN


@pytest.mark.parametrize("overrides", [
    {"secretaryai_deployment_env": "production"},
    {"secretaryai_registrar_database_url": ""},
    {"secretaryai_registrar_database_url":
        f"postgresql://postgres:synthetic@db.{PROJECT}.supabase.co/postgres"},
    {"secretaryai_registrar_database_url":
        "postgresql://secretary_registrar:synthetic@db.otherprojectref.supabase.co/postgres"},
])
def test_invalid_staging_pilot_credentials_refused_without_echoing_secret(overrides):
    with pytest.raises(ValueError) as err:
        valid_settings(**overrides)
    msg = str(err.value)
    assert "synthetic-only" not in msg
    assert "Restricted registrar" in msg


@pytest.mark.asyncio
async def test_enabled_registration_uses_restricted_registrar_without_legacy_path():
    body = auth.RegisterRequest(
        company_name="CI co", email="TEST@EXAMPLE.INVALID",
        password="Valid-Passphrase123!", full_name="CI owner",
    )
    result_user = {"id": "owner-ci", "company_id": "tenant-ci"}
    with (
        patch.object(auth.settings, "secretaryai_restricted_registrar_enabled", True),
        patch.object(auth.settings, "secretaryai_registrar_database_url", REGISTRAR_DSN),
        patch.object(auth.settings, "secretaryai_expected_supabase_project_ref", PROJECT),
        patch("app.auth.registrar_rpc.create_owner_using_registrar",
              new_callable=AsyncMock, return_value=result_user) as restricted,
        patch("app.auth.registration.create_company_owner_transactionally",
              new_callable=AsyncMock) as privileged,
        patch.object(auth, "hash_password", return_value="ci-hash"),
        patch.object(auth, "password_session_version", return_value="ci-session"),
        patch.object(auth, "create_access_token", return_value="ci-token"),
        patch("supabase.create_client", return_value=MagicMock()),
        patch("app.utils.audit.write_audit_event"),
    ):
        result = await auth.register(body, _=None)
    assert result["access_token"] == "ci-token"
    assert result["company_id"] == "tenant-ci"
    restricted.assert_awaited_once()
    assert restricted.await_args.kwargs["database_url"] == REGISTRAR_DSN
    assert restricted.await_args.kwargs["expected_project_ref"] == PROJECT
    assert restricted.await_args.kwargs["email"] == "test@example.invalid"
    privileged.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_registrar_fails_closed_no_general_db_or_token():
    body = auth.RegisterRequest(
        company_name="CI co", email="owner@example.invalid",
        password="Valid-Passphrase123!", full_name="CI owner",
    )
    with (
        patch.object(auth.settings, "secretaryai_restricted_registrar_enabled", True),
        patch.object(auth.settings, "secretaryai_registrar_database_url", ""),
        patch.object(auth.settings, "secretaryai_expected_supabase_project_ref", PROJECT),
        patch("app.auth.registration.create_company_owner_transactionally",
              new_callable=AsyncMock) as privileged,
        patch("asyncpg.connect", new_callable=AsyncMock) as database,
        patch.object(auth, "hash_password", return_value="ci-hash"),
        patch.object(auth, "create_access_token") as mint,
        patch("supabase.create_client") as client,
    ):
        with pytest.raises(auth.HTTPException) as exc:
            await auth.register(body, _=None)
    assert exc.value.status_code == 503
    assert "registar" not in exc.value.detail.lower()
    privileged.assert_not_awaited()
    database.assert_not_awaited()
    client.assert_not_called()
    mint.assert_not_called()


@pytest.mark.asyncio
async def test_restricted_registrar_error_does_not_retry_privileged_writer():
    body = auth.RegisterRequest(
        company_name="CI co", email="owner@example.invalid",
        password="Valid-Passphrase123!", full_name="CI owner",
    )
    with (
        patch.object(auth.settings, "secretaryai_restricted_registrar_enabled", True),
        patch.object(auth.settings, "secretaryai_registrar_database_url", REGISTRAR_DSN),
        patch.object(auth.settings, "secretaryai_expected_supabase_project_ref", PROJECT),
        patch("app.auth.registrar_rpc.create_owner_using_registrar",
              new_callable=AsyncMock, side_effect=RuntimeError("synthetic unavailable"))
            as restricted,
        patch("app.auth.registration.create_company_owner_transactionally",
              new_callable=AsyncMock) as privileged,
        patch.object(auth, "hash_password", return_value="ci-hash"),
        patch.object(auth, "create_access_token") as mint,
        patch("supabase.create_client") as client,
    ):
        with pytest.raises(auth.HTTPException) as exc:
            await auth.register(body, _=None)
    assert exc.value.status_code == 503
    assert "synthetic unavailable" not in exc.value.detail
    restricted.assert_awaited_once()
    privileged.assert_not_awaited()
    mint.assert_not_called()
    client.assert_not_called()
