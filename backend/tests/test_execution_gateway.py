"""V12 transport tests are synthetic: no database credentials or network."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.execution_gateway import (
    ExecutionDatabaseUnavailable,
    PostgresDurableGateway,
)


def connection(role="secretary_executor", result=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=[
        role,
        json.dumps(result or {"execution_id": "execution-1"}),
    ])
    conn.close = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_enqueues_through_private_function_only():
    conn = connection(result={"execution_id": "exec-7", "state": "queued"})
    with patch("app.workflows.execution_gateway.asyncpg.connect",
               new_callable=AsyncMock, return_value=conn) as connect:
        gw = PostgresDurableGateway("postgresql://test")
        result = await gw.enqueue("tenant-1", "draft-1", "idem-1")
    assert result == "exec-7"
    sql = conn.fetchval.await_args_list[1].args[0]
    assert "secretary_internal.enqueue_agent_execution" in sql
    connect.assert_awaited_once()
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejects_broad_database_role_before_rpc():
    conn = connection(role="postgres")
    with patch("app.workflows.execution_gateway.asyncpg.connect",
               new_callable=AsyncMock, return_value=conn):
        gw = PostgresDurableGateway("postgresql://test")
        with pytest.raises(ExecutionDatabaseUnavailable):
            await gw.enqueue("tenant-1", "draft-1", "idem-1")
    assert conn.fetchval.await_count == 1
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_claim_returns_no_token():
    conn = connection(result={"claimed": False})
    with patch("app.workflows.execution_gateway.asyncpg.connect",
               new_callable=AsyncMock, return_value=conn):
        gw = PostgresDurableGateway("postgresql://test")
        assert await gw.claim("tenant-1", "execution-1", "worker-1") is None


@pytest.mark.asyncio
async def test_malformed_claim_fails_closed():
    conn = connection(result={"claimed": True})
    with patch("app.workflows.execution_gateway.asyncpg.connect",
               new_callable=AsyncMock, return_value=conn):
        gw = PostgresDurableGateway("postgresql://test")
        with pytest.raises(ExecutionDatabaseUnavailable):
            await gw.claim("tenant-1", "execution-1", "worker-1")


@pytest.mark.asyncio
async def test_connection_errors_hide_credentials():
    with patch("app.workflows.execution_gateway.asyncpg.connect",
               new_callable=AsyncMock,
               side_effect=OSError("postgresql://secret-password@private")):
        gw = PostgresDurableGateway("postgresql://secret-password@private")
        with pytest.raises(ExecutionDatabaseUnavailable) as exc:
            await gw.enqueue("tenant", "draft", "key")
    assert "secret-password" not in str(exc.value)


def test_missing_dedicated_connection_fails_closed(monkeypatch):
    monkeypatch.delenv("SECRETARYAI_EXECUTION_DATABASE_URL", raising=False)
    with pytest.raises(ExecutionDatabaseUnavailable):
        PostgresDurableGateway.from_environment()


@pytest.mark.asyncio
async def test_coordinator_uses_gateway_enqueue_claim_and_finish():
    from app.workflows.approved_execution import run_approved_external_action

    enqueue_conn = connection(result={"execution_id": "exec-7", "state": "queued"})
    claim_conn = connection(result={
        "claimed": True, "execution_id": "exec-7", "claim_token": "token-7",
        "attempt": 1,
    })
    finish_conn = connection(result={"finalized": True, "state": "completed"})
    effect = AsyncMock()

    with patch("app.workflows.execution_gateway.asyncpg.connect",
               new_callable=AsyncMock,
               side_effect=[enqueue_conn, claim_conn, finish_conn]):
        receipt = await run_approved_external_action(
            gateway=PostgresDurableGateway("postgresql://test"),
            company_id="tenant-1", draft_id="draft-1",
            idempotency_key="business-key", worker_id="worker-1",
            external_action=effect,
        )
    assert receipt.state == "completed"
    effect.assert_awaited_once()
    assert "secretary_internal.claim_agent_execution" in (
        claim_conn.fetchval.await_args_list[1].args[0]
    )
    assert "secretary_internal.finish_agent_execution" in (
        finish_conn.fetchval.await_args_list[1].args[0]
    )
