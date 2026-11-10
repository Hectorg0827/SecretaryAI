"""Gate 3: QBO sandbox ledger comparison is read-only and never certified."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.briefing import build_owner_briefing
from app.connectors.qb_online import QBOnlineAdapter
from app.scheduler.dashboard_snapshot import (
    SnapshotDataInvalid, validate_dashboard_cached_payload,
)
from app.scheduler.finance_comparison import (
    FinanceReportInvalid, compare_invoice_headers_to_qbo_income,
    parse_profit_loss_income, validate_finance_comparison,
)

TODAY = date(2026, 11, 10)
START = TODAY - timedelta(days=30)
OBSERVED = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def profit_loss(*, total="100.00", basis="Accrual", start=START, end=TODAY,
                currency="USD", group="Income", columns=None):
    return {
        "Header": {
            "ReportName": "ProfitAndLoss", "ReportBasis": basis,
            "StartPeriod": start.isoformat(), "EndPeriod": end.isoformat(),
            "SummarizeColumnsBy": "Total", "Currency": currency,
        },
        "Rows": {"Row": [{
            "type": "Section", "group": group,
            "Summary": {"ColData": columns or [
                {"value": "Total Income"}, {"value": ""}, {"value": total},
            ]},
        }]},
    }


def snapshot(finance=None):
    payload = {
        "source_quality": "partial", "source_read_status": "completed",
        "accounts": {"healthy": 1, "slowing": 0, "at_risk": 0, "dormant": 0,
                     "unknown": 0, "total": 1},
        "inventory_alerts": [], "inventory_unassessed": 0,
        "sales_30d": 120.00,
    }
    if finance is not None:
        payload["finance_comparison"] = finance
    return payload


def test_profit_loss_parser_requires_exact_provider_basis_period_and_income_section():
    assert parse_profit_loss_income(profit_loss(), date_from=START, date_to=TODAY) == (100, "USD")
    for bad in (
        profit_loss(basis="Cash"), profit_loss(end=TODAY - timedelta(days=1)),
        profit_loss(group="Expenses"), profit_loss(total="NaN"),
        profit_loss(columns=[{"value": "Total Income"}, {"value": "100"}, {"value": "200"}]),
        profit_loss(currency=""),
    ):
        with pytest.raises(FinanceReportInvalid):
            parse_profit_loss_income(bad, date_from=START, date_to=TODAY)
    repeated = profit_loss()
    repeated["Rows"]["Row"].append(repeated["Rows"]["Row"][0])
    with pytest.raises(FinanceReportInvalid):
        parse_profit_loss_income(repeated, date_from=START, date_to=TODAY)


@pytest.mark.asyncio
async def test_comparison_labels_variance_and_alignment_without_certification():
    adapter = SimpleNamespace(get_qbo_profit_loss_read_only=AsyncMock(return_value=profit_loss()))
    comparison = await compare_invoice_headers_to_qbo_income(adapter, "120.00", as_of=TODAY)
    assert comparison["status"] == "variance_detected"
    assert comparison["variance_ledger_minus_invoices"] == "-20.00"
    assert comparison["comparison_is_reconciliation"] is False
    assert validate_finance_comparison(comparison) is comparison
    adapter.get_qbo_profit_loss_read_only.assert_awaited_once_with(START, TODAY)

    aligned = await compare_invoice_headers_to_qbo_income(adapter, "100.00", as_of=TODAY)
    assert aligned["status"] == "aligned_not_certified"
    assert aligned["source_quality"] == "partial"


@pytest.mark.asyncio
async def test_unavailable_qbo_report_fails_open_for_basic_snapshot_but_closed_for_finance():
    adapter = SimpleNamespace(get_qbo_profit_loss_read_only=AsyncMock(side_effect=OSError("SECRET_CANARY")))
    result = await compare_invoice_headers_to_qbo_income(adapter, 0, as_of=TODAY)
    assert result["status"] == "unavailable"
    assert result["source_quality"] == "unavailable"
    assert "ledger_income_total" not in result
    validate_finance_comparison(result)


def test_cached_comparison_rejects_forged_verified_or_unbalanced_amounts():
    base = {
        "source_system": "qbo_profit_loss", "window_start": START.isoformat(),
        "window_end": TODAY.isoformat(), "comparison_is_reconciliation": False,
        "source_quality": "partial", "status": "variance_detected", "currency": "USD",
        "invoice_headers_total": "120.00", "ledger_income_total": "100.00",
        "variance_ledger_minus_invoices": "-20.00",
        "fetched_at": OBSERVED.isoformat(),
    }
    assert validate_dashboard_cached_payload(snapshot(base))
    for mutation in (
        {"comparison_is_reconciliation": True}, {"source_quality": "verified"},
        {"variance_ledger_minus_invoices": "0.00"},
        {"status": "aligned_not_certified"},
        {"currency": ""},
    ):
        with pytest.raises(SnapshotDataInvalid):
            validate_dashboard_cached_payload(snapshot({**base, **mutation}))


def test_briefing_evidence_exposes_variance_but_not_verified_revenue():
    finance = {
        "source_system": "qbo_profit_loss", "window_start": START.isoformat(),
        "window_end": TODAY.isoformat(), "comparison_is_reconciliation": False,
        "source_quality": "partial", "status": "variance_detected", "currency": "USD",
        "invoice_headers_total": "120.00", "ledger_income_total": "100.00",
        "variance_ledger_minus_invoices": "-20.00", "fetched_at": OBSERVED.isoformat(),
    }
    briefing = build_owner_briefing(
        company_id="tenant-a", role="manager",
        snapshot={"payload": snapshot(finance), "generated_at": OBSERVED.isoformat()},
        now=OBSERVED + timedelta(minutes=2),
    )
    assert any("comparison only" in entry["title"] for entry in briefing["sections"]["scoreboard"])
    assert any("-20.00" in entry["title"] for entry in briefing["sections"]["yellow_alerts"])
    assert all(entry["evidence"][0]["quality"] != "verified"
               for entries in briefing["sections"].values() for entry in entries)
    assert briefing["complete_digest"] is False


def test_briefing_marks_missing_ledger_read_unavailable_not_all_clear():
    finance = {
        "source_system": "qbo_profit_loss", "window_start": START.isoformat(),
        "window_end": TODAY.isoformat(), "comparison_is_reconciliation": False,
        "source_quality": "unavailable", "status": "unavailable",
        "reason": "report_read_or_validation_failed",
    }
    briefing = build_owner_briefing(
        company_id="tenant-a", role="owner",
        snapshot={"payload": snapshot(finance), "generated_at": OBSERVED.isoformat()},
        now=OBSERVED + timedelta(minutes=2),
    )
    missing = next(entry for entry in briefing["sections"]["yellow_alerts"]
                   if entry["entry_id"] == "ledger-comparison-unavailable")
    assert missing["status"] == "source_unavailable"
    assert missing["evidence"][0]["confidence"] == 0


@pytest.mark.asyncio
async def test_qbo_profit_loss_is_get_only_with_sandbox_endpoint():
    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return profit_loss()

    class Client:
        def __init__(self, **kwargs):
            self.get = AsyncMock(return_value=Response())
            self.post = AsyncMock(side_effect=AssertionError("No POST allowed"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    client = Client()
    with patch("app.connectors.qb_online.httpx.AsyncClient", return_value=client), \
         patch("app.config.get_settings", return_value=SimpleNamespace(intuit_environment="sandbox")):
        qb = QBOnlineAdapter("client", "secret", "access", "refresh", "realm")
        result = await qb.get_profit_loss_report_read_only(START, TODAY)
    assert result["Header"]["ReportName"] == "ProfitAndLoss"
    client.post.assert_not_awaited()
    url = client.get.await_args.args[0]
    assert url == "https://sandbox-quickbooks.api.intuit.com/v3/company/realm/reports/ProfitAndLoss"
    assert client.get.await_args.kwargs["params"]["accounting_method"] == "Accrual"
