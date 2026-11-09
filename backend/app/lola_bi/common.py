"""Shared deterministic helpers for Lola BI department engines."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from app.lola_bi.contracts import ResultBuilder, SourceSnapshot
from app.lola_bi.money import CurrencyContext, MoneyTotals, UNKNOWN_CURRENCY

NOT_BILLED = frozenset({"void", "voided", "cancelled", "canceled", "deleted", "draft"})
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("Date window starts after it ends")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def describe(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat(), "days": self.days}


def trailing_windows(as_of: date, days: int) -> tuple[Window, Window]:
    """(current, prior) equal non-overlapping windows ending on ``as_of``."""
    if days < 7 or days > 400:
        raise ValueError("Comparison window must be between 7 and 400 days")
    current = Window(as_of - timedelta(days=days - 1), as_of)
    prior = Window(current.start - timedelta(days=days), current.start - timedelta(days=1))
    return current, prior


def shift_year(day: date, years: int) -> date:
    year = day.year + years
    last = calendar.monthrange(year, day.month)[1]
    return date(year, day.month, min(day.day, last))


def month_to_date(as_of: date) -> Window:
    return Window(as_of.replace(day=1), as_of)


def pct_change(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    """Percentage change; None when the base is zero/unknown (not 'infinite growth')."""
    if current is None or prior is None or prior == 0:
        return None
    return ((current - prior) / abs(prior) * HUNDRED).quantize(Decimal("0.1"))


async def currency_context(gw, rb: ResultBuilder) -> CurrencyContext:
    rows = rb.optional(await gw.company_currency())
    if not rows:
        return CurrencyContext(None, None, "unknown")
    row = rows[0]
    return CurrencyContext(row.get("home_currency"), row.get("multicurrency_enabled"),
                           row.get("source") or "unknown")


def with_currency(rows: Iterable[Mapping], ctx: CurrencyContext) -> list[dict]:
    return [{**row, "currency": ctx.resolve(row.get("currency"))} for row in rows]


def note_currency(rb: ResultBuilder, ctx: CurrencyContext, rows: Sequence[Mapping]) -> None:
    if any(r.get("currency") == UNKNOWN_CURRENCY for r in rows):
        rb.note_missing("currency", "unverified",
                        "some records have no verified currency; they are reported separately")


def dedupe(rows: Iterable[Mapping], *, key: str = "id", label: str = "record",
           rb: ResultBuilder | None = None) -> list[dict]:
    """Collapse identical repeats; conflicting repeats of one ID are a data error."""
    seen: dict[str, dict] = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident.strip():
            raise ValueError(f"{label} missing {key}")
        if ident in seen:
            if seen[ident] != dict(row):
                raise ValueError(f"Conflicting duplicate {label} {key}")
            if rb is not None:
                rb.warn(f"duplicate {label} records were collapsed")
            continue
        seen[ident] = dict(row)
    return list(seen.values())


def billable(invoices: Iterable[Mapping]) -> list[dict]:
    return [dict(i) for i in invoices if str(i.get("status") or "").strip().lower() not in NOT_BILLED]


def billed_totals(invoices: Iterable[Mapping]) -> MoneyTotals:
    totals = MoneyTotals()
    for inv in invoices:
        totals.add(inv["currency"], inv["total"])
    return totals


def invoices_in(records: Iterable[Mapping], window: Window) -> list[dict]:
    return [dict(r) for r in records if window.contains(r["date"])]


async def invoices_for(gw, rb: ResultBuilder, window: Window, ctx: CurrencyContext,
                       *, required: bool = True) -> list[dict] | None:
    snap: SourceSnapshot = await gw.invoices(window.start, window.end)
    rows = rb.use(snap) if required else rb.optional(snap)
    if rows is None:
        return None
    rows = dedupe(rows, label="invoice", rb=rb)
    return billable(with_currency(rows, ctx))


def money_view(totals: MoneyTotals) -> dict:
    return totals.describe()


def single_or_none(totals: MoneyTotals) -> tuple[str | None, Decimal | None]:
    code, amount, _ = totals.single()
    return code, amount


def compare_totals(current: MoneyTotals, prior: MoneyTotals) -> dict:
    """Compare two periods. A single change figure exists only within one currency;
    ``by_currency_change`` always compares like with like."""
    per_currency = {
        code: {"current": current.get(code), "prior": prior.get(code),
               "change_amount": current.get(code) - prior.get(code),
               "change_pct": pct_change(current.get(code), prior.get(code))}
        for code in sorted(set(current.currencies) | set(prior.currencies)) if code != UNKNOWN_CURRENCY
    }
    base = {"current": current.describe(), "prior": prior.describe(), "by_currency_change": per_currency}
    cur_code, cur = single_or_none(current)
    pri_code, pri = single_or_none(prior)
    if not current.currencies and not prior.currencies:
        return {**base, "change_pct": None, "change_amount": Decimal("0"), "comparable": True}
    if (cur_code and pri_code and cur_code != pri_code) or (cur is None and current.currencies) \
            or (pri is None and prior.currencies):
        return {**base, "change_pct": None, "change_amount": None, "comparable": False}
    cur = cur if cur is not None else Decimal("0")
    pri = pri if pri is not None else Decimal("0")
    return {**base, "change_amount": cur - pri, "change_pct": pct_change(cur, pri), "comparable": True}
