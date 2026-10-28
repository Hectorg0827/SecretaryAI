"""Connected-data readers shared by the supply-chain insight endpoints.

Tenant scope always comes from the caller (``user["company_id"]``). Readers
raise on a failed or malformed source read, so endpoints wrap them in
``read_source`` and answer 503 instead of analysing partial data. Individual
bad rows are skipped and counted; a missing observation is never filled in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from app.api.insights._common import row_value

CLOSED_INVOICE_STATUSES = frozenset({"void", "voided", "cancelled", "canceled", "refunded"})
OPEN_PO_STATUSES = frozenset({"approved", "partially_received"})
# Most recent purchase orders read from the purchasing ledger per request.
MAX_LEDGER_ORDERS = 200
# Outstanding orders count toward stock position however old they are.
MAX_OPEN_ORDERS = 2000
# Keeps each PostgREST ``in`` filter well inside URL limits.
_IN_CHUNK = 100
MAX_LEDGER_CHILD_ROWS = 5000
# A received PO taking longer than this is a data error, not a lead time.
MAX_LEAD_TIME_DAYS = 730


def finite_number(value: Any) -> Optional[float]:
    """A finite float, or None for a missing or invalid value."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return number if math.isfinite(number) else None


def as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def norm(value: Any) -> str:
    return str(value or "").strip().casefold()


# ── Invoices (QuickBooks via the adapter) ─────────────────────────────────────


@dataclass
class CleanInvoice:
    customer_id: str
    customer_name: str
    day: date
    total: float
    lines: list


def clean_invoices(invoices: Any, *, today: date, days: int) -> tuple[list[CleanInvoice], int]:
    """Keep open/paid invoices dated inside the window; count unreadable ones."""
    if not isinstance(invoices, list):
        raise ValueError("Invoice source returned no list")
    kept: list[CleanInvoice] = []
    skipped = 0
    for invoice in invoices:
        if invoice is None:
            skipped += 1
            continue
        if norm(row_value(invoice, "status", "")) in CLOSED_INVOICE_STATUSES:
            continue
        day = as_date(row_value(invoice, "date", row_value(invoice, "order_date")))
        total = finite_number(row_value(invoice, "total"))
        lines = row_value(invoice, "line_items", row_value(invoice, "items", []))
        if day is None or total is None or not isinstance(lines, list):
            skipped += 1
            continue
        age = (today - day).days
        if age < 0 or age >= days:
            continue
        kept.append(CleanInvoice(
            customer_id=str(row_value(invoice, "customer_id", "") or ""),
            customer_name=str(row_value(invoice, "customer_name", "") or ""),
            day=day, total=total, lines=lines,
        ))
    return kept, skipped


def line_unit_price(line: dict, quantity: float) -> Optional[float]:
    """Price actually charged on a sale line: unit price, else amount / qty."""
    price = finite_number(line.get("unit_price"))
    if price is not None and price > 0:
        return price
    amount = finite_number(line.get("amount"))
    if amount is not None and amount > 0 and quantity > 0:
        return amount / quantity
    return None


# ── Matching invoice lines to inventory items ─────────────────────────────────


class ItemIndex:
    """Match sale lines to merged inventory rows by item id, SKU, then name."""

    def __init__(self, inventory: list):
        self.by_id: dict[str, int] = {}
        self.by_sku: dict[str, int] = {}
        self.by_name: dict[str, int] = {}
        for index, item in enumerate(inventory):
            if not isinstance(item, dict):
                continue
            for key, table in ((norm(item.get("qb_id")), self.by_id),
                               (norm(item.get("sku")), self.by_sku),
                               (norm(item.get("product_name")), self.by_name)):
                if key:
                    table.setdefault(key, index)

    def match(self, line: dict) -> Optional[int]:
        for key, table in ((norm(line.get("item_id")), self.by_id),
                           (norm(line.get("sku")), self.by_sku),
                           (norm(line.get("name")), self.by_name)):
            if key and key in table:
                return table[key]
        return None


@dataclass
class SaleLine:
    day: date
    quantity: float
    unit_price: Optional[float]


def sales_by_item(invoices: list[CleanInvoice], index: ItemIndex) -> tuple[dict[int, list[SaleLine]], int]:
    """Positive-quantity sale lines per inventory row; credit lines are ignored."""
    sales: dict[int, list[SaleLine]] = {}
    skipped = 0
    for invoice in invoices:
        for line in invoice.lines:
            if not isinstance(line, dict):
                skipped += 1
                continue
            quantity = finite_number(line.get("quantity"))
            if quantity is None:
                skipped += 1
                continue
            if quantity <= 0:
                continue
            item = index.match(line)
            if item is None:
                continue
            sales.setdefault(item, []).append(
                SaleLine(invoice.day, quantity, line_unit_price(line, quantity))
            )
    return sales, skipped


def daily_series(lines: list[SaleLine], *, today: date, days: int) -> list[float]:
    """Units sold per day, oldest first, over a window the invoice read covers."""
    series = [0.0] * days
    for line in lines:
        age = (today - line.day).days
        if 0 <= age < days:
            series[days - 1 - age] += line.quantity
    return series


def inventory_list(inventory: Any) -> list:
    if not isinstance(inventory, list):
        raise ValueError("Inventory source returned no list")
    return inventory


# ── SecretaryAI purchasing ledger (database) ──────────────────────────────────


