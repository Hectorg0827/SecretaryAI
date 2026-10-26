"""Durable purchasing routes must enforce tenant identity and two-person posting.

Real PostgreSQL stock/PO transitions are exercised by
scripts/test_purchasing_receiving.sql in the CI schema job.
"""
import os
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.api.logistics import (
    DurablePORequest, DurableReceiptRequest, _execute_purchasing_rpc,
    create_durable_purchase_order, approve_durable_purchase_order,
    stage_durable_warehouse_receipt, approve_staged_receipt_and_post_inventory,
    get_durable_purchase_order_detail,
)


def _db(response):
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = response
    return db


def _actor(role="manager", uid="logged-in-user", company_id="current-tenant"):
    return {"sub": uid, "role": role, "company_id": company_id}


@pytest.mark.asyncio
async def test_draft_creation_is_tenant_scoped_and_never_books_stock():
    db = _db({"status": "draft", "inventory_posted": False})
    wh = uuid4()
    data = DurablePORequest(
        po_number="PO-100", supplier_ref="SUP", warehouse_id=wh,
        lines=[{"sku": "CASE-1", "qty_ordered": 1200}],
    )
    result = await create_durable_purchase_order(data, user=_actor("back_office"), db=db)
    assert result["inventory_posted"] is False
    args = db.rpc.call_args
    assert args.args[0] == "create_warehouse_purchase_order"
    assert args.args[1]["p_actor"] == "logged-in-user"
    assert args.args[1]["p_company_id"] == "current-tenant"
    assert args.args[1]["p_warehouse_id"] == str(wh)
    assert args.args[1]["p_lines"] == [{"sku": "CASE-1", "qty_ordered": 1200}]


@pytest.mark.asyncio
async def test_approvals_use_authenticated_user_not_request_supplied_ids():
    db = _db({"status": "approved", "inventory_posted": False})
    await approve_durable_purchase_order("PO-100", user=_actor(uid="approving-user"), db=db)
    assert db.rpc.call_args.args == (
        "approve_warehouse_purchase_order",
        {"p_company_id": "current-tenant", "p_po_number": "PO-100",
         "p_actor": "approving-user"},
    )


@pytest.mark.asyncio
async def test_stage_receipt_posts_nothing_and_requires_quantities():
    db = _db({"status": "pending", "inventory_posted": False})
    request = DurableReceiptRequest(
        receipt_key="RECEIPT-1",
        lines=[{"sku": "CASE-1", "lot_code": "A", "accepted_qty": 1180,
                "damaged_qty": 20}],
    )
    result = await stage_durable_warehouse_receipt(
        "PO-100", request, user=_actor("back_office"), db=db)
    assert result["inventory_posted"] is False
    args = db.rpc.call_args.args
    assert args[0] == "stage_warehouse_receipt"
    assert args[1]["p_actor"] == "logged-in-user"
    assert args[1]["p_lines"][0]["damaged_qty"] == 20
    assert args[1]["p_lines"][0]["accepted_qty"] == 1180

    db.rpc.reset_mock()
    with pytest.raises(HTTPException) as err:
        await stage_durable_warehouse_receipt(
            "PO-100", DurableReceiptRequest(
                receipt_key="EMPTY",
                lines=[{"sku": "CASE-1", "accepted_qty": 0}]),
            user=_actor("back_office"), db=db,
        )
    assert err.value.status_code == 422
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_post_approval_cannot_impersonate_second_user():
    db = _db({"inventory_posted": True, "accounting_reconciled": False})
    staged_id = uuid4()
    await approve_staged_receipt_and_post_inventory(
        staged_id, user=_actor(uid="manager-from-jwt"), db=db)
    args = db.rpc.call_args.args
    assert args == ("approve_staged_warehouse_receipt", {
        "p_company_id": "current-tenant",
        "p_staged_receipt_id": str(staged_id),
        "p_actor": "manager-from-jwt",
    })
    assert "p_receiver" not in args[1] and "p_approver" not in args[1]


