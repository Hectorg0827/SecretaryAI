"""Provider-free tests for the unconnected, least-privilege registrar transport."""
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.auth.registrar_rpc import (
    validate_registrar_dsn,
    create_owner_using_registrar,
)

CI_URL = "postgresql://secretary_registrar:ci-local@localhost:5432/sai"
PROJECT = "xerktvdgjvjozvckjyfp"


@pytest.mark.parametrize("dsn", [
    "",
    "postgresql://postgres:admin@localhost:5432/sai",
    "postgresql://service_role:admin@localhost:5432/sai",
    "postgresql://secretary_registrar@localhost:5432/sai",
    "postgresql://secretary_registrar:wrong@staging.example/sai",
    "https://secretary_registrar:pw@localhost/db",
    f"postgresql://secretary_registrar:pw@db.anotherprojectref.supabase.co/postgres",
])
def test_privileged_or_unscoped_dsn_rejected(dsn):
    with pytest.raises(RuntimeError):
        validate_registrar_dsn(dsn)


def test_staging_direct_and_pooler_role_are_scoped_to_expected_project():
    validate_registrar_dsn(
        f"postgresql://secretary_registrar:synthetic@db.{PROJECT}.supabase.co/postgres",
        expected_project_ref=PROJECT,
    )
    validate_registrar_dsn(
        f"postgresql://secretary_registrar.{PROJECT}:synthetic@aws-0-us-east-1.pooler.supabase.com/postgres",
        expected_project_ref=PROJECT,
    )


@pytest.mark.asyncio
async def test_private_registrar_rpc_calls_only_one_fixed_statement():
    connection = AsyncMock()
    connection.fetchrow.return_value = {
        "company_id": UUID("11111111-1111-4111-8111-111111111121"),
        "user_id": UUID("22222222-2222-4222-8222-222222222221"),
    }
    with patch("asyncpg.connect", new_callable=AsyncMock, return_value=connection) as connector:
        result = await create_owner_using_registrar(
            database_url=CI_URL, expected_project_ref="",
            company_name="CI", email="ci@example.invalid",
            hashed_password="fake-hash", full_name="CI Owner",
        )
    assert result == {
        "company_id": "11111111-1111-4111-8111-111111111121",
        "id": "22222222-2222-4222-8222-222222222221",
    }
    assert connector.await_args.kwargs["statement_cache_size"] == 0
    stmt = connection.fetchrow.await_args.args[0]
    assert stmt.startswith("SELECT company_id, user_id FROM secretary_internal.register_owner_atomic(")
    assert "DELETE " not in stmt.upper()
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_registrar_dsn_validation_precedes_network_and_never_falls_back():
    with patch("asyncpg.connect", new_callable=AsyncMock) as connector:
        with pytest.raises(RuntimeError, match="role mismatch"):
            await create_owner_using_registrar(
                database_url=f"postgresql://postgres:admin@db.{PROJECT}.supabase.co/postgres",
                expected_project_ref=PROJECT,
                company_name="CI", email="ci@example.invalid",
                hashed_password="fake-hash", full_name="CI",
            )
    connector.assert_not_awaited()