@dataclass
class PurchasingHistory:
    # SKU keys are normalised with ``norm``.
    lead_times_by_sku: dict[str, list[float]] = field(default_factory=dict)
    open_units_by_sku: dict[str, float] = field(default_factory=dict)
    open_order_ages_by_sku: dict[str, list[float]] = field(default_factory=dict)
    supplier_lead_times: list[float] = field(default_factory=list)
    orders_read: int = 0
    rows_skipped: int = 0


def _rows(query) -> list:
    data = getattr(query.execute(), "data", None)
    if not isinstance(data, list):
        raise ValueError("Purchasing query returned no row list")
    return data


def _rows_by_order(make_query, ids: list[str]) -> list:
    rows: list = []
    for start in range(0, len(ids), _IN_CHUNK):
        rows.extend(_rows(make_query(ids[start:start + _IN_CHUNK])))
        if len(rows) > MAX_LEDGER_CHILD_ROWS:
            raise ValueError("Purchasing records exceed the supported page")
    return rows


def _lead_days(approved: Optional[datetime], received: Optional[datetime]) -> Optional[float]:
    if approved is None or received is None:
        return None
    days = (received - approved).total_seconds() / 86400
    return days if 0 < days <= MAX_LEAD_TIME_DAYS else None


async def read_purchasing_history(db, company_id: str, *, now: Optional[datetime] = None) -> PurchasingHistory:
    """Observed supplier lead times (approval to first posted receipt) and open PO units."""
    now = now or datetime.now(timezone.utc)
    history = PurchasingHistory()
    recent = _rows(
        db.table("purchasing_orders")
          .select("id,status,approved_at")
          .eq("company_id", company_id)
          .order("created_at", desc=True)
          .limit(MAX_LEDGER_ORDERS)
    )
    outstanding = _rows(
        db.table("purchasing_orders")
          .select("id,status,approved_at")
          .eq("company_id", company_id)
          .in_("status", sorted(OPEN_PO_STATUSES))
          .limit(MAX_OPEN_ORDERS + 1)
    )
    if len(outstanding) > MAX_OPEN_ORDERS:
        raise ValueError("Outstanding purchase orders exceed the supported page")
    by_id: dict[str, dict] = {}
    for order in [*recent, *outstanding]:
        if isinstance(order, dict) and order.get("id"):
            by_id[str(order["id"])] = order
        else:
            history.rows_skipped += 1
    history.orders_read = len(by_id)
    if not by_id:
        return history
    ids = list(by_id)
    lines = _rows_by_order(lambda chunk: (
        db.table("purchasing_order_lines")
          .select("po_id,sku,qty_ordered,qty_received")
          .eq("company_id", company_id)
          .in_("po_id", chunk)
          .limit(MAX_LEDGER_CHILD_ROWS + 1)
    ), ids)
    receipts = _rows_by_order(lambda chunk: (
        db.table("warehouse_receipt_documents")
          .select("po_id,posted_at,payload")
          .eq("company_id", company_id)
          .in_("po_id", chunk)
          .limit(MAX_LEDGER_CHILD_ROWS + 1)
    ), ids)

    # A PO's first delivery gives the supplier lead time; each SKU's own first
    # delivery gives that SKU's lead time (split deliveries arrive at different times).
    first_receipt: dict[str, datetime] = {}
    first_sku_receipt: dict[tuple[str, str], datetime] = {}
    for receipt in receipts:
        posted = as_datetime(receipt.get("posted_at")) if isinstance(receipt, dict) else None
        po_id = str(receipt.get("po_id")) if isinstance(receipt, dict) else ""
        if posted is None or po_id not in by_id:
            history.rows_skipped += 1
            continue
        if po_id not in first_receipt or posted < first_receipt[po_id]:
            first_receipt[po_id] = posted
        payload = receipt.get("payload")
        skus = {norm(line.get("sku")) for line in payload if isinstance(line, dict)} \
            if isinstance(payload, list) else set()
        skus.discard("")
        if not skus:
            history.rows_skipped += 1
            continue
        for sku in skus:
            key = (po_id, sku)
            if key not in first_sku_receipt or posted < first_sku_receipt[key]:
                first_sku_receipt[key] = posted

    for po_id, received_at in first_receipt.items():
        lead_days = _lead_days(as_datetime(by_id[po_id].get("approved_at")), received_at)
        if lead_days is None:
            history.rows_skipped += 1
            continue
        history.supplier_lead_times.append(lead_days)

    for line in lines:
        if not isinstance(line, dict):
            history.rows_skipped += 1
            continue
        po_id = str(line.get("po_id"))
        sku = norm(line.get("sku"))
        ordered = finite_number(line.get("qty_ordered"))
        received = finite_number(line.get("qty_received"))
        if po_id not in by_id or not sku or ordered is None or received is None \
                or ordered <= 0 or not 0 <= received <= ordered:
            history.rows_skipped += 1
            continue
        order = by_id[po_id]
        approved = as_datetime(order.get("approved_at"))
        if received > 0:
            lead_days = _lead_days(approved, first_sku_receipt.get((po_id, sku)))
            if lead_days is not None:
                history.lead_times_by_sku.setdefault(sku, []).append(lead_days)
        if norm(order.get("status")) in OPEN_PO_STATUSES and ordered > received:
            history.open_units_by_sku[sku] = history.open_units_by_sku.get(sku, 0.0) + ordered - received
            if approved is not None and approved <= now:
                history.open_order_ages_by_sku.setdefault(sku, []).append(
                    (now - approved).total_seconds() / 86400
                )
    return history