@pytest.mark.asyncio
async def test_sales_rep_cannot_submit_purchase_documents_even_if_called_directly():
    db = _db({})
    request = DurablePORequest(
        po_number="PO-100", supplier_ref="SUP", warehouse_id=uuid4(),
        lines=[{"sku": "CASE-1", "qty_ordered": 1}])
    with pytest.raises(HTTPException) as err:
        await create_durable_purchase_order(
            request, user=_actor("sales_rep"), db=db)
    assert err.value.status_code == 403
    db.rpc.assert_not_called()


def test_purchasing_input_is_bounded_and_rejects_negative_quantities():
    with pytest.raises(ValidationError):
        DurablePORequest(
            po_number="PO", supplier_ref="SUP", warehouse_id=uuid4(),
            lines=[{"sku": "A", "qty_ordered": -1}])
    with pytest.raises(ValidationError):
        DurableReceiptRequest(
            receipt_key="KEY", lines=[{"sku": "A", "damaged_qty": -1}])


@pytest.mark.parametrize("code,status", [
    ("42501", 403), ("P0002", 404), ("22023", 422),
    ("23503", 409), ("23514", 409), ("23505", 409),
    ("network", 503),
])
def test_db_rejections_do_not_leak_raw_sql_to_clients(code,status):
    db = _db({})
    class DBError(Exception):
        def __init__(self):
            self.code = code
            super().__init__("private database value and connection password")
    db.rpc.return_value.execute.side_effect = DBError()
    with pytest.raises(HTTPException) as err:
        _execute_purchasing_rpc(db, "stage_warehouse_receipt", {})
    assert err.value.status_code == status
    assert "password" not in str(err.value.detail)


class _ReadQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
    def select(self, *args, **kwargs):
        return self
    def eq(self, field, value):
        self.filters.append((field, value))
        return self
    def order(self, *args, **kwargs):
        return self
    def limit(self, *args, **kwargs):
        return self
    def execute(self):
        return SimpleNamespace(data=self.rows)


@pytest.mark.asyncio
async def test_po_detail_reveals_receipt_provenance_without_claiming_qb_sync():
    tables = {
        "purchasing_orders": [{
            "id": "order-1", "po_number": "PO-2026", "supplier_ref": "SUP",
            "status": "received",
        }],
        "purchasing_order_lines": [{
            "sku": "A", "qty_ordered": "1200.000", "qty_received": "1200.000",
        }],
        "staged_warehouse_receipts": [{
            "id": "staged-1", "status": "posted",
            "receipt_key": "RECEIPT-1",
        }],
        "warehouse_receipt_documents": [{
            "id": "posted-1", "receipt_key": "RECEIPT-1",
        }],
    }
    read_queries = []
    db = MagicMock()
    def table(name):
        query = _ReadQuery(tables[name])
        read_queries.append((name, query))
        return query
    db.table.side_effect = table
    response = await get_durable_purchase_order_detail(
        "PO-2026", user=_actor("manager"), db=db,
    )
    assert response["ordered_cases"] == "1200.000"
    assert response["received_cases"] == "1200.000"
    assert response["remaining_cases"] == "0.000"
    assert response["inventory_posted"] is True
    assert response["accounting_reconciled"] is False
    assert len(response["staged_receipts"]) == 1
    assert len(response["posted_receipts"]) == 1
    assert all(("company_id", "current-tenant") in query.filters
               for _, query in read_queries)
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_po_detail_missing_tenant_order_does_not_search_other_tenants():
    db = MagicMock()
    query = _ReadQuery([])
    db.table.return_value = query
    with pytest.raises(HTTPException) as err:
        await get_durable_purchase_order_detail(
            "PO-OTHER", user=_actor("manager"), db=db,
        )
    assert err.value.status_code == 404
    assert ("company_id", "current-tenant") in query.filters
    assert ("po_number", "PO-OTHER") in query.filters
