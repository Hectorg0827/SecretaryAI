"""Exact money, multi-currency and envelope contract rules."""
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.lola_bi.contracts import (
    Finding, SourceQuality, SourceSnapshot, SourceStatus, SourceUnavailable, TenantMismatch, jsonable,
)
from app.lola_bi.money import CurrencyContext, FxRate, MoneyTotals, UNKNOWN_CURRENCY, to_money

from .synthetic import NOW, TENANT


def test_money_parsing_is_exact_and_rejects_floats_and_missing():
    assert to_money("0.10") + to_money("0.20") == D("0.30")
    for bad in (None, "", 0.1, True, "NaN", "Infinity", "abc"):
        with pytest.raises(ValueError):
            to_money(bad)
    assert to_money("0") == D("0")  # a verified zero is still a value
    with pytest.raises(ValueError):
        to_money("-1", signed=False)


def test_totals_never_mix_currencies_without_rates():
    totals = MoneyTotals()
    totals.add("USD", D("100.10"))
    totals.add("USD", D("0.20"))
    totals.add("CAD", D("50.00"))
    view = totals.describe()
    assert view["by_currency"] == {"CAD": D("50.00"), "USD": D("100.30")}
    assert view["total"] is None and view["total_unavailable_reason"] == "mixed_currencies"


def test_conversion_requires_sourced_rate_for_every_currency():
    totals = MoneyTotals()
    totals.add("USD", D("100.00"))
    totals.add("CAD", D("50.00"))
    totals.add("EUR", D("10.00"))
    rate = FxRate("CAD", "USD", D("0.73"), "test-fixture-rate", NOW)
    converted, used, missing = totals.convert("USD", [rate])
    assert converted is None and missing == ["EUR"]
    converted, used, missing = totals.convert(
        "USD", [rate, FxRate("EUR", "USD", D("1.10"), "test-fixture-rate", NOW)])
    assert converted == D("100.00") + D("50.00") * D("0.73") + D("10.00") * D("1.10")
    assert {u["base"] for u in used} == {"CAD", "EUR"} and missing == []


@pytest.mark.parametrize("kwargs", [
    {"rate": D("0")}, {"rate": D("-1")}, {"source": ""}, {"as_of": datetime(2026, 1, 1)}, {"base": "usd"},
])
def test_fx_rates_must_be_positive_sourced_and_timestamped(kwargs):
    base = dict(base="CAD", quote="USD", rate=D("0.7"), source="x", as_of=NOW)
    base.update(kwargs)
    with pytest.raises(ValueError):
        FxRate(**base)


def test_unknown_currency_blocks_a_combined_total():
    totals = MoneyTotals()
    totals.add("USD", D("1"))
    totals.add(None, D("1"))  # type: ignore[arg-type]
    assert totals.single() == (None, None, "unknown_currency_present")
    assert UNKNOWN_CURRENCY in totals.by_currency()


def test_absent_record_currency_is_home_only_in_single_currency_books():
    assert CurrencyContext("USD", False, "qbo").resolve(None) == "USD"
    assert CurrencyContext("USD", True, "qbo").resolve(None) == UNKNOWN_CURRENCY
    assert CurrencyContext(None, None, "unknown").resolve(None) == UNKNOWN_CURRENCY
    assert CurrencyContext("USD", True, "qbo").resolve("CAD") == "CAD"
    assert CurrencyContext("USD", True, "qbo").resolve("bogus") == UNKNOWN_CURRENCY


def test_failed_snapshot_cannot_carry_records_and_is_never_usable():
    snap = SourceSnapshot.failed(TENANT, "qb.open_bills", "quickbooks_online", SourceStatus.FAILED, "x")
    assert not snap.usable
    with pytest.raises(SourceUnavailable):
        snap.require(NOW)
    with pytest.raises(ValueError):
        SourceSnapshot(tenant_id=TENANT, source="s", provider="p", status=SourceStatus.FAILED, records=({"a": 1},))
    with pytest.raises(TenantMismatch):
        SourceSnapshot(tenant_id="", source="s", provider="p", status=SourceStatus.FAILED)


def test_stale_snapshot_is_refused_not_presented_as_current():
    snap = SourceSnapshot(tenant_id=TENANT, source="qb.bank_accounts", provider="p", status=SourceStatus.OK,
                          records=({"balance": D("1")},), fetched_at=NOW - timedelta(hours=3),
                          max_age=timedelta(hours=1), quality=SourceQuality.PROVIDER_OBSERVED)
    assert snap.is_stale(NOW)
    with pytest.raises(SourceUnavailable) as exc:
        snap.require(NOW)
    assert exc.value.status == "stale"
    future = SourceSnapshot(tenant_id=TENANT, source="s", provider="p", status=SourceStatus.OK,
                            fetched_at=NOW + timedelta(hours=1))
    assert future.is_stale(NOW)  # implausible future timestamps are not fresh


def test_jsonable_keeps_decimals_exact():
    out = jsonable({"a": D("1234.5600"), "d": date(2026, 1, 2), "f": Finding("x", "verified")})
    assert out == {"a": "1234.5600", "d": "2026-01-02",
                   "f": {"statement": "x", "basis": "verified", "evidence": {}, "sources": []}}
    with pytest.raises(ValueError):
        Finding("x", "probably")
