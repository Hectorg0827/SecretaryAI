"""QuickBooks versus stock-ledger snapshots are read-only and never auto-reconcile."""
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.api.logistics import (preview_stock_reconciliation,
                               process_warehouse_receipt, WarehouseReceiptRequest)


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.filters = []
    def select(self, *args, **kwargs):
        return self
    def eq(self, field, value):
        self.filters.append((field, value))
        return self
    def limit(self, count):
        self.limit_count = count
        return self
    def execute(self):
        if isinstance(self.data, Exception):
            raise self.data
        return SimpleNamespace(data=self.data)


class FakeDB:
    def __init__(self, sku=None, balances=None):
        self.tables = {
            "stock_skus": sku if sku is not None else [{
                "sku": "CASE-A", "product_name": "Case A", "base_unit": "case",
                "qb_item_id": "QB-123",
            }],
            "stock_balances": balances if balances is not None else [{
                "warehouse_id": "w-1", "lot_code": "LOT-01", "bucket": "available",
                "unit": "case", "quantity": 1180,
                "updated_at": "2026-10-17T15:00:00Z",
            }, {
                "warehouse_id": "w-1", "lot_code": "LOT-01", "bucket": "damaged",
                "unit": "case", "quantity": 20,
                "updated_at": "2026-10-17T15:00:00Z",
            }],
        }
        self.queries = []
        self.rpc = MagicMock()
    def table(self, name):
        q = FakeQuery(self.tables[name])
        self.queries.append((name, q))
        return q


def actor(role="manager"):
    return {"role": role, "company_id": "co-1", "sub": "user-1"}


def qb_adapter(qty=Decimal("1200"), qb_id="QB-123"):
    return SimpleNamespace(
        get_inventory_qb=AsyncMock(return_value=[
            SimpleNamespace(qb_id=qb_id, quantity_on_hand=qty),
        ])
    )


@pytest.mark.asyncio
async def test_equal_source_quantities_never_marked_reconciled():
    db,adapter = FakeDB(),qb_adapter()
    with patch("app.api.deps.get_adapter", return_value=adapter):
        response=await preview_stock_reconciliation("CASE-A",user=actor(),db=db)
    assert response["ledger_quantities"] == {
        "available":1180.0, "damaged":20.0, "quarantined":0.0
    }
    assert response["ledger_total"] == 1200
    assert response["qb_quantity"] == 1200
    assert response["indicative_quantity_difference"] == 0
    assert response["state"]=="unverified_scope"
    assert response["reconciled"] is False
    assert response["accounting_reconciled"] is False
    assert "opening balances" in response["scope_warning"]
    assert all(("company_id","co-1") in query.filters for _,query in db.queries)
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_missing_explicit_qb_mapping_never_fuzzy_matches():
    db=FakeDB(sku=[{
        "sku":"CASE-A","product_name":"Case A",
        "base_unit":"case","qb_item_id":None
    }])
    with patch("app.api.deps.get_adapter") as get_adapter:
        result=await preview_stock_reconciliation("CASE-A",user=actor(),db=db)
    get_adapter.assert_not_called()
    assert result["state"]=="missing_explicit_qb_mapping"
    assert result["qb_quantity"] is None
    assert result["reconciled"] is False


@pytest.mark.asyncio
async def test_quickbooks_unavailable_is_not_automatically_zero():
    db=FakeDB()
    adapter=qb_adapter()
    adapter.get_inventory_qb.side_effect=ConnectionError("qb fail")
    with patch("app.api.deps.get_adapter",return_value=adapter):
        with pytest.raises(HTTPException) as err:
            await preview_stock_reconciliation("CASE-A",user=actor(),db=db)
    assert err.value.status_code==503


@pytest.mark.asyncio
async def test_cross_tenant_scope_uses_only_authenticated_company():
    db=FakeDB(sku=[])
    with pytest.raises(HTTPException) as err:
        await preview_stock_reconciliation("CASE-A",user=actor(),db=db)
    assert err.value.status_code==404
    assert ("company_id","co-1") in db.queries[0][1].filters
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_incomplete_balance_page_fails_closed():
    db=FakeDB(balances=[{
        "warehouse_id":"w", "lot_code":"L","unit":"case","bucket":"available",
        "quantity":1
    }] * 1001)
    with pytest.raises(HTTPException) as err:
        await preview_stock_reconciliation("CASE-A",user=actor(),db=db)
    assert err.value.status_code==503


@pytest.mark.asyncio
async def test_invalid_ledger_bucket_is_not_ignored():
    db=FakeDB(balances=[{
        "bucket":"reserved", "unit":"case", "quantity":4
    }])
    with pytest.raises(HTTPException) as err:
        await preview_stock_reconciliation("CASE-A",user=actor(),db=db)
    assert err.value.status_code==503


@pytest.mark.asyncio
async def test_legacy_warehouse_analysis_does_not_promote_po_status():
    receipt=SimpleNamespace(
        total_cases_received=1200,
        po_to_ship_days=None,ship_to_arrival_days=None,
        arrival_to_customs_days=None,customs_to_warehouse_days=None,
        total_lead_time_days=None,
    )
    lm=SimpleNamespace(process_warehouse_receipt=MagicMock(return_value={
        "receipt":receipt,"shortages":[],"alert":None,
    }))
    db=MagicMock()
    with patch("app.api.logistics._get_logistics_module",return_value=lm):
        response=await process_warehouse_receipt(WarehouseReceiptRequest(
            booking_id="BK1",po_number="PO1",line_items=[]
        ),user=actor(),db=db)
    assert response["status"]=="analysis_only"
    assert response["inventory_posted"] is False
    db.table.assert_not_called()
