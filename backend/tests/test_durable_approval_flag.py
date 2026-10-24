"""V14 dark-launch approval route: no actual sends or purchases."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import actions
from app.workflows.execution_gateway import ExecutionDatabaseUnavailable


def database():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "draft-1", "status": "pending", "action_type": "email_reply",
         "content": {"to": "test@example.invalid", "body": "Synthetic"}}
    ]
    return db


@pytest.mark.asyncio
async def test_enabled_durable_approval_only_queues(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_DURABLE_APPROVAL_ENABLED", "1")
    db = database()
    adapter = MagicMock()
    adapter.send_email_reply = AsyncMock()
    gw = MagicMock()
    gw.approve_and_enqueue = AsyncMock(return_value={
        "execution_id": "exec-1", "status": "approved",
        "queued": True, "already_processed": False,
    })
    with patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="allow"), \
         patch.object(actions.PostgresDurableGateway, "from_environment",
                      return_value=gw), \
         patch.object(actions, "write_audit_event") as audit, \
         patch.object(actions, "_execute_approved_draft", new_callable=AsyncMock) as legacy:
        result = await actions.approve_draft(
            "draft-1", actions.ApprovalRequest(),
            user={"company_id": "tenant-a", "sub": "actor-a"},
            db=db, adapter=adapter,
        )
    assert result["execution_status"] == "queued"
    assert result["execution_id"] == "exec-1"
    gw.approve_and_enqueue.assert_awaited_once()
    legacy.assert_not_awaited()
    adapter.send_email_reply.assert_not_awaited()
    assert audit.call_args.kwargs["metadata"]["execution_status"] == "queued"


@pytest.mark.asyncio
async def test_enabled_missing_credentials_fails_before_approval(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_DURABLE_APPROVAL_ENABLED", "1")
    db = database()
    with patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="allow"), \
         patch.object(actions.PostgresDurableGateway, "from_environment",
                      side_effect=ExecutionDatabaseUnavailable("not configured")), \
         patch.object(actions, "_execute_approved_draft", new_callable=AsyncMock) as legacy:
        with pytest.raises(HTTPException) as exc:
            await actions.approve_draft(
                "draft-1", actions.ApprovalRequest(),
                user={"company_id": "tenant-a", "sub": "actor-a"},
                db=db, adapter=MagicMock(),
            )
    assert exc.value.status_code == 503
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_policy_deny_never_queues(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_DURABLE_APPROVAL_ENABLED", "1")
    with patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="deny"), \
         patch.object(actions.PostgresDurableGateway, "from_environment") as gateway:
        with pytest.raises(HTTPException) as exc:
            await actions.approve_draft(
                "draft-1", actions.ApprovalRequest(),
                user={"company_id": "tenant-a", "sub": "actor-a"},
                db=database(), adapter=MagicMock(),
            )
    assert exc.value.status_code == 403
    gateway.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_rejects_non_email_before_execution(monkeypatch):
    monkeypatch.setenv("SECRETARYAI_DURABLE_APPROVAL_ENABLED", "1")
    db = database()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data[0]["action_type"] = "purchase_order"
    with patch.object(actions, "_execution_policy_effect", new_callable=AsyncMock,
                      return_value="allow"), \
         patch.object(actions.PostgresDurableGateway, "from_environment") as gateway:
        with pytest.raises(HTTPException) as exc:
            await actions.approve_draft(
                "draft-1", actions.ApprovalRequest(),
                user={"company_id": "tenant-a", "sub": "actor-a"},
                db=db, adapter=MagicMock(),
            )
    assert exc.value.status_code == 409
    gateway.assert_not_called()
