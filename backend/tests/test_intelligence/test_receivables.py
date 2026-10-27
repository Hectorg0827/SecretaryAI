"""Deterministic point-in-time AR fixtures; no vendor, DB or write calls."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.intelligence.receivables import age_invoice_balances


AS_OF = date(2026, 10, 26)
OBSERVED = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
EVALUATED = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


def row(invoice_id="inv-1", balance="100.25", due_date=AS_OF, **updates):
    invoice = {
        "tenant_id": "tenant-a", "source_connection_id": "qbo-sandbox-a",
        "source_invoice_id": invoice_id, "source_ref": f"qbo:invoice:{invoice_id}",
        "customer_id": "cust-a", "date": date(2026, 9, 1),
        "due_date": due_date, "total": "120.25", "balance": balance,
        "status": "open", "currency": "USD",
    }
    invoice.update(updates)
    return invoice


def age(rows, **overrides):
    options = {
        "as_of": AS_OF, "tenant_id": "tenant-a", "source_connection_id": "qbo-sandbox-a",
        "source_as_of": AS_OF, "source_observed_at": OBSERVED,
        "evaluated_at": EVALUATED, "source_complete": True,
        "covers_all_open_invoices": True, "currency_verified": True, "currency": "USD",
    }
    options.update(overrides)
    return age_invoice_balances(rows, **options)


def test_inclusive_ar_bucket_boundaries_and_exact_decimal_total():
    records = [
        row("due-today", "1.11"),
        row("future", "2.22", AS_OF + timedelta(days=1)),
        row("one", "3.33", AS_OF - timedelta(days=1)),
        row("thirty", "4.44", AS_OF - timedelta(days=30)),
        row("thirtyone", "5.55", AS_OF - timedelta(days=31)),
        row("sixty", "6.66", AS_OF - timedelta(days=60)),
        row("sixtyone", "7.77", AS_OF - timedelta(days=61)),
        row("ninety", "8.88", AS_OF - timedelta(days=90)),
        row("ninetyone", "9.99", AS_OF - timedelta(days=91)),
    ]
    for invoice in records:
        invoice["total"] = invoice["balance"]
    report = age(records)
    assert report["status"] == "available_unreconciled"
    assert report["total_open"] == Decimal("49.95")
    assert report["buckets"] == {
        "current": Decimal("3.33"), "days_1_30": Decimal("7.77"),
        "days_31_60": Decimal("12.21"), "days_61_90": Decimal("16.65"),
        "days_91_plus": Decimal("9.99"), "unaged": Decimal("0"),
    }
    assert {p["source_invoice_id"] for p in report["contributions"]} == {
        p["source_invoice_id"] for p in records
    }
    assert report["reconciliation_status"] == "not_reconciled_to_payments_or_gl"


def test_complete_empty_snapshot_proves_zero_only_when_source_scope_verified():
    good = age([])
    assert good["total_open"] == Decimal("0")
    assert good["invoice_count"] == 0
    assert good["status"] == "available_unreconciled"

    missing = age([], source_complete=False)
    assert missing["status"] == "unavailable"
    assert missing["total_open"] is None
    assert missing["buckets"] is None
    assert "source_incomplete" in missing["exceptions"]


@pytest.mark.parametrize("overrides,code", [
    ({"source_complete": False}, "source_incomplete"),
    ({"covers_all_open_invoices": False}, "history_not_exhaustive"),
    ({"currency_verified": False}, "currency_unverified"),
    ({"currency": None}, "currency_unverified"),
    ({"source_as_of": date(2026, 10, 25)}, "snapshot_date_mismatch"),
    ({"source_observed_at": OBSERVED - timedelta(days=2)}, "source_stale"),
])
def test_missing_or_stale_source_returns_no_false_ar_number(overrides, code):
    result = age([row()], **overrides)
    assert result["status"] == "unavailable"
    assert result["total_open"] is None
    assert result["buckets"] is None
    assert code in result["exceptions"]


@pytest.mark.parametrize("updates,error", [
    ({"balance": None}, "balance"),
    ({"balance": "NaN"}, "balance"),
    ({"balance": "Infinity"}, "balance"),
    ({"balance": "-1"}, "balance"),
    ({"balance": "200"}, "exceeds total"),
    ({"total": None}, "total"),
    ({"source_invoice_id": ""}, "source_invoice_id"),
    ({"customer_id": ""}, "customer_id"),
    ({"source_ref": ""}, "source_ref"),
    ({"currency": "EUR"}, "currency"),
    ({"status": "mystery"}, "status"),
    ({"date": AS_OF + timedelta(days=1)}, "future"),
])
def test_bad_invoice_identity_money_dates_and_currency_fail_closed(updates, error):
    with pytest.raises(ValueError, match=error):
        age([row(**updates)])


def test_duplicate_invoice_prevents_double_counting():
    with pytest.raises(ValueError, match="duplicate"):
        age([row("same"), row("same")])


@pytest.mark.parametrize("updates", [
    {"tenant_id": "tenant-b"},
    {"tenant_id": None},
    {"source_connection_id": "different-source"},
])
def test_wrong_or_missing_tenant_or_connection_denied(updates):
    with pytest.raises(PermissionError):
        age([row(**updates)])


def test_missing_due_date_is_visible_unaged_not_current():
    report = age([row(due_date=None)])
    assert report["status"] == "partial_aging"
    assert report["total_open"] == Decimal("100.25")
    assert report["buckets"]["current"] == Decimal("0")
    assert report["buckets"]["unaged"] == Decimal("100.25")
    assert "missing_due_date" in report["exceptions"]


def test_void_draft_and_paid_do_not_count_as_open():
    records = [
        row("void", "0", status="void"),
        row("draft", "0", status="draft"),
        row("paid", "0", status="paid"),
        row("open", "0", status="open"),
    ]
    report = age(records)
    assert report["total_open"] == Decimal("0")
    assert report["invoice_count"] == 0
    assert report["excluded_count"] == 3


def test_conflicting_paid_or_void_status_with_open_balance_rejected():
    with pytest.raises(ValueError, match="status"):
        age([row("paid", status="paid")])
    with pytest.raises(ValueError, match="status"):
        age([row("void", status="void")])


def test_naive_or_future_source_timestamps_rejected():
    with pytest.raises(ValueError, match="timezone"):
        age([row()], source_observed_at=datetime(2026, 10, 26, 12))
    with pytest.raises(ValueError, match="future"):
        age([row()], source_observed_at=EVALUATED + timedelta(seconds=1))
