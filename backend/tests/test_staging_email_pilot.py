"""No real mail is sent in pilot tests."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows.approved_execution import ExecutionReconciliationRequired
from app.workflows.staging_email_pilot import (
    StagingPilotDisabled,
    run_staging_email_pilot,
)


def enable(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_DEPLOYMENT_ENV", "staging")
    monkeypatch.setenv("SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED", "1")
    monkeypatch.setenv("SECRETARYAI_STAGING_EMAIL_RECIPIENT", "pilot@example.test")
    monkeypatch.setenv("SECRETARYAI_EMAIL_DELIVERY_MODE", "live")
    monkeypatch.setenv("SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED", "1")


def gateway():
    g = MagicMock()
    g.claim_staging_email = AsyncMock(return_value={
        "claimed": True, "claim_token": "claim-1",
        "recipient": "pilot@example.test",
        "subject": "Synthetic", "body": "Approved snapshot",
    })
    g.finish = AsyncMock(return_value=True)
    return g


async def pilot(g, adapter):
    return await run_staging_email_pilot(
        gateway=g, adapter=adapter, company_id="tenant-1",
        execution_id="exec-1", worker_id="worker-1",
    )


@pytest.mark.asyncio
async def test_pilot_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED", raising=False)
    g = gateway()
    adapter = MagicMock(send_email_reply=AsyncMock())
    with pytest.raises(StagingPilotDisabled):
        await pilot(g, adapter)
    g.claim_staging_email.assert_not_awaited()
    adapter.send_email_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_allowlisted_recipient_only(monkeypatch):
    enable(monkeypatch)
    g = gateway()
    adapter = MagicMock(send_email_reply=AsyncMock(return_value={"status": "sent"}))
    result = await pilot(g, adapter)
    assert result.state == "completed"
    g.claim_staging_email.assert_awaited_once_with(
        "tenant-1", "exec-1", "worker-1", "pilot@example.test"
    )
    adapter.send_email_reply.assert_awaited_once_with(
        to="pilot@example.test", subject="Synthetic", body="Approved snapshot"
    )


@pytest.mark.asyncio
async def test_wrong_recipient_is_denied_before_send(monkeypatch):
    enable(monkeypatch)
    g = gateway()
    g.claim_staging_email.side_effect = PermissionError("not allowlisted")
    adapter = MagicMock(send_email_reply=AsyncMock())
    with pytest.raises(PermissionError):
        await pilot(g, adapter)
    adapter.send_email_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_recipients_rejected_before_claim(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setenv(
        "SECRETARYAI_STAGING_EMAIL_RECIPIENT",
        "pilot@example.test,customer@example.test",
    )
    g = gateway()
    adapter = MagicMock(send_email_reply=AsyncMock())
    with pytest.raises(StagingPilotDisabled):
        await pilot(g, adapter)
    g.claim_staging_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_worker_does_not_send(monkeypatch):
    enable(monkeypatch)
    g = gateway()
    g.claim_staging_email.return_value = {"claimed": False}
    adapter = MagicMock(send_email_reply=AsyncMock())
    receipt = await pilot(g, adapter)
    assert receipt.state == "not_claimed"
    adapter.send_email_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_provider_receipt_requires_reconciliation(monkeypatch):
    enable(monkeypatch)
    g = gateway()
    adapter = MagicMock(send_email_reply=AsyncMock(return_value={"status": "queued"}))
    with pytest.raises(ExecutionReconciliationRequired):
        await pilot(g, adapter)
    g.finish.assert_awaited_once_with(
        "tenant-1", "exec-1", "claim-1", "needs_reconciliation"
    )


@pytest.mark.asyncio
async def test_after_send_write_failure_cannot_retry(monkeypatch):
    enable(monkeypatch)
    g = gateway()
    g.finish.side_effect = ConnectionError("ledger down")
    adapter = MagicMock(send_email_reply=AsyncMock(return_value={"status": "sent"}))
    with pytest.raises(ExecutionReconciliationRequired):
        await pilot(g, adapter)
    adapter.send_email_reply.assert_awaited_once()
