"""Finance staging gate: provisional valuations and strict read-only Bill candidate."""
import os
from decimal import Decimal
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
from uuid import uuid4

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
    ProvisionalPOCostRequest,
    record_provisional_po_cost_assessment,
    preview_provisional_po_valuation,
)
from app.services.purchasing_finance import compare_qbo_bill, money, value_receipts
from app.connectors.qb_online import QBOnlineAdapter


ASSESSMENT = {
    "currency": "USD", "fx_rate_to_usd": "1", "landed_cost_total": "0",
    "lines": [{"sku": "QA-1200", "qty_ordered": 1200, "unit_cost": "10.00"}],
}
PO_LINES = [{"sku": "QA-1200", "qty_ordered": 1200, "qty_received": 1200,
             "qb_item_id": "234"}]
RECEIPTS = [{"sku": "QA-1200", "accepted_qty": 1180,
             "damaged_qty": 20, "quarantined_qty": 0}]
QBO_BILL = {
    "Id": "12", "VendorRef": {"value": "444"},
    "CurrencyRef": {"value": "USD"}, "TotalAmt": 12000.0,
    "Line": [{"DetailType": "ItemBasedExpenseLineDetail", "Amount": 12000.0,
              "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "234"},
                                             "Qty": 1200, "UnitPrice": 10.0}}],
}


def test_1200_case_pilot_has_separate_damaged_exposure_and_no_accounting_post():
    valuation = value_receipts(ASSESSMENT, PO_LINES, RECEIPTS)
    assert valuation["estimated_order_value"] == "12000.00"
    assert valuation["estimated_value_by_bucket"] == {
        "available": "11800.00", "damaged": "200.00",
        "quarantined": "0.00", "unreceived": "0.00",
    }
    assert valuation["rounding_residual"] == "0.00"
    assert valuation["indicative_usd_order_value"] == "12000.00"
    assert valuation["supplier_claim_candidate"] == {
        "damaged_cases": "20", "estimated_damaged_exposure": "200.00",
        "currency": "USD", "claim_status": "unfiled_unverified",
        "supplier_credit_posted": False,
    }
    assert valuation["valuation_verified"] is False
    assert valuation["accounting_reconciled"] is False


def test_partial_receipt_retains_unreceived_value_and_quarantine():
    valuation = value_receipts({**ASSESSMENT, "landed_cost_total": "120"}, PO_LINES, [
        {"sku": "QA-1200", "accepted_qty": 900, "damaged_qty": 20,
         "quarantined_qty": 10}])
    assert valuation["estimated_value_by_bucket"] == {
        "available": "9090.00", "damaged": "202.00",
        "quarantined": "101.00", "unreceived": "2727.00",
    }
    assert valuation["estimated_order_value"] == "12120.00"


def test_eur_costs_have_explicit_but_unverified_usd_conversion():
    valuation = value_receipts({**ASSESSMENT, "currency": "EUR",
                                "fx_rate_to_usd": "1.250000"}, PO_LINES, RECEIPTS)
    assert valuation["indicative_usd_order_value"] == "15000.00"
    assert valuation["indicative_usd_value_by_bucket"]["damaged"] == "250.00"
    assert valuation["valuation_verified"] is False


@pytest.mark.parametrize("broken_lines,broken_receipts", [
    ([{"sku": "WRONG", "qty_ordered": 1200, "unit_cost": "10"}], RECEIPTS),
    ([{"sku": "QA-1200", "qty_ordered": 1199, "unit_cost": "10"}], RECEIPTS),
    (ASSESSMENT["lines"], [{"sku": "QA-1200", "accepted_qty": 1200,
                            "damaged_qty": 20, "quarantined_qty": 0}]),
])
def test_financial_valuation_fails_closed_on_mismatched_scope(broken_lines, broken_receipts):
    with pytest.raises(ValueError):
        value_receipts({**ASSESSMENT, "lines": broken_lines}, PO_LINES, broken_receipts)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, "1.0000001", None, True])
