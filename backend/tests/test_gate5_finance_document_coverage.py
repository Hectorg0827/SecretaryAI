"""Gate 5: sandbox-only AR/AP document counts, never unverified money or writes."""
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.api.briefing import BriefingSourceUnavailable, build_owner_briefing
from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.unified_adapter import UnifiedDataAdapter
from app.domain.briefing_wire import BriefingTodayResponseV1
from app.scheduler.dashboard_snapshot import SnapshotDataInvalid, validate_dashboard_cached_payload
from app.scheduler.finance_document_coverage import (
    FinanceDocumentCoverageInvalid, read_finance_document_coverage,
    validate_finance_document_coverage,
)

TODAY = date(2026, 11, 10)
OBSERVED = datetime(2026, 11, 10, 5, tzinfo=timezone.utc)


def _qb():
    return QBOnlineAdapter("client", "secret", "access", "refresh", "realm")


def _bill(identifier="42", amount="25", balance="10", due="2026-11-01"):
    return {"Id": identifier, "TxnDate": "2026-10-20", "DueDate": due,
            "TotalAmt": amount, "Balance": balance, "VendorRef": {"value": "vendor"}}


def _invoice(identifier="25", amount="30", balance="10", due="2026-11-01"):
    return {"Id": identifier, "TxnDate": "2026-10-20", "DueDate": due,
            "TotalAmt": amount, "Balance": balance,
            "CustomerRef": {"value": "customer", "name": "Sensitive Name"}}


def _snapshot(coverage):
    return {"generated_at": OBSERVED.isoformat(), "payload": {
        "source_read_status": "completed", "source_quality": "partial",
        "accounts": {"healthy": 1, "slowing": 0, "at_risk": 0,
                     "dormant": 0, "unknown": 0, "total": 1},
        "inventory_alerts": [], "sales_30d": 20.0,
        "finance_document_coverage": coverage,
    }}


@pytest.mark.asyncio
async def test_both_sources_observed_count_only_and_never_leak_identity_or_money():
    qb = _qb()
    qb._query = AsyncMock(side_effect=[
        {"QueryResponse": {"Invoice": [_invoice()]}, "time": "2026-11-10T05:00:00Z"},
        {"QueryResponse": {"Bill": [_bill()]}, "time": "2026-11-10T05:00:00Z"},
    ])
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = qb
    with patch("app.config.get_settings") as settings:
        settings.return_value.intuit_environment = "sandbox"
        result = await read_finance_document_coverage(adapter, as_of=TODAY)
    for kind in ("ar", "ap"):
        assert result[kind] == {
            "status": "observed", "source_quality": "partial", "reason": None,
            "headers_examined": 1, "open_count": 1,
            "overdue_count": 1, "due_date_missing_count": 0,
        }
    assert result["currency_verified"] is False
    assert result["ledger_reconciled"] is False
    assert "Sensitive Name" not in str(result)
    assert "vendor" not in str(result)
    assert "25" not in str(result)
    assert qb._query.await_count == 2
    assert all(call.args[0].startswith("SELECT * FROM ") for call in qb._query.await_args_list)
    briefing = build_owner_briefing(company_id="tenant-a", role="manager",
                                    snapshot=_snapshot(result), now=OBSERVED)
    assert briefing["source_coverage"] == "dashboard_snapshot_plus_finance_docs"
    assert BriefingTodayResponseV1.model_validate(briefing).complete_digest is False
    assert len([e for e in briefing["sections"]["scoreboard"] if "QBO sandbox headers" in e["title"]]) == 2
    assert "Sensitive Name" not in str(briefing)
    assert "vendor" not in str(briefing)
    with pytest.raises(PermissionError):
        build_owner_briefing(company_id="tenant-a", role="sales_rep",
                             snapshot=_snapshot(result), now=OBSERVED)


