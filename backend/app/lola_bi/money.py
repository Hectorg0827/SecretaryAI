"""Exact, currency-aware money arithmetic for Lola BI.

Rules
-----
* Amounts are ``Decimal``; floats are rejected so rounding never hides cents.
* Different currencies are never added together. A single total exists only
  when every amount shares one verified currency, or when an explicit FX rate
  (with its source and timestamp) is supplied for every foreign currency.
* An unknown currency is kept in its own ``UNKNOWN`` bucket and blocks a
  combined total rather than being assumed to be the home currency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Iterable, Mapping

UNKNOWN_CURRENCY = "UNKNOWN"
CENT = Decimal("0.01")


def is_currency_code(value: object) -> bool:
    return isinstance(value, str) and len(value) == 3 and value.isalpha() and value.isupper()


def to_money(value: object, label: str = "amount", *, signed: bool = True) -> Decimal:
    """Parse a source amount exactly; missing, float, bool or non-finite fail."""
    if value is None or value == "" or isinstance(value, (bool, float)):
        raise ValueError(f"{label} is missing or not an exact decimal")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is missing or invalid") from exc
    if not amount.is_finite():
        raise ValueError(f"{label} is not finite")
    if not signed and amount < 0:
        raise ValueError(f"{label} must not be negative")
    return amount


def round_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class CurrencyContext:
    """What we know about a tenant's currencies, and how we know it."""

    home_currency: str | None
    multicurrency_enabled: bool | None
    source: str  # e.g. "qbo_preferences", "tenant_settings", "unknown"

    def resolve(self, record_currency: object) -> str:
        """Currency of a record; an absent code is home only in single-currency books."""
        if is_currency_code(record_currency):
            return record_currency  # type: ignore[return-value]
        if record_currency not in (None, ""):
            return UNKNOWN_CURRENCY
        if self.multicurrency_enabled is False and is_currency_code(self.home_currency):
            return self.home_currency  # type: ignore[return-value]
        return UNKNOWN_CURRENCY


@dataclass(frozen=True)
class FxRate:
    """1 unit of ``base`` = ``rate`` units of ``quote``, observed at ``as_of``."""

    base: str
    quote: str
    rate: Decimal
    source: str
    as_of: datetime

    def __post_init__(self) -> None:
        if not (is_currency_code(self.base) and is_currency_code(self.quote)):
            raise ValueError("FX rate currencies must be ISO codes")
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite() or self.rate <= 0:
            raise ValueError("FX rate must be a positive Decimal")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("FX rate requires a named source")
        if not isinstance(self.as_of, datetime) or self.as_of.tzinfo is None:
            raise ValueError("FX rate requires a timezone-aware as_of")


class MoneyTotals:
    """Per-currency sums. Never silently collapses currencies."""

    def __init__(self) -> None:
        self._by: dict[str, Decimal] = {}

    def add(self, currency: str, amount: Decimal) -> None:
        if not isinstance(amount, Decimal) or not amount.is_finite():
            raise ValueError("MoneyTotals accepts finite Decimals only")
        code = currency if is_currency_code(currency) else UNKNOWN_CURRENCY
        self._by[code] = self._by.get(code, Decimal("0")) + amount

    def merge(self, other: "MoneyTotals", *, sign: int = 1) -> "MoneyTotals":
        result = MoneyTotals()
        for code, amount in self._by.items():
            result.add(code, amount)
        for code, amount in other._by.items():
            result.add(code, amount * sign)
        return result

    @property
    def currencies(self) -> list[str]:
        return sorted(self._by)

    def get(self, currency: str) -> Decimal:
        return self._by.get(currency, Decimal("0"))

    def by_currency(self) -> dict[str, Decimal]:
        return {code: self._by[code] for code in sorted(self._by)}

    def single(self) -> tuple[str | None, Decimal | None, str | None]:
        """(currency, total, reason-if-none). Empty totals have no currency."""
        if not self._by:
            return None, None, "no_amounts"
        if UNKNOWN_CURRENCY in self._by:
            return None, None, "unknown_currency_present"
        if len(self._by) > 1:
            return None, None, "mixed_currencies"
        code = next(iter(self._by))
        return code, self._by[code], None

    def convert(self, target: str, rates: Iterable[FxRate]) -> tuple[Decimal | None, list[dict], list[str]]:
        """Convert to ``target`` using only supplied rates; report what was used/missing."""
        if not is_currency_code(target):
            raise ValueError("Target currency must be an ISO code")
        table: dict[str, FxRate] = {}
        for rate in rates:
            if rate.quote == target:
                table[rate.base] = rate
        used: list[dict] = []
        missing: list[str] = []
        total = Decimal("0")
        for code, amount in self.by_currency().items():
            if code == target:
                total += amount
                continue
            rate = table.get(code)
            if rate is None:
                missing.append(code)
                continue
            total += amount * rate.rate
            used.append({"base": code, "quote": target, "rate": rate.rate,
                         "source": rate.source, "as_of": rate.as_of})
        if missing:
            return None, used, missing
        return total, used, []

    def describe(self, rates: Iterable[FxRate] = (), target: str | None = None) -> dict:
        currency, total, reason = self.single()
        out: dict = {"by_currency": self.by_currency(), "total": total,
                     "currency": currency, "total_unavailable_reason": reason}
        if total is None and target and reason != "no_amounts":
            converted, used, missing = self.convert(target, rates)
            out.update({
                "converted_total": converted, "converted_currency": target if converted is not None else None,
                "fx_rates_used": used, "fx_missing_for": missing,
            })
        return out


def sum_by_currency(rows: Iterable[Mapping], amount_key: str, currency_key: str = "currency") -> MoneyTotals:
    totals = MoneyTotals()
    for row in rows:
        totals.add(row.get(currency_key) or UNKNOWN_CURRENCY, row[amount_key])
    return totals
