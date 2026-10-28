"""Per-product sales aggregation shared by the growth insight endpoints.

Reads the tenant's connected QuickBooks inventory and invoices and turns them
into per-product period totals. It never invents a value: a product without a
usable stock or cost reading carries ``None`` for it, and invoices or lines
that cannot be interpreted are skipped and counted rather than failing the
whole analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

from app.api.insights._common import read_source, row_value
from app.intelligence.brands.scorecards import BrandPeriod, BrandVitals, brand_vitality

ZERO = Decimal("0")
# Billing states that never represent a sale (mirrors sales_analytics).
_EXCLUDED_STATUSES = frozenset({"void", "voided", "cancelled", "canceled", "deleted", "draft", "refunded"})
_QUALITY = frozenset({"verified", "partial", "unavailable"})


def to_decimal(value: Any) -> Decimal | None:
    """A finite Decimal, or None for missing, boolean or non-numeric values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def from_input(value: float) -> Decimal:
    """Exact Decimal for a validated, finite request number (0.1 -> 0.1)."""
    return Decimal(str(value))


def money(value: Decimal | None, places: int = 2) -> float | None:
    """Round a Decimal for the JSON response; None stays None."""
    if value is None:
        return None
    return float(round(value, places))


@dataclass(frozen=True)
class Product:
    item_id: str
    name: str
    sku: str
    stock: Decimal | None        # None: no usable on-hand reading
    unit_cost: Decimal | None    # None: QuickBooks purchase cost missing or zero
    list_price: Decimal | None
    stock_quality: str           # adapter's own quality flag for the quantity


@dataclass
class ProductPeriod:
    cases_sold: Decimal = ZERO
    cases_returned: Decimal = ZERO
    net_sales: Decimal = ZERO
    invoices_by_customer: dict[str, set[str]] = field(default_factory=dict)

    @property
    def net_cases(self) -> Decimal:
        return self.cases_sold - self.cases_returned

    @property
    def invoice_count(self) -> int:
        return len({inv for invoices in self.invoices_by_customer.values() for inv in invoices})


@dataclass
class Period:
    start: date
    end: date
    products: dict[str, ProductPeriod] = field(default_factory=dict)
    active_customers: set[str] = field(default_factory=set)

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass
class SalesWindow:
    current: Period
    previous: Period | None
    rows_skipped: int = 0
    lines_not_in_catalog: int = 0


def periods(today: date, weeks: int, *, with_previous: bool) -> tuple[Period, Period | None]:
    """Equal, non-overlapping windows ending today, newest first."""
    days = weeks * 7
    current = Period(start=today - timedelta(days=days - 1), end=today)
    previous = None
    if with_previous:
        previous = Period(start=current.start - timedelta(days=days), end=current.start - timedelta(days=1))
    return current, previous


def load_catalog(rows: list) -> tuple[dict[str, Product], int]:
    """Index merged inventory rows by QuickBooks item id; bad rows are counted."""
    catalog: dict[str, Product] = {}
    skipped = 0
    for row in rows:
        item_id = row_value(row, "qb_id")
        if not isinstance(item_id, str) or not item_id.strip() or item_id.strip() in catalog:
            skipped += 1
            continue
        stock = to_decimal(row_value(row, "total_qty"))
        cost = to_decimal(row_value(row, "purchase_cost"))
        price = to_decimal(row_value(row, "unit_price"))
        quality = row_value(row, "source_quality")
        catalog[item_id.strip()] = Product(
            item_id=item_id.strip(),
            name=str(row_value(row, "product_name") or ""),
            sku=str(row_value(row, "sku") or ""),
            # Negative on-hand is an unreconciled count, not a quantity.
            stock=stock if stock is not None and stock >= 0 else None,
            unit_cost=cost if cost is not None and cost > 0 else None,
            list_price=price if price is not None and price > 0 else None,
            stock_quality=quality if quality in _QUALITY else "unavailable",
        )
    return catalog, skipped


def _invoice_date(raw: Any) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _matcher(catalog: dict[str, Product]):
    by_sku = {p.sku.strip().casefold(): p.item_id for p in catalog.values() if p.sku.strip()}
    by_name = {p.name.strip().casefold(): p.item_id for p in catalog.values() if p.name.strip()}

    def match(line: dict) -> str | None:
        item_id = line.get("item_id")
        if isinstance(item_id, str) and item_id.strip() in catalog:
            return item_id.strip()
        sku = str(line.get("sku") or "").strip().casefold()
        if sku and sku in by_sku:
            return by_sku[sku]
        name = str(line.get("name") or "").strip().casefold()
        return by_name.get(name) if name else None

    return match


