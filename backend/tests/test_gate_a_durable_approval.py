"""Gate A: no legacy provider delivery; policy outage denies external effects."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException

from app.api import actions
from app.domain.contracts import ActionClass, ActionProposal
from app.domain.policy import PolicyEngine, PolicySourceUnavailable


def proposal(action_class):
    return ActionProposal(
        action_type="email_reply", action_class=action_class,
        description="Fixture only", company_id="tenant-x", requested_by="actor-x",
    )


@pytest.mark.asyncio
async def test_missing_policy_database_blocks_commit_even_when_defaults_exist():
    db = MagicMock()
    db.table.side_effect = RuntimeError("policy DB offline")
    engine = PolicyEngine("tenant-x", db)
    with pytest.raises(PolicySourceUnavailable):
        await engine.evaluate(proposal(ActionClass.COMMIT))


@pytest.mark.asyncio
async def test_missing_policy_database_blocks_destructive_actions():
    db = MagicMock()
    db.table.side_effect = RuntimeError("policy DB offline")
    with pytest.raises(PolicySourceUnavailable):
        await PolicyEngine("tenant-x", db).evaluate(proposal(ActionClass.DESTRUCTIVE))


@pytest.mark.asyncio
async def test_policy_source_failure_yields_denial_to_approval_gateway():
    db = MagicMock()
    db.table.side_effect = RuntimeError("policy DB offline")
    effect = await actions._execution_policy_effect(
        db, "tenant-x", "actor-x",
        {"id": "draft-id", "action_type": "email_reply"}, {"body": "Fixture"},
    )
    assert effect == "deny"


@pytest.mark.asyncio
async def test_durable_approval_queues_without_executing_provider():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft-x", "status": "pending", "action_type": "email_reply",
         "content": {"to": "test@example.invalid", "subject": "Fixture", "body": "No send"}}
    ]
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    gateway = MagicMock()
    gateway.approve_and_enqueue = AsyncMock(return_value={
        "execution_id": "execution-x", "queued": True, "status": "approved",
        "already_processed": False,
    })
    with patch.dict("os.environ", {"SECRETARYAI_DURABLE_APPROVAL_ENABLED": "1"}), \
         patch.object(actions, "_execution_policy_effect", new=AsyncMock(return_value="require_approval")), \
         patch.object(actions.PostgresDurableGateway, "from_environment", return_value=gateway), \
         patch.object(actions, "write_audit_event") as audit, \
         patch.object(actions.ApprovalQueue, "approve", new_callable=AsyncMock) as legacy:
        response = await actions.approve_draft(
            "draft-x", actions.ApprovalRequest(),
            user={"company_id": "tenant-x", "sub": "actor-x"},
            db=db, adapter=adapter,
        )
    assert response["execution_status"] == "queued"
    assert response["execution_id"] == "execution-x"
    gateway.approve_and_enqueue.assert_awaited_once()
    legacy.assert_not_awaited()
    adapter.send_email_reply.assert_not_awaited()
    assert audit.call_args.kwargs["metadata"]["execution_status"] == "queued"


@pytest.mark.asyncio
async def test_approval_with_durable_mode_rejects_unsupported_po_without_side_effect():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft-x", "status": "pending", "action_type": "purchase_order",
         "content": {"sku": "fixture"}}
    ]
    adapter = MagicMock()
    with patch.dict("os.environ", {"SECRETARYAI_DURABLE_APPROVAL_ENABLED": "1"}), \
         patch.object(actions, "_execution_policy_effect", new=AsyncMock(return_value="require_approval")), \
         patch.object(actions, "_execute_approved_draft", new_callable=AsyncMock) as unsafe:
        with pytest.raises(HTTPException) as err:
            await actions.approve_draft(
                "draft-x", actions.ApprovalRequest(),
                user={"company_id": "tenant-x", "sub": "actor-x"},
                db=db, adapter=adapter,
            )
    assert err.value.status_code == 409
    unsafe.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_durable_approval_does_not_claim_new_dispatch_or_audit():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft-x", "status": "pending", "action_type": "email_reply",
         "content": {"to": "test@example.invalid", "body": "No send"}}
    ]
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    gateway = MagicMock()
    gateway.approve_and_enqueue = AsyncMock(return_value={
        "execution_id": "existing-execution", "queued": False,
        "already_processed": True, "status": "approved",
    })
    with patch.dict("os.environ", {"SECRETARYAI_DURABLE_APPROVAL_ENABLED": "1"}), \
         patch.object(actions, "_execution_policy_effect", new=AsyncMock(return_value="require_approval")), \
         patch.object(actions.PostgresDurableGateway, "from_environment", return_value=gateway), \
         patch.object(actions, "write_audit_event") as audit:
        response = await actions.approve_draft(
            "draft-x", actions.ApprovalRequest(),
            user={"company_id": "tenant-x", "sub": "actor-x"},
            db=db, adapter=adapter,
        )
    assert response["execution_status"] == "already_processed"
    assert response["already_processed"] is True
    assert response["execution_id"] == "existing-execution"
    adapter.send_email_reply.assert_not_awaited()
    audit.assert_not_called()


@pytest.mark.asyncio
async def test_unqueued_durable_response_without_prior_processing_is_rejected():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft-x", "status": "pending", "action_type": "email_reply",
         "content": {"to": "test@example.invalid", "body": "No send"}}
    ]
    gateway = MagicMock()
    gateway.approve_and_enqueue = AsyncMock(return_value={
        "execution_id": "ambiguous-id", "queued": False, "already_processed": False,
    })
    with patch.dict("os.environ", {"SECRETARYAI_DURABLE_APPROVAL_ENABLED": "1"}), \
         patch.object(actions, "_execution_policy_effect", new=AsyncMock(return_value="allow")), \
         patch.object(actions.PostgresDurableGateway, "from_environment", return_value=gateway), \
         patch.object(actions, "write_audit_event") as audit:
        with pytest.raises(HTTPException) as caught:
            await actions.approve_draft(
                "draft-x", actions.ApprovalRequest(),
                user={"company_id": "tenant-x", "sub": "actor-x"},
                db=db, adapter=MagicMock(),
            )
    assert caught.value.status_code == 409
    audit.assert_not_called()