def test_financial_decimals_must_be_exact_and_bounded(value):
    with pytest.raises(ValueError):
        money(value)


def test_qbo_bill_exact_candidate_does_not_reconcile_accounts():
    check = compare_qbo_bill(ASSESSMENT, PO_LINES, QBO_BILL, "444")
    assert check["bill_candidate_matched"] is True
    assert check["accounting_reconciled"] is False


@pytest.mark.parametrize("change", [
    {"TotalAmt": 11999.99},
    {"VendorRef": {"value": "other"}},
    {"CurrencyRef": {"value": "EUR"}},
    {"Line": []},
    {"Line": [{"DetailType": "AccountBasedExpenseLineDetail", "Amount": 12000}]},
])
def test_qbo_bill_mismatch_never_marks_candidate(change):
    check = compare_qbo_bill(ASSESSMENT, PO_LINES, {**QBO_BILL, **change}, "444")
    assert check["bill_candidate_matched"] is False
    assert check["accounting_reconciled"] is False


def test_unmapped_qbo_item_blocks_candidate():
    check = compare_qbo_bill(ASSESSMENT,
                             [{**PO_LINES[0], "qb_item_id": None}], QBO_BILL, "444")
    assert check["bill_candidate_matched"] is False


@pytest.mark.asyncio
async def test_qbo_bill_adapter_uses_single_get_only():
    adapter = object.__new__(QBOnlineAdapter)
    adapter._get_entity = AsyncMock(return_value={"Bill": QBO_BILL})
    bill = await adapter.get_bill_read_only("12")
    assert bill["Id"] == "12"
    adapter._get_entity.assert_awaited_once_with("bill", "12")
    with pytest.raises(ValueError):
        await adapter.get_bill_read_only("12 OR 1=1")
    adapter._get_entity.assert_awaited_once()


@pytest.mark.asyncio
async def test_qbo_bill_adapter_rejects_malformed_provider_data():
    adapter = object.__new__(QBOnlineAdapter)
    adapter._get_entity = AsyncMock(return_value={"Bill": {"Id": "13"}})
    with pytest.raises(ValueError):
        await adapter.get_bill_read_only("12")


def test_cost_submission_uses_current_actor_and_company():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "status": "provisional", "accounting_reconciled": False}
    request = ProvisionalPOCostRequest(
        assessment_key="QA-FINANCE-1", currency="USD", fx_rate_to_usd="1",
        landed_cost_total="0", lines=ASSESSMENT["lines"])
    import asyncio
    result = asyncio.run(record_provisional_po_cost_assessment(
        "QA-PO-1200", request,
        user={"company_id": "tenant-one", "sub": "actor-one", "role": "back_office"},
        db=db))
    args = db.rpc.call_args.args
    assert result["accounting_reconciled"] is False
    assert args[0] == "record_provisional_purchasing_cost_assessment"
    assert args[1]["p_company_id"] == "tenant-one"
    assert args[1]["p_actor"] == "actor-one"
    assert args[1]["p_lines"][0]["unit_cost"] == "10.00"


@pytest.mark.parametrize("kwargs", [
    {"currency": "usd"}, {"fx_rate_to_usd": "0"},
    {"fx_rate_to_usd": "1.1"}, {"landed_cost_total": "-1"},
    {"landed_cost_total": 1.0},
    {"lines": [{"sku": "QA-1200", "qty_ordered": 1200, "unit_cost": 10.0}]},
    {"lines": [ASSESSMENT["lines"][0], ASSESSMENT["lines"][0]]},
])
def test_cost_request_rejects_unsafe_inputs(kwargs):
    values = {"assessment_key": "A", "currency": "USD", "fx_rate_to_usd": "1",
              "landed_cost_total": "0", "lines": ASSESSMENT["lines"]}
    values.update(kwargs)
    with pytest.raises(ValidationError):
        ProvisionalPOCostRequest(**values)
