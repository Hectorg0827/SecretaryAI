"""Gate A: draft purchase-order authorization is enforced server-side."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.auth.rbac import require_permission
from app.api.actions import DraftPORequest, draft_purchase_order


def _authorized(role: str) -> bool:
    checker = require_permission("draft_purchase_orders")
    try:
        checker(user={"role": role, "company_id": "tenant-a", "sub": "subject-a"})
    except HTTPException:
        return False
    return True


@pytest.mark.parametrize("role", ["owner", "manager", "back_office"])
def test_explicit_purchase_order_draft_roles_are_allowed(role):
    assert _authorized(role) is True


@pytest.mark.parametrize("role", ["sales_rep", "viewer", "unknown"])
def test_unprivileged_roles_cannot_create_purchase_order_draft(role):
    assert _authorized(role) is False


@pytest.mark.parametrize("invalid_qty", [-5, 0])
def test_po_draft_invalid_quantity_rejected_before_queue(invalid_qty):
    with pytest.raises(ValidationError):
        DraftPORequest(item_id="SKU-A", product_name="Test item", qty=invalid_qty)


def test_po_draft_item_id_must_exist():
    with pytest.raises(ValidationError):
        DraftPORequest(item_id="", product_name="Test item")


@pytest.mark.asyncio
async def test_draft_queue_is_tenant_scoped_and_not_executed():
    db = MagicMock()
    request = DraftPORequest(
        item_id="synthetic-item", product_name="Test product", qty=12
    )
    with patch("app.api.actions.ApprovalQueue") as cls:
        cls.return_value.enqueue = AsyncMock(return_value="fixture-draft-id")
        result = await draft_purchase_order(
            body=request,
            user={"company_id": "tenant-a", "sub": "actor-a", "role": "back_office"},
            db=db,
        )
    assert result["status"] == "queued"
    assert result["draft_id"] == "fixture-draft-id"
    cls.return_value.enqueue.assert_awaited_once()
    assert cls.return_value.enqueue.await_args.kwargs["company_id"] == "tenant-a"
    assert cls.return_value.enqueue.await_args.kwargs["action_type"] == "purchase_order"