@pytest.mark.asyncio
async def test_ar_failure_does_not_invent_zero_or_hide_independent_ap_observation():
    adapter = AsyncMock()
    adapter.get_qbo_finance_document_headers_read_only.side_effect = [
        RuntimeError("sensitive OAuth canary"),
        [{"id": "bill-id", "balance": Decimal("10"), "due_date": TODAY}],
    ]
    result = await read_finance_document_coverage(adapter, as_of=TODAY)
    assert result["ar"]["status"] == "unavailable"
    assert result["ar"]["open_count"] is None
    assert result["ap"]["open_count"] == 1
    assert "sensitive" not in str(result)
    briefing = build_owner_briefing(company_id="tenant-a", role="owner",
                                    snapshot=_snapshot(result), now=OBSERVED)
    assert any(e["status"] == "source_unavailable" for e in briefing["sections"]["yellow_alerts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    None,
    [{"id": "a", "balance": "NaN", "due_date": TODAY}],
    [{"id": "a", "balance": "-1", "due_date": TODAY}],
    [{"id": "a", "balance": "1", "due_date": TODAY},
     {"id": "a", "balance": "2", "due_date": TODAY}],
    [{"id": "a", "balance": "1", "due_date": "2026-11-01"}],
    [{"id": str(i), "balance": "0", "due_date": TODAY} for i in range(5001)],
])
async def test_invalid_or_incomplete_source_fails_closed(rows):
    adapter = AsyncMock()
    adapter.get_qbo_finance_document_headers_read_only.side_effect = [rows, []]
    result = await read_finance_document_coverage(adapter, as_of=TODAY)
    assert result["ar"]["status"] == "unavailable"
    assert result["ar"]["headers_examined"] is None
    assert result["ap"]["status"] == "observed"


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(currency_verified=True),
    lambda v: v.update(ledger_reconciled=True),
    lambda v: v["ar"].update(open_count=999),
    lambda v: v["ap"].update(source_quality="verified"),
    lambda v: v["ar"].update(vendor_name="leak"),
    lambda v: v.update(as_of="not-a-date"),
])
def test_tampered_cached_financial_coverage_rejected(mutation):
    valid = {"as_of": TODAY.isoformat(), "currency_verified": False,
             "ledger_reconciled": False,
             "ar": {"status": "observed", "source_quality": "partial", "reason": None,
                    "headers_examined": 1, "open_count": 1,
                    "overdue_count": 1, "due_date_missing_count": 0},
             "ap": {"status": "observed", "source_quality": "partial", "reason": None,
                    "headers_examined": 0, "open_count": 0,
                    "overdue_count": 0, "due_date_missing_count": 0}}
    mutation(valid)
    with pytest.raises(FinanceDocumentCoverageInvalid):
        validate_finance_document_coverage(valid)
    with pytest.raises(SnapshotDataInvalid):
        validate_dashboard_cached_payload(_snapshot(valid)["payload"])
    with pytest.raises(BriefingSourceUnavailable):
        build_owner_briefing(company_id="tenant-a", role="owner", snapshot=_snapshot(valid), now=OBSERVED)


@pytest.mark.asyncio
async def test_bills_require_financial_fields_and_ignore_paid_documents():
    qb = _qb()
    qb._query = AsyncMock(return_value={"QueryResponse": {"Bill": [
        _bill("1", balance="0"), _bill("2", balance="25", due=""),
    ]}, "time": "2026-11-10T05:00:00Z"})
    rows = await qb.get_bill_headers_read_only()
    assert len(rows) == 2
    assert rows[0]["balance"] == Decimal("0")
    assert rows[1]["due_date"] is None
    for bad in (_bill(balance=None), _bill(balance="NaN"), _bill(due="tomorrow")):
        qb._query = AsyncMock(return_value={"QueryResponse": {"Bill": [bad]},
                                              "time": "2026-11-10T05:00:00Z"})
        with pytest.raises(ValueError):
            await qb.get_bill_headers_read_only()


@pytest.mark.asyncio
async def test_production_or_unknown_source_never_calls_qbo():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._qb = AsyncMock()
    with patch("app.config.get_settings") as settings:
        settings.return_value.intuit_environment = "production"
        with pytest.raises(RuntimeError, match="sandbox"):
            await adapter.get_qbo_finance_document_headers_read_only("ar")
        adapter._qb.get_invoice_headers_read_only.assert_not_called()
