"""V12 coordinator tests: no real mail, payments, stock or network."""
from unittest.mock import AsyncMock

import pytest

from app.workflows.approved_execution import (
    ExecutionReconciliationRequired,
    run_approved_external_action,
)


def gateway():
    from unittest.mock import MagicMock
    g = MagicMock()
    g.enqueue = AsyncMock(return_value="execution-1")
    g.claim = AsyncMock(return_value="token-1")
    g.finish = AsyncMock(return_value=True)
    return g


async def run(g, action):
    return await run_approved_external_action(
        gateway=g, company_id="tenant-a", draft_id="draft-1",
        idempotency_key="draft-1", worker_id="worker-a",
        external_action=action,
    )


@pytest.mark.asyncio
async def test_winner_executes_and_finalizes_once():
    g = gateway()
    effect = AsyncMock()
    receipt = await run(g, effect)
    assert receipt.state == "completed" and receipt.claimed
    effect.assert_awaited_once()
    g.finish.assert_awaited_once_with(
        "tenant-a", "execution-1", "token-1", "completed"
    )


@pytest.mark.asyncio
async def test_losing_worker_never_sends():
    g = gateway()
    g.claim.return_value = None
    effect = AsyncMock()
    receipt = await run(g, effect)
    assert receipt.state == "not_claimed"
    effect.assert_not_awaited()
    g.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_failure_never_sends():
    g = gateway()
    g.enqueue.side_effect = ConnectionError("database down")
    effect = AsyncMock()
    with pytest.raises(ConnectionError):
        await run(g, effect)
    effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_failure_never_sends():
    g = gateway()
    g.claim.side_effect = TimeoutError("database unavailable")
    effect = AsyncMock()
    with pytest.raises(TimeoutError):
        await run(g, effect)
    effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_external_effect_requires_reconciliation():
    g = gateway()
    effect = AsyncMock(side_effect=TimeoutError("provider did not acknowledge"))
    with pytest.raises(ExecutionReconciliationRequired):
        await run(g, effect)
    g.finish.assert_awaited_once_with(
        "tenant-a", "execution-1", "token-1", "needs_reconciliation"
    )


@pytest.mark.asyncio
async def test_failed_reconciliation_write_still_cannot_retry_send():
    g = gateway()
    g.finish.side_effect = ConnectionError("ledger unavailable")
    effect = AsyncMock(side_effect=TimeoutError("provider uncertain"))
    with pytest.raises(ExecutionReconciliationRequired):
        await run(g, effect)
    effect.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_success_but_result_write_failure_is_uncertain():
    g = gateway()
    g.finish.side_effect = ConnectionError("database down after send")
    effect = AsyncMock()
    with pytest.raises(ExecutionReconciliationRequired):
        await run(g, effect)
    effect.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_worker_cannot_report_completion():
    g = gateway()
    g.finish.return_value = False
    effect = AsyncMock()
    with pytest.raises(ExecutionReconciliationRequired):
        await run(g, effect)
    effect.assert_awaited_once()
