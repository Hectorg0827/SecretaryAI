"""Startup gate tests for the opt-in least-privilege runtime DB role audit.

All tests use synthetic DSNs and mocks; no live Railway or Supabase database
is contacted.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.runtime_role_audit import RuntimeRoleEvidence
from app.config import Settings

PROJECT = "xerktvdgjvjozvckjyfp"
OTHER_PROJECT = "aaaaaaaaaaaaaaaaaaaa"
SECRET_CANARY = "synthetic-runtime-secret-do-not-leak"


def _staging_settings(**overrides) -> Settings:
    data = dict(
        secret_key="ci-secret-" * 5,
        supabase_url=f"https://{PROJECT}.supabase.co",
        supabase_anon_key="synthetic-anon",
        supabase_service_role_key="synthetic-service-role",
        database_url=(
            f"postgresql://postgres.{PROJECT}:{SECRET_CANARY}"
            "@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
        ),
        anthropic_api_key="sk-ant-synthetic",
        secretaryai_deployment_env="staging",
        secretaryai_expected_supabase_project_ref=PROJECT,
        intuit_environment="sandbox",
        computer_use_enabled=False,
        billing_enabled=False,
    )
    data.update(overrides)
    return Settings(**data)


def test_default_audit_flag_is_false_and_staging_accepts_runtime_pooler_dsn():
    legacy = _staging_settings()
    assert legacy.secretaryai_enforce_runtime_db_role_audit is False

    runtime_pooler = _staging_settings(
        database_url=(
            f"postgresql://secretary_runtime.{PROJECT}:{SECRET_CANARY}"
            "@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
        )
    )
    assert runtime_pooler.secretaryai_enforce_runtime_db_role_audit is False
    assert f"secretary_runtime.{PROJECT}" in runtime_pooler.database_url


@pytest.mark.parametrize("bad_dsn", [
    f"postgresql://service_role.{PROJECT}:{SECRET_CANARY}@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    f"postgresql://secretary_runtime.{OTHER_PROJECT}:{SECRET_CANARY}@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    f"postgresql://secretary_runtime:{SECRET_CANARY}@db.{OTHER_PROJECT}.supabase.co:5432/postgres",
])
def test_staging_rejects_foreign_or_unauthorized_pooler_identities(bad_dsn):
    with pytest.raises(ValueError) as err:
        _staging_settings(database_url=bad_dsn)
    assert SECRET_CANARY not in str(err.value)


@pytest.mark.parametrize("valid_dsn", [
    f"postgresql://secretary_runtime:{SECRET_CANARY}@db.{PROJECT}.supabase.co:5432/postgres",
    f"postgresql://secretary_runtime.{PROJECT}:{SECRET_CANARY}@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
])
def test_enforced_runtime_db_audit_accepts_scoped_runtime_credentials(valid_dsn):
    configured = _staging_settings(
        database_url=valid_dsn,
        secretaryai_enforce_runtime_db_role_audit=True,
    )
    assert configured.secretaryai_enforce_runtime_db_role_audit is True


@pytest.mark.parametrize("bad_dsn", [
    f"postgresql://postgres:{SECRET_CANARY}@db.{PROJECT}.supabase.co:5432/postgres",
    f"postgresql://postgres.{PROJECT}:{SECRET_CANARY}@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    f"postgresql://service_role:{SECRET_CANARY}@db.{PROJECT}.supabase.co:5432/postgres",
    f"postgresql://secretary_runtime:{SECRET_CANARY}@db.{OTHER_PROJECT}.supabase.co:5432/postgres",
    f"postgresql://secretary_runtime:{SECRET_CANARY}@localhost:5432/postgres",
])
def test_enforced_runtime_db_audit_refuses_elevated_or_unscoped_dsns_without_leaking(bad_dsn):
    with pytest.raises(ValueError) as err:
        _staging_settings(
            database_url=bad_dsn,
            secretaryai_enforce_runtime_db_role_audit=True,
        )
    msg = str(err.value)
    assert SECRET_CANARY not in msg
    assert "secretary_runtime" in msg


@pytest.mark.asyncio
async def test_lifespan_skips_runtime_db_audit_when_disabled():
    from app.main import app, lifespan

    cfg = _staging_settings(secretaryai_enforce_runtime_db_role_audit=False)
    with (
        patch("app.main.get_settings", return_value=cfg),
        patch(
            "app.auth.runtime_role_audit.require_safe_runtime_database_role",
            new_callable=AsyncMock,
        ) as audit,
    ):
        async with lifespan(app):
            pass
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifespan_passes_when_enforced_and_runtime_role_verified():
    from app.main import app, lifespan

    runtime_dsn = (
        f"postgresql://secretary_runtime.{PROJECT}:{SECRET_CANARY}"
        "@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    )
    cfg = _staging_settings(
        database_url=runtime_dsn,
        secretaryai_enforce_runtime_db_role_audit=True,
    )
    evidence = RuntimeRoleEvidence("secretary_runtime", "secretary_runtime", ())
    with (
        patch("app.main.get_settings", return_value=cfg),
        patch(
            "app.auth.runtime_role_audit.require_safe_runtime_database_role",
            new_callable=AsyncMock,
            return_value=evidence,
        ) as audit,
    ):
        async with lifespan(app):
            pass
    audit.assert_awaited_once_with(
        database_url=runtime_dsn,
        expected_project_ref=PROJECT,
    )


@pytest.mark.asyncio
async def test_lifespan_fails_closed_when_runtime_role_has_elevated_grants():
    from app.main import app, lifespan

    runtime_dsn = f"postgresql://secretary_runtime:{SECRET_CANARY}@db.{PROJECT}.supabase.co:5432/postgres"
    cfg = _staging_settings(
        database_url=runtime_dsn,
        secretaryai_enforce_runtime_db_role_audit=True,
    )
    with (
        patch("app.main.get_settings", return_value=cfg),
        patch(
            "app.auth.runtime_role_audit.require_safe_runtime_database_role",
            new_callable=AsyncMock,
            side_effect=RuntimeError(
                "Routine database identity has elevated or missing grants: company_delete,rolbypassrls"
            ),
        ) as audit,
    ):
        with pytest.raises(RuntimeError, match="company_delete,rolbypassrls"):
            async with lifespan(app):
                pass
    audit.assert_awaited_once_with(
        database_url=runtime_dsn,
        expected_project_ref=PROJECT,
    )