def aggregate_sales(
    invoices: list, catalog: dict[str, Product], current: Period, previous: Period | None,
) -> SalesWindow:
    """Sum billed invoice lines per catalog product for each period."""
    window = SalesWindow(current=current, previous=previous)
    match = _matcher(catalog)
    seen: set[str] = set()
    for invoice in invoices:
        source_id = row_value(invoice, "qb_id") or row_value(invoice, "id")
        customer = row_value(invoice, "customer_id")
        day = _invoice_date(row_value(invoice, "date"))
        lines = row_value(invoice, "line_items")
        if (
            not isinstance(source_id, str) or not source_id.strip()
            or not isinstance(customer, str) or not customer.strip()
            or day is None or not isinstance(lines, list)
        ):
            window.rows_skipped += 1
            continue
        if source_id in seen:
            # A repeated provider identity would double count; keep the first.
            window.rows_skipped += 1
            continue
        seen.add(source_id)
        if str(row_value(invoice, "status") or "").strip().casefold() in _EXCLUDED_STATUSES:
            continue
        period = current if current.contains(day) else previous if previous and previous.contains(day) else None
        if period is None:
            continue
        period.active_customers.add(customer)
        for line in lines:
            if not isinstance(line, dict):
                window.rows_skipped += 1
                continue
            item_id = match(line)
            if item_id is None:
                window.lines_not_in_catalog += 1
                continue
            quantity = to_decimal(line.get("quantity"))
            amount = to_decimal(line.get("amount"))
            if quantity is None or amount is None:
                window.rows_skipped += 1
                continue
            totals = period.products.setdefault(item_id, ProductPeriod())
            if quantity > 0:
                totals.cases_sold += quantity
            elif quantity < 0:
                totals.cases_returned += -quantity
            totals.net_sales += amount
            totals.invoices_by_customer.setdefault(customer, set()).add(source_id)
    return window


async def read_sales(adapter, *, days: int) -> tuple[list, list]:
    """Read inventory and invoices; any failure or malformed payload is a 503."""
    inventory = await read_source(adapter.get_inventory_merged(), source_name="Inventory data")
    invoices = await read_source(adapter.get_orders_last_n_days(days), source_name="Sales history")
    if not isinstance(inventory, list) or not isinstance(invoices, list):
        raise HTTPException(status_code=503, detail="Connected data is unavailable; no analysis was produced")
    return inventory, invoices


def _brand_period(company_id: str, product: Product, period: Period, totals: ProductPeriod, weeks: int) -> BrandPeriod:
    net_cases = totals.net_cases
    gross_profit = totals.net_sales - net_cases * product.unit_cost if product.unit_cost is not None else ZERO
    return BrandPeriod(
        company_id=company_id,
        brand_id=product.item_id,
        period_start=period.start,
        period_end=period.end,
        cases_sold=totals.cases_sold,
        cases_returned=totals.cases_returned,
        active_accounts=len(totals.invoices_by_customer),
        eligible_accounts=len(period.active_customers),
        repeat_accounts=sum(1 for invoices in totals.invoices_by_customer.values() if len(invoices) > 1),
        net_sales=totals.net_sales,
        gross_profit=gross_profit,
        stock_on_hand_cases=product.stock if product.stock is not None else ZERO,
        weekly_case_velocity=net_cases / weeks if net_cases > 0 else ZERO,
        source_record_id=f"quickbooks:item:{product.item_id}",
        source_revision=period.label(),
    )


def product_vitals(company_id: str, product: Product, window: SalesWindow, weeks: int) -> BrandVitals:
    """Brand vitality for one product; raises ValueError for inconsistent data.

    QuickBooks does not record brands, so each product is scored on its own.
    Missing cost or stock readings are reported as gaps instead of numbers.
    """
    current = _brand_period(company_id, product, window.current,
                            window.current.products.get(product.item_id, ProductPeriod()), weeks)
    previous = None
    if window.previous is not None:
        previous = _brand_period(company_id, product, window.previous,
                                 window.previous.products.get(product.item_id, ProductPeriod()), weeks)
    vitals = brand_vitality(current, previous)
    gaps = list(vitals.gaps)
    overrides: dict[str, Any] = {}
    if product.unit_cost is None:
        overrides["gross_margin_pct"] = None
        gaps.append("unavailable_product_cost")
    if product.stock is None:
        overrides["weeks_of_supply"] = None
        gaps.append("unavailable_stock_reading")
    if not overrides:
        return vitals
    return replace(vitals, gaps=tuple(gaps), **overrides)
