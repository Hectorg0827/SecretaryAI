"""Approved business actions must not be reported as executed on failure."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import actions


@pytest.mark.asyncio
async def test_email_send_failure_is_not_reported_as_sent():
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(side_effect=OSError("mail service unavailable"))
    status = await actions._execute_approved_draft(
        {"id": "d1", "action_type": "email_reply",
         "content": {"to": "a@example.com", "body": "Hello"}}, adapter
    )
    assert status == "needs_reconciliation"
    adapter.send_email_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_email_send_success_reports_sent():
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(return_value={"status": "sent"})
    status = await actions._execute_approved_draft(
        {"id": "d1", "action_type": "email_reply",
         "content": {"to": "a@example.com", "subject": "Hi", "body": "Hello"}},
        adapter,
    )
    assert status == "sent"


@pytest.mark.asyncio
async def test_unsupported_action_never_claims_execution():
    assert await actions._execute_approved_draft(
        {"id": "d1", "action_type": "unknown", "content": {}}, MagicMock()
    ) == "unsupported"


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


@pytest.mark.asyncio
async def test_email_send_without_explicit_provider_receipt_is_uncertain():
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock(return_value={"status": "queued"})
    status = await actions._execute_approved_draft(
        {"id": "d1", "action_type": "email_reply",
         "content": {"to": "a@example.test", "body": "Approved"}},
        adapter,
    )
    assert status == "needs_reconciliation"
