"""Purchasing evidence claims must remain unverified and tenant scoped."""
import os
from decimal import Decimal
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
    PurchasingEvidenceClaimRequest,
    list_purchasing_evidence_metadata,
    record_purchasing_evidence_metadata,
)


def _user(role="back_office"):
    return {"company_id": "tenant-1", "sub": "actor-from-jwt", "role": role}


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.filters = []
    def select(self, *_args, **_kwargs):
        return self
    def eq(self, column, value):
        self.filters.append((column, value))
        return self
    def limit(self, _limit):
        return self
    def order(self, *_args, **_kwargs):
        return self
    def execute(self):
        if isinstance(self.data, Exception):
            raise self.data
        return SimpleNamespace(data=self.data)


class FakeDB:
    def __init__(self, orders=None, claims=None):
        self.data = {
            "purchasing_orders": (
                [{"id": "po-1", "po_number": "PO-1", "status": "received"}]
                if orders is None else orders
            ),
            "purchasing_evidence_claims": [] if claims is None else claims,
        }
        self.queries = []
        self.rpc = MagicMock()
        self.rpc.return_value.execute.return_value.data = {
            "verification_status": "unverified",
            "accounting_reconciled": False,
            "replayed": False,
        }
    def table(self, name):
        q = FakeQuery(self.data[name])
        self.queries.append((name, q))
        return q


def _request(**overrides):
    args = {
        "evidence_key": "FREIGHT-1",
        "document_type": "freight_invoice",
        "storage_object_key": "tenant-1/purchasing/invoice.pdf",
        "claimed_sha256": "a" * 64,
        "claimed_amount": Decimal("100.20"),
        "currency": "USD",
    }
    args.update(overrides)
    return PurchasingEvidenceClaimRequest(**args)


@pytest.mark.asyncio
async def test_record_claim_uses_jwt_actor_and_does_not_book_money():
    db = FakeDB()
    result = await record_purchasing_evidence_metadata(
        "PO-1", _request(), user=_user(), db=db,
    )
    assert result == {
        "verification_status": "unverified",
        "accounting_reconciled": False,
        "replayed": False,
    }
    name, payload = db.rpc.call_args.args
    assert name == "record_purchasing_evidence_claim"
    assert payload["p_actor"] == "actor-from-jwt"
    assert payload["p_company_id"] == "tenant-1"
    assert payload["p_claimed_amount"] == "100.20"
    assert payload["p_currency"] == "USD"
    assert "p_verified" not in payload and "p_approver" not in payload


@pytest.mark.asyncio
async def test_unpaired_amount_currency_is_rejected_before_database():
    for request in (
        _request(claimed_amount=None),
        _request(currency=None),
    ):
        db = FakeDB()
        with pytest.raises(HTTPException) as exc:
            await record_purchasing_evidence_metadata(
                "PO-1", request, user=_user(), db=db,
            )
        assert exc.value.status_code == 422
        db.rpc.assert_not_called()


def test_invalid_digest_currency_and_precision_rejected():
    for overrides in (
        {"claimed_sha256": "ab"},
        {"currency": "US"},
        {"currency": "usd"},
        {"claimed_amount": "1.001"},
        {"claimed_amount": -1},
    ):
        with pytest.raises(ValidationError):
            _request(**overrides)


@pytest.mark.asyncio
async def test_claim_listing_keeps_currency_separate_and_proves_no_verification():
    db = FakeDB(claims=[
        {"verification_status": "unverified", "claimed_amount": "100.20", "currency": "USD"},
        {"verification_status": "unverified", "claimed_amount": "20.50", "currency": "USD"},
        {"verification_status": "unverified", "claimed_amount": "50.00", "currency": "EUR"},
        {"verification_status": "unverified", "claimed_amount": None, "currency": None},
    ])
    result = await list_purchasing_evidence_metadata(
        "PO-1", user=_user(), db=db,
    )
    assert result["unverified_claim_totals_by_currency"] == {
        "EUR": "50.00", "USD": "120.70",
    }
    assert result["claim_count"] == 4
    assert result["landed_cost_verified"] is False
    assert result["accounting_reconciled"] is False
    assert result["inventory_changed"] is False
    assert all(("company_id", "tenant-1") in q.filters for _, q in db.queries)
    assert ("po_id", "po-1") in db.queries[-1][1].filters
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_missing_tenant_po_is_not_resolved_cross_tenant():
    db = FakeDB(orders=[])
    with pytest.raises(HTTPException) as exc:
        await list_purchasing_evidence_metadata("PO-OTHER", user=_user(), db=db)
    assert exc.value.status_code == 404
    assert ("company_id", "tenant-1") in db.queries[0][1].filters


@pytest.mark.asyncio
async def test_invalid_or_claimed_verified_records_fail_closed():
    for claim in (
        {"verification_status": "verified", "claimed_amount": "1", "currency": "USD"},
        {"verification_status": "unverified", "claimed_amount": "NaN", "currency": "USD"},
        {"verification_status": "unverified", "claimed_amount": "1", "currency": None},
    ):
        db = FakeDB(claims=[claim])
        with pytest.raises(HTTPException) as exc:
            await list_purchasing_evidence_metadata("PO-1", user=_user(), db=db)
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_evidence_read_denies_sales_rep_and_does_not_query():
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        await list_purchasing_evidence_metadata("PO-1", user=_user("sales_rep"), db=db)
    assert exc.value.status_code == 403
    assert db.queries == []
