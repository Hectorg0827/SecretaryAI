"""Email worker certification uses only simulated callback delivery."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows.approved_email_worker import (
    EmailWorkerDisabled, simulate_approved_email,
)
from app.workflows.approved_execution import ExecutionReconciliationRequired


def gateway():
    g = MagicMock()
    g.claim_email = AsyncMock(return_value={
        "claimed": True, "claim_token": "claim-a",
        "recipient": "synthetic@example.test", "subject": "Approved",
        "body": "Frozen body", "payload_sha256": "a" * 64,
    })
    g.finish = AsyncMock(return_value=True)
    return g


def enable_simulation(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_EMAIL_WORKER_MODE", "simulate")
    monkeypatch.setenv("SECRETARYAI_TEST_DATABASE_ONLY", "1")


async def work(g, send):
    return await simulate_approved_email(
        gateway=g, company_id="tenant-a", execution_id="exec-a",
        worker_id="worker-a", synthetic_delivery=send,
    )


@pytest.mark.asyncio
async def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SECRETARYAI_EMAIL_WORKER_MODE", raising=False)
    g = gateway()
    send = AsyncMock()
    with pytest.raises(EmailWorkerDisabled):
        await work(g, send)
    g.claim_email.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_claimed_immutable_body_is_used(monkeypatch):
    enable_simulation(monkeypatch)
    g = gateway()
    send = AsyncMock()
    result = await work(g, send)
    assert result.state == "completed"
    send.assert_awaited_once_with(
        "synthetic@example.test", "Approved", "Frozen body"
    )
    g.finish.assert_awaited_once_with(
        "tenant-a", "exec-a", "claim-a", "completed"
    )


@pytest.mark.asyncio
async def test_loser_never_receives_or_sends(monkeypatch):
    enable_simulation(monkeypatch)
    g = gateway()
    g.claim_email.return_value = {"claimed": False}
    send = AsyncMock()
    result = await work(g, send)
    assert result.state == "not_claimed"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_timeout_is_uncertain(monkeypatch):
    enable_simulation(monkeypatch)
    g = gateway()
    send = AsyncMock(side_effect=TimeoutError("ack lost"))
    with pytest.raises(ExecutionReconciliationRequired):
        await work(g, send)
    g.finish.assert_awaited_once_with(
        "tenant-a", "exec-a", "claim-a", "needs_reconciliation"
    )


@pytest.mark.asyncio
async def test_failed_snapshot_claim_does_not_send(monkeypatch):
    enable_simulation(monkeypatch)
    g = gateway()
    g.claim_email.side_effect = RuntimeError("database unavailable")
    send = AsyncMock()
    with pytest.raises(RuntimeError):
        await work(g, send)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_delivery_write_failure_never_retries(monkeypatch):
    enable_simulation(monkeypatch)
    g = gateway()
    g.finish.side_effect = ConnectionError("ledger unavailable")
    send = AsyncMock()
    with pytest.raises(ExecutionReconciliationRequired):
        await work(g, send)
    send.assert_awaited_once()
