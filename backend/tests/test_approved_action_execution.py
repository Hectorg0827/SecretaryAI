"""Approved business actions must never bypass the durable execution ledger.

These tests use synthetic requests and mock adapters; no email, supplier
communication, payment or production side effect can be performed.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import actions


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", [
    "email_reply", "purchase_order", "unknown",
])
async def test_legacy_executor_is_permanently_quarantined(action_type):
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    adapter.create_purchase_order = AsyncMock()
    draft = {
        "id": "synthetic-draft",
        "action_type": action_type,
        "content": {
            "to": "nobody@example.invalid",
            "body": "Fixture only",
            "vendor_id": "no-vendor",
        },
    }
    with pytest.raises(RuntimeError, match="Legacy inline action execution is disabled"):
        await actions._execute_approved_draft(draft, adapter)
    adapter.send_email_reply.assert_not_awaited()
    adapter.create_purchase_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_approval_cannot_send_without_durable_gateway():
    from fastapi import HTTPException

    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "d1", "status": "pending", "action_type": "email_reply",
         "workflow_run_id": "workflow-1",
         "content": {"to": "a@example.com", "body": "Hello"}}
    ]
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    with patch.dict("os.environ", {"SECRETARYAI_DURABLE_APPROVAL_ENABLED": "0"}), \
         patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="allow"), \
         patch.object(actions.ApprovalQueue, "approve", new_callable=AsyncMock) as approve, \
         patch.object(actions, "write_audit_event") as audit, \
         patch("app.workflows.engine.WorkflowEngine") as workflow:
        with pytest.raises(HTTPException) as error:
            await actions.approve_draft(
                "d1", actions.ApprovalRequest(),
                user={"company_id": "co1", "sub": "owner1"},
                db=db, adapter=adapter,
            )
    assert error.value.status_code == 409
    approve.assert_not_awaited()
    adapter.send_email_reply.assert_not_awaited()
    audit.assert_not_called()
    workflow.return_value.resume_after_approval.assert_not_called()

