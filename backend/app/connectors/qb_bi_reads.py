"""Read-only QuickBooks projections for Lola business intelligence (WS2).

These readers wrap an existing ``QBOnlineAdapter`` / ``QBDesktopAdapter`` and
add the reads the analytics need but the base adapters lack: all open
invoices (not a date window), customer payments with invoice allocations,
open vendor bills, bank book balances, the home currency and the QBO
Profit & Loss report.

Safety
------
* Only GET/query calls. No method here creates, updates, voids or pays.
* Every record is validated; a missing amount, ID or date raises ``ValueError``
  instead of being imputed as zero or today.
* Amounts are ``Decimal`` in the *transaction* currency, with that currency
  code attached when the provider supplies one.
* QuickBooks Desktop (Conductor) mappings for payments, bills and bank
  accounts have **no vendor-backed fixtures yet**; they report
  ``quality = unverified_mapping`` so consumers never treat them as verified.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping

import httpx

from app.connectors.invoice_integrity import decimal_field

PROVIDER_QBO = "quickbooks_online"
PROVIDER_QBD = "quickbooks_desktop"


class SourceNotSupported(NotImplementedError):
    """The provider has no read path for this data."""


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is missing")
    return value.strip()


def _opt_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _day(value: object, label: str, *, required: bool = True) -> date | None:
    if value in (None, ""):
        if required:
            raise ValueError(f"{label} is missing")
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        return date.fromisoformat(value[:10])  # "YYYY-MM-DD" or an ISO timestamp
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc


def _ref(raw: Mapping, key: str) -> Mapping:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def _currency(raw: Mapping, key: str = "CurrencyRef") -> str | None:
    value = _ref(raw, key).get("value")
    return value if isinstance(value, str) and value.strip() else None


def _invoice_dict(inv: Any, *, currency: str | None, doc_number: str | None) -> dict:
    return {
        "id": inv.qb_id, "doc_number": doc_number,
        "customer_id": inv.customer_id, "customer_name": inv.customer_name,
        "date": inv.date, "due_date": inv.due_date,
        "total": inv.total, "balance": inv.balance, "status": inv.status,
        "currency": currency,
        "lines": list(inv.line_items), "lines_observed": inv.line_items_observed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# QuickBooks Online
# ─────────────────────────────────────────────────────────────────────────────

_PNL_GROUPS = {
    "Income": "income",
    "COGS": "cost_of_goods_sold",
    "GrossProfit": "gross_profit",
    "Expenses": "expenses",
    "NetOperatingIncome": "net_operating_income",
    "OtherIncome": "other_income",
    "OtherExpenses": "other_expenses",
    "NetOtherIncome": "net_other_income",
    "NetIncome": "net_income",
}


class QBOnlineBIReads:
    provider = PROVIDER_QBO
    mapping_quality = "provider_observed"

    def __init__(self, qbo: Any):
        self._q = qbo

    async def customers(self) -> list[dict]:
        raws = await self._q._query_all("Customer", "Active = True")
        out = []
        for raw in raws:
            cust = self._q._normalize_customer(raw)
            balance = decimal_field(raw.get("Balance"), "QBO customer Balance", signed=True)
            out.append({
                "id": _text(cust.qb_id, "QBO customer Id"), "name": cust.name or "",
                "email": cust.email, "state": cust.state, "balance": balance,
                "currency": _currency(raw),
            })
        return out

    async def open_invoices(self) -> list[dict]:
        """Every invoice with an open balance, regardless of date."""
        raws = await self._q._query_all("Invoice", "Balance > '0'")
        out = []
        for raw in raws:
            inv = self._q._normalize_invoice_summary(raw)
            if inv.balance <= 0:
                raise ValueError("QBO open-invoice query returned a closed invoice")
            out.append(_invoice_dict(inv, currency=_currency(raw), doc_number=_opt_text(raw.get("DocNumber"))))
        return out

    async def invoices_between(self, date_from: date, date_to: date) -> list[dict]:
        where = f"TxnDate >= '{date_from.isoformat()}' AND TxnDate <= '{date_to.isoformat()}'"
        raws = await self._q._query_all("Invoice", where)
        out = []
        for raw in raws:
            inv = self._q._normalize_invoice(raw)
            if not date_from <= inv.date <= date_to:
                raise ValueError("QBO invoice outside the requested date window")
            out.append(_invoice_dict(inv, currency=_currency(raw), doc_number=_opt_text(raw.get("DocNumber"))))
        return out

    async def payments_between(self, date_from: date, date_to: date) -> list[dict]:
        where = f"TxnDate >= '{date_from.isoformat()}' AND TxnDate <= '{date_to.isoformat()}'"
        raws = await self._q._query_all("Payment", where)
        return [self._payment(raw, date_from, date_to) for raw in raws]

    @staticmethod
    def _payment(raw: Mapping, date_from: date, date_to: date) -> dict:
        pid = _text(raw.get("Id"), "QBO payment Id")
        paid_on = _day(raw.get("TxnDate"), "QBO payment TxnDate")
        if not date_from <= paid_on <= date_to:
            raise ValueError("QBO payment outside the requested date window")
        total = decimal_field(raw.get("TotalAmt"), "QBO payment TotalAmt")
        unapplied_raw = raw.get("UnappliedAmt")
        unapplied = None if unapplied_raw in (None, "") else decimal_field(unapplied_raw, "QBO UnappliedAmt")
        applied: list[dict] = []
        other_links: list[dict] = []
        lines = raw.get("Line", [])
        if not isinstance(lines, list):
            raise ValueError("QBO payment Line must be a list")
        for line in lines:
            if not isinstance(line, Mapping):
                raise ValueError("QBO payment line is not a mapping")
            amount = decimal_field(line.get("Amount"), "QBO payment line Amount")
            links = line.get("LinkedTxn")
            if not isinstance(links, list) or not links:
                raise ValueError("QBO payment line has no linked transaction")
            for link in links:
                if not isinstance(link, Mapping):
                    raise ValueError("QBO payment link malformed")
                txn_id = _text(link.get("TxnId"), "QBO LinkedTxn TxnId")
                kind = _text(link.get("TxnType"), "QBO LinkedTxn TxnType")
                (applied if kind == "Invoice" else other_links).append(
                    {"txn_id": txn_id, "txn_type": kind, "amount": amount})
        return {
            "id": pid, "customer_id": _text(_ref(raw, "CustomerRef").get("value"), "QBO payment CustomerRef"),
            "date": paid_on, "total": total, "unapplied": unapplied,
            "currency": _currency(raw),
            "applied": [{"invoice_id": a["txn_id"], "amount": a["amount"]} for a in applied],
            "other_links": other_links,
        }

    async def open_bills(self) -> list[dict]:
        raws = await self._q._query_all("Bill", "Balance > '0'")
        out = []
        for raw in raws:
            total = decimal_field(raw.get("TotalAmt"), "QBO bill TotalAmt")
            balance = decimal_field(raw.get("Balance"), "QBO bill Balance")
            if balance > total:
                raise ValueError("QBO bill balance exceeds total")
            vendor = _ref(raw, "VendorRef")
            out.append({
                "id": _text(raw.get("Id"), "QBO bill Id"),
                "vendor_id": _text(vendor.get("value"), "QBO bill VendorRef"),
                "vendor_name": vendor.get("name") or "",
                "doc_number": _opt_text(raw.get("DocNumber")),
                "date": _day(raw.get("TxnDate"), "QBO bill TxnDate"),
                "due_date": _day(raw.get("DueDate"), "QBO bill DueDate", required=False),
                "total": total, "balance": balance, "currency": _currency(raw),
            })
        return out

    async def bank_accounts(self) -> list[dict]:
        raws = await self._q._query_all("Account", "AccountType = 'Bank'")
        out = []
        for raw in raws:
            if raw.get("AccountType") not in (None, "Bank"):
                raise ValueError("QBO bank query returned a non-bank account")
            if raw.get("Active") is False:
                continue
            out.append({
                "id": _text(raw.get("Id"), "QBO account Id"),
                "name": raw.get("Name") or "",
                "balance": decimal_field(raw.get("CurrentBalance"), "QBO CurrentBalance", signed=True),
                "currency": _currency(raw),
                "balance_basis": "quickbooks_book_balance",
            })
        return out

    async def company_currency(self) -> dict:
        data = await self._q._query("SELECT * FROM Preferences")
        prefs = (data.get("QueryResponse") or {}).get("Preferences") if isinstance(data, dict) else None
        if not isinstance(prefs, list) or len(prefs) != 1 or not isinstance(prefs[0], Mapping):
            raise ValueError("QBO Preferences response malformed")
        cur = prefs[0].get("CurrencyPrefs")
        if not isinstance(cur, Mapping):
            return {"home_currency": None, "multicurrency_enabled": None, "source": "qbo_preferences"}
        enabled = cur.get("MultiCurrencyEnabled")
        home = _ref(cur, "HomeCurrency").get("value")
        return {
            "home_currency": home if isinstance(home, str) else None,
            "multicurrency_enabled": enabled if isinstance(enabled, bool) else None,
            "source": "qbo_preferences",
        }

    async def profit_and_loss(self, start: date, end: date) -> dict:
        report = await self._report_json("ProfitAndLoss", {
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "accounting_method": "Accrual",
        })
        return parse_profit_and_loss(report, start, end)

    async def _report_json(self, name: str, params: dict) -> dict:
        q = self._q
        url = f"{q._base_url}/{q._realm_id}/reports/{name}"
        query = {**params, "minorversion": "70"}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url, params=query, headers=q._headers())
            if response.status_code == 401:
                await q._refresh_access_token()
                response = await client.get(url, params=query, headers=q._headers())
            response.raise_for_status()
            return response.json()

    async def products(self) -> list[dict]:
        raws = await self._q._query_all("Item", "Type = 'Inventory'")
        out = []
        for raw in raws:
            item = self._q._normalize_inventory_item(raw)
            out.append(_product(
                item_id=item.qb_id, name=item.name, full_name=raw.get("FullyQualifiedName"),
                sku=item.sku, qty=item.quantity_on_hand,
                cost=raw.get("PurchaseCost"), price=raw.get("UnitPrice"), active=raw.get("Active", True),
            ))
        return out


def parse_profit_and_loss(report: Any, start: date, end: date) -> dict:
    """Extract QBO P&L section totals; refuse reports that don't match the request."""
    if not isinstance(report, Mapping):
        raise ValueError("P&L report malformed")
    header = report.get("Header")
    if not isinstance(header, Mapping):
        raise ValueError("P&L report header missing")
    if header.get("StartPeriod") != start.isoformat() or header.get("EndPeriod") != end.isoformat():
        raise ValueError("P&L report period does not match the request")
    basis = header.get("ReportBasis")
    rows = (report.get("Rows") or {}).get("Row") if isinstance(report.get("Rows"), Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("P&L report rows missing")
    totals: dict[str, Decimal | None] = {key: None for key in _PNL_GROUPS.values()}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("group") not in _PNL_GROUPS:
            continue
        cols = (row.get("Summary") or {}).get("ColData") if isinstance(row.get("Summary"), Mapping) else None
        if not isinstance(cols, list) or len(cols) != 2 or not isinstance(cols[1], Mapping):
            raise ValueError("P&L summary row must have exactly one total column")
        value = cols[1].get("value")
        key = _PNL_GROUPS[row["group"]]
        if totals[key] is not None:
            raise ValueError("P&L report repeats a section total")
        totals[key] = Decimal("0") if value == "" else decimal_field(value, f"P&L {key}", signed=True)
    if totals["net_income"] is None:
        raise ValueError("P&L report lacks a net income total")
    return {
        "start": start, "end": end,
        "currency": header.get("Currency") if isinstance(header.get("Currency"), str) else None,
        "basis": basis if isinstance(basis, str) else None,
        **totals,
    }


def _product(*, item_id: object, name: object, full_name: object, sku: object, qty: Decimal,
             cost: object, price: object, active: object) -> dict:
    def positive(value: object, label: str) -> Decimal | None:
        if value in (None, ""):
            return None
        amount = decimal_field(value, label, signed=True)
        return amount if amount > 0 else None  # zero cost/price is "not maintained"

    full = full_name if isinstance(full_name, str) and full_name.strip() else (name if isinstance(name, str) else "")
    brand, brand_source = (None, None)
    if ":" in full:
        brand, brand_source = full.split(":", 1)[0].strip() or None, "qb_item_hierarchy"
    return {
        "id": _text(item_id, "item id"), "name": name if isinstance(name, str) else "",
        "full_name": full, "sku": sku if isinstance(sku, str) and sku.strip() else None,
        "qty_on_hand": qty, "cost": positive(cost, "item cost"), "list_price": positive(price, "item price"),
        "brand": brand, "brand_source": brand_source, "active": active is not False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# QuickBooks Desktop via Conductor
# ─────────────────────────────────────────────────────────────────────────────

class QBDesktopBIReads:
    provider = PROVIDER_QBD
    # Payment/bill/account field names below were not validated against
    # Conductor vendor fixtures. Invoices/customers/items reuse the adapter's
    # existing normalizers.
    mapping_quality = "unverified_mapping"

    def __init__(self, qbd: Any, company_config: Mapping | None = None):
        self._d = qbd
        self._cfg = company_config or {}

    async def customers(self) -> list[dict]:
        return [
            {"id": _text(c.qb_id, "Conductor customer id"), "name": c.name or "", "email": c.email,
             "state": c.state, "balance": c.balance, "currency": None}
            for c in await self._d.get_customers()
        ]

    async def open_invoices(self) -> list[dict]:
        raws = await self._d._paginate("/quickbooks-desktop/invoices", {"paymentStatus": "not_paid"})
        out = []
        for raw in raws:
            inv = self._d._normalize_invoice(raw)
            if inv.balance <= 0:
                raise ValueError("Conductor not_paid filter returned a closed invoice")
            out.append(_invoice_dict(inv, currency=None, doc_number=_opt_text(raw.get("refNumber"))))
        return out

    async def invoices_between(self, date_from: date, date_to: date) -> list[dict]:
        out = []
        for inv in await self._d.get_invoices(date_from, date_to):
            if not date_from <= inv.date <= date_to:
                raise ValueError("Conductor invoice outside the requested date window")
            out.append(_invoice_dict(inv, currency=None, doc_number=None))
        return out

    async def payments_between(self, date_from: date, date_to: date) -> list[dict]:
        raws = await self._d._paginate("/quickbooks-desktop/receive-payments", {
            "transactionDateFrom": date_from.isoformat(), "transactionDateTo": date_to.isoformat(),
        })
        out = []
        for raw in raws:
            paid_on = _day(raw.get("transactionDate"), "Conductor payment date")
            if not date_from <= paid_on <= date_to:
                raise ValueError("Conductor payment outside the requested date window")
            applied = []
            for link in raw.get("appliedToTransactions") or []:
                if not isinstance(link, Mapping):
                    raise ValueError("Conductor payment allocation malformed")
                applied.append({
                    "invoice_id": _text(link.get("transactionId"), "Conductor applied transactionId"),
                    "amount": decimal_field(link.get("paymentAmount"), "Conductor paymentAmount"),
                })
            unused = raw.get("unusedPayment")
            out.append({
                "id": _text(raw.get("id"), "Conductor payment id"),
                "customer_id": _text(_ref(raw, "customer").get("id"), "Conductor payment customer"),
                "date": paid_on,
                "total": decimal_field(raw.get("totalAmount"), "Conductor payment totalAmount"),
                "unapplied": None if unused in (None, "") else decimal_field(unused, "Conductor unusedPayment"),
                "currency": None, "applied": applied, "other_links": [],
            })
        return out

    async def open_bills(self) -> list[dict]:
        raws = await self._d._paginate("/quickbooks-desktop/bills", {"paymentStatus": "not_paid"})
        out = []
        for raw in raws:
            balance = decimal_field(raw.get("amountDue"), "Conductor bill amountDue")
            total_raw = raw.get("totalAmount", raw.get("amountDue"))
            total = decimal_field(total_raw, "Conductor bill total")
            if balance > total:
                raise ValueError("Conductor bill balance exceeds total")
            if balance <= 0:
                raise ValueError("Conductor not_paid filter returned a closed bill")
            vendor = _ref(raw, "vendor")
            out.append({
                "id": _text(raw.get("id"), "Conductor bill id"),
                "vendor_id": _text(vendor.get("id"), "Conductor bill vendor"),
                "vendor_name": vendor.get("fullName") or vendor.get("name") or "",
                "doc_number": _opt_text(raw.get("refNumber")),
                "date": _day(raw.get("transactionDate"), "Conductor bill date"),
                "due_date": _day(raw.get("dueDate"), "Conductor bill dueDate", required=False),
                "total": total, "balance": balance, "currency": None,
            })
        return out

    async def bank_accounts(self) -> list[dict]:
        raws = await self._d._paginate("/quickbooks-desktop/accounts", {"accountType": "bank"})
        out = []
        for raw in raws:
            if str(raw.get("accountType", "bank")).lower() != "bank":
                raise ValueError("Conductor bank filter returned a non-bank account")
            if raw.get("isActive") is False:
                continue
            balance = raw.get("currentBalance", raw.get("balance"))
            out.append({
                "id": _text(raw.get("id"), "Conductor account id"),
                "name": raw.get("fullName") or raw.get("name") or "",
                "balance": decimal_field(balance, "Conductor account balance", signed=True),
                "currency": None, "balance_basis": "quickbooks_book_balance",
            })
        return out

    async def company_currency(self) -> dict:
        home = self._cfg.get("home_currency")
        multi = self._cfg.get("qbd_multicurrency_enabled")
        return {
            "home_currency": home if isinstance(home, str) else None,
            "multicurrency_enabled": multi if isinstance(multi, bool) else None,
            "source": "tenant_settings" if isinstance(home, str) else "unknown",
        }

    async def profit_and_loss(self, start: date, end: date) -> dict:
        raise SourceNotSupported("Conductor exposes no Profit & Loss report read")

    async def products(self) -> list[dict]:
        return [
            _product(item_id=i.qb_id, name=i.name, full_name=i.name, sku=i.sku,
                     qty=i.quantity_on_hand, cost=i.purchase_cost, price=i.unit_price, active=True)
            for i in await self._d.get_inventory()
        ]


def bi_reads_for(adapter: Any) -> Any | None:
    """Return the BI reader for a UnifiedDataAdapter's QuickBooks connector."""
    from app.connectors.qb_desktop import QBDesktopAdapter
    from app.connectors.qb_online import QBOnlineAdapter

    qb = getattr(adapter, "_qb", None)
    if isinstance(qb, QBOnlineAdapter):
        return QBOnlineBIReads(qb)
    if isinstance(qb, QBDesktopAdapter):
        return QBDesktopBIReads(qb, getattr(adapter, "_config", {}))
    return None
