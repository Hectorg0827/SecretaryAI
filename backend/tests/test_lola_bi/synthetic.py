"""Synthetic, invented business data for Lola BI tests. No real company data.

Reference day: 2026-11-09 (UTC). Home currency USD, multicurrency enabled.
Comparison windows for 90 days: current 2026-08-12..2026-11-09,
prior 2026-05-14..2026-08-11.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace
from typing import Any

from app.lola_bi.gateway import BusinessDataGateway, SnapshotCache
from app.lola_bi.scope import BIAccessScope, ROLE_CAPABILITIES
from app.lola_bi.tools import LolaBIService

TENANT = "tenant-acme"
OTHER_TENANT = "tenant-globex"
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
REP1, REP2 = "user-rep-1", "user-rep-2"


def clock() -> datetime:
    return NOW


def line(item_id: str, name: str, qty: str, amount: str, unit_price: str | None = None) -> dict:
    return {"line_id": None, "item_id": item_id, "name": name, "description": "",
            "quantity": D(qty), "unit_price": D(unit_price) if unit_price else None, "amount": D(amount)}


def inv(iid: str, cust: str, name: str, day: date, lines: list[dict], *, balance: str = "0",
        due: date | None = None, currency: str | None = "USD", doc: str | None = None,
        status: str | None = None, total: str | None = None) -> dict:
    tot = D(total) if total is not None else sum((l["amount"] for l in lines), D("0"))
    bal = D(balance)
    return {"id": iid, "doc_number": doc or iid, "customer_id": cust, "customer_name": name, "date": day,
            "due_date": due, "total": tot, "balance": bal,
            "status": status or ("open" if bal > 0 else "paid"), "currency": currency,
            "lines": lines, "lines_observed": True}


CUSTOMERS = [
    {"id": "C1", "name": "Sunrise Distributors", "email": "buyer@sunrise.example", "state": "FL",
     "balance": D("1500.00"), "currency": "USD"},
    {"id": "C2", "name": "Delta Beverage Co", "email": "ap@delta.example", "state": "GA",
     "balance": D("300.50"), "currency": "USD"},
    {"id": "C3", "name": "Maple Imports Ltd", "email": "orders@maple.example", "state": None,
     "balance": D("700.00"), "currency": "CAD"},
    {"id": "C4", "name": "Sunrise Liquors", "email": None, "state": "FL",
     "balance": D("-50.00"), "currency": "USD"},
]

PRODUCTS = [
    {"id": "P1", "name": "Reserve Rum 750ml", "full_name": "Lion:Reserve Rum 750ml", "sku": "LR750",
     "qty_on_hand": D("120"), "cost": D("10.00"), "list_price": D("20.00"), "brand": "Lion",
     "brand_source": "qb_item_hierarchy", "active": True},
    {"id": "P2", "name": "Spiced Rum 1L", "full_name": "Lion:Spiced Rum 1L", "sku": "LS1L",
     "qty_on_hand": D("0"), "cost": D("12.00"), "list_price": D("20.00"), "brand": "Lion",
     "brand_source": "qb_item_hierarchy", "active": True},
    {"id": "P3", "name": "Tequila Blanco", "full_name": "Tigre:Tequila Blanco", "sku": "TB750",
     "qty_on_hand": D("40"), "cost": D("15.00"), "list_price": D("30.00"), "brand": "Tigre",
     "brand_source": "qb_item_hierarchy", "active": True},
    {"id": "P4", "name": "Mezcal Artesanal", "full_name": "Mezcal Artesanal", "sku": None,
     "qty_on_hand": D("10"), "cost": None, "list_price": D("45.00"), "brand": None,
     "brand_source": None, "active": True},
]

# Sales history. C1 drops sharply (and P2 is now out of stock, P1 price rose);
# C2 grows; C3 is CAD; one void invoice must be ignored.
INVOICES = [
    # prior window (2026-05-14 .. 2026-08-11)
    inv("I-101", "C1", "Sunrise Distributors", date(2026, 6, 1), [line("P1", "Reserve Rum 750ml", "50", "1000.00", "20.00")]),
    inv("I-102", "C1", "Sunrise Distributors", date(2026, 7, 1), [line("P2", "Spiced Rum 1L", "40", "800.00", "20.00"),
                                                                 line("P1", "Reserve Rum 750ml", "10", "200.00", "20.00")]),
    inv("I-103", "C1", "Sunrise Distributors", date(2026, 8, 1), [line("P2", "Spiced Rum 1L", "50", "1000.00", "20.00")]),
    inv("I-201", "C2", "Delta Beverage Co", date(2026, 7, 15), [line("P3", "Tequila Blanco", "10", "300.00", "30.00")]),
    inv("I-301", "C3", "Maple Imports Ltd", date(2026, 7, 20), [line("P1", "Reserve Rum 750ml", "10", "250.00", "25.00")],
        currency="CAD"),
    # current window (2026-08-12 .. 2026-11-09)
    inv("I-104", "C1", "Sunrise Distributors", date(2026, 9, 15), [line("P1", "Reserve Rum 750ml", "20", "440.00", "22.00")]),
    inv("I-202", "C2", "Delta Beverage Co", date(2026, 9, 25), [line("P3", "Tequila Blanco", "30", "800.00", "26.6667")],
        balance="300.50", due=date(2026, 10, 25)),
    inv("I-203", "C2", "Delta Beverage Co", date(2026, 11, 2), [line("P4", "Mezcal Artesanal", "5", "225.00", "45.00"),
                                                               line("P1", "Reserve Rum 750ml", "10", "90.00", "9.00")]),
    inv("I-204", "C2", "Delta Beverage Co", date(2026, 11, 3), [line("P3", "Tequila Blanco", "1", "30.00", "30.00")],
        status="void", total="0"),
    inv("I-302", "C3", "Maple Imports Ltd", date(2026, 10, 1), [line("P1", "Reserve Rum 750ml", "28", "700.00", "25.00")],
        balance="700.00", due=date(2026, 12, 1), currency="CAD"),
    # same month last year (Nov 2025) for period comparison
    inv("I-090", "C1", "Sunrise Distributors", date(2025, 11, 5), [line("P1", "Reserve Rum 750ml", "25", "500.00", "20.00")]),
    inv("I-091", "C2", "Delta Beverage Co", date(2025, 11, 20), [line("P3", "Tequila Blanco", "10", "300.00", "30.00")]),
    # prior month same days (Oct 1..9)
    inv("I-205", "C2", "Delta Beverage Co", date(2026, 10, 3), [line("P3", "Tequila Blanco", "5", "150.00", "30.00")]),
]

# Open AR (all open invoices, any date). Includes older invoices not in windows.
OPEN_INVOICES = [
    inv("I-050", "C1", "Sunrise Distributors", date(2026, 8, 2), [line("P1", "Reserve Rum 750ml", "50", "1000.00")],
        balance="1000.00", due=date(2026, 9, 1), doc="5001"),
    inv("I-051", "C1", "Sunrise Distributors", date(2026, 10, 21), [line("P1", "Reserve Rum 750ml", "25", "500.00")],
        balance="500.00", due=date(2026, 11, 20), doc="5002"),
    copy.deepcopy(INVOICES[6]),   # I-202 C2 300.50 due 2026-10-25
    copy.deepcopy(INVOICES[9]),   # I-302 C3 CAD 700 due 2026-12-01
]

PAYMENTS = [
    {"id": "PMT-1", "customer_id": "C2", "date": date(2026, 10, 20), "total": D("499.50"), "unapplied": D("0"),
     "currency": "USD", "applied": [{"invoice_id": "I-202", "amount": D("499.50")}], "other_links": []},
    {"id": "PMT-2", "customer_id": "C1", "date": date(2026, 10, 5), "total": D("440.00"), "unapplied": D("0"),
     "currency": "USD", "applied": [{"invoice_id": "I-104", "amount": D("440.00")}], "other_links": []},
    {"id": "PMT-3", "customer_id": "C1", "date": date(2026, 10, 30), "total": D("100.00"), "unapplied": D("100.00"),
     "currency": "USD", "applied": [], "other_links": []},
]

BILLS = [
    {"id": "B1", "vendor_id": "V1", "vendor_name": "Caribe Spirits", "doc_number": "INV-100",
     "date": date(2026, 10, 16), "due_date": date(2026, 11, 15), "total": D("5000.00"), "balance": D("5000.00"),
     "currency": "USD"},
    {"id": "B2", "vendor_id": "V1", "vendor_name": "Caribe Spirits", "doc_number": "inv 100",
     "date": date(2026, 10, 17), "due_date": date(2026, 11, 15), "total": D("5000.00"), "balance": D("5000.00"),
     "currency": "USD"},
    {"id": "B3", "vendor_id": "V2", "vendor_name": "Agave Co", "doc_number": "A-77",
     "date": date(2026, 10, 31), "due_date": date(2026, 11, 30), "total": D("2000.00"), "balance": D("2000.00"),
     "currency": "USD"},
    {"id": "B4", "vendor_id": "V2", "vendor_name": "Agave Co", "doc_number": "A-70",
     "date": date(2026, 9, 1), "due_date": date(2026, 10, 1), "total": D("750.00"), "balance": D("750.00"),
     "currency": "USD"},
]

BANKS = [
    {"id": "BK1", "name": "Operating", "balance": D("12000.00"), "currency": "USD",
     "balance_basis": "quickbooks_book_balance"},
    {"id": "BK2", "name": "Savings", "balance": D("3000.00"), "currency": "USD",
     "balance_basis": "quickbooks_book_balance"},
    {"id": "BK3", "name": "Toronto", "balance": D("1000.00"), "currency": "CAD",
     "balance_basis": "quickbooks_book_balance"},
]

PNL = {"start": date(2026, 11, 1), "end": TODAY, "currency": "USD", "basis": "Accrual",
       "income": D("10000.00"), "cost_of_goods_sold": D("6000.00"), "gross_profit": D("4000.00"),
       "expenses": D("2500.00"), "net_operating_income": D("1500.00"), "other_income": D("100.00"),
       "other_expenses": None, "net_other_income": D("100.00"), "net_income": D("1600.00")}

DIRECTORY_ROWS = [
    {"qb_customer_id": "C1", "name": "Sunrise Distributors", "territory": "North", "assigned_rep_id": REP1,
     "company_id": TENANT},
    {"qb_customer_id": "C3", "name": "Maple Imports Ltd", "territory": "North", "assigned_rep_id": REP1,
     "company_id": TENANT},
    {"qb_customer_id": "C2", "name": "Delta Beverage Co", "territory": "South", "assigned_rep_id": REP2,
     "company_id": TENANT},
    {"qb_customer_id": "C4", "name": "Sunrise Liquors", "territory": "South", "assigned_rep_id": None,
     "company_id": TENANT},
]
USERS_ROWS = [{"id": REP1, "full_name": "Ana Rep", "company_id": TENANT},
              {"id": REP2, "full_name": "Ben Rep", "company_id": TENANT}]


class FakeReads:
    """In-memory BI reader. ``fail`` maps method name → exception to raise."""

    provider = "quickbooks_online"
    mapping_quality = "provider_observed"

    def __init__(self, **overrides: Any):
        self.data = {
            "customers": copy.deepcopy(CUSTOMERS), "products": copy.deepcopy(PRODUCTS),
            "invoices": copy.deepcopy(INVOICES), "open_invoices": copy.deepcopy(OPEN_INVOICES),
            "payments": copy.deepcopy(PAYMENTS), "bills": copy.deepcopy(BILLS),
            "banks": copy.deepcopy(BANKS), "pnl": dict(PNL),
            "currency": {"home_currency": "USD", "multicurrency_enabled": True, "source": "qbo_preferences"},
        }
        self.data.update(overrides.pop("data", {}))
        self.fail: dict[str, BaseException] = overrides.pop("fail", {})
        self.calls: list[str] = []

    def _check(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]

    async def company_currency(self):
        self._check("company_currency")
        return dict(self.data["currency"])

    async def customers(self):
        self._check("customers")
        return copy.deepcopy(self.data["customers"])

    async def products(self):
        self._check("products")
        return copy.deepcopy(self.data["products"])

    async def open_invoices(self):
        self._check("open_invoices")
        return copy.deepcopy(self.data["open_invoices"])

    async def invoices_between(self, a: date, b: date):
        self._check("invoices_between")
        return [copy.deepcopy(i) for i in self.data["invoices"] if a <= i["date"] <= b]

    async def payments_between(self, a: date, b: date):
        self._check("payments_between")
        return [copy.deepcopy(p) for p in self.data["payments"] if a <= p["date"] <= b]

    async def open_bills(self):
        self._check("open_bills")
        return copy.deepcopy(self.data["bills"])

    async def bank_accounts(self):
        self._check("bank_accounts")
        return copy.deepcopy(self.data["banks"])

    async def profit_and_loss(self, start: date, end: date):
        self._check("profit_and_loss")
        if start == self.data["pnl"]["start"] and end == self.data["pnl"]["end"]:
            return dict(self.data["pnl"])
        prior = dict(self.data["pnl"], start=start, end=end, income=D("8000.00"), net_income=D("1000.00"))
        return prior


class FakeAdapter:
    def __init__(self, tenant: str = TENANT, inventory: list | None = None, emails: list | None = None,
                 email_error: BaseException | None = None):
        self._config = {"id": tenant}
        self._gmail = object()
        self._inventory = inventory if inventory is not None else [
            {"qb_id": "P1", "product_name": "Reserve Rum 750ml", "total_qty": 120.0}]
        self._emails = emails if emails is not None else [
            {"id": "m1", "thread_id": "t1", "from": "Buyer <buyer@sunrise.example>",
             "from_email": "buyer@sunrise.example", "subject": "Pricing question", "date": "2026-11-01",
             "snippet": "SECRET BODY TEXT"}]
        self._email_error = email_error

    async def get_inventory_merged(self):
        return self._inventory

    async def get_emails(self, query: str = "", max_results: int = 20):
        if self._email_error:
            raise self._email_error
        rows = self._emails
        if query.startswith("from:"):
            addr = query[5:]
            rows = [r for r in rows if r.get("from_email") == addr]
        return rows[:max_results]


class FakeQuery:
    def __init__(self, rows: list[dict], fail: BaseException | None = None):
        self._rows = rows
        self._filters: list[tuple[str, Any]] = []
        self._fail = fail

    def select(self, *_a):
        return self

    def eq(self, key: str, value: Any):
        self._filters.append((key, value))
        return self

    def execute(self):
        if self._fail:
            raise self._fail
        rows = [r for r in self._rows if all(r.get(k) == v for k, v in self._filters)]
        return SimpleNamespace(data=copy.deepcopy(rows))


class FakeDB:
    def __init__(self, accounts: list[dict] | None = None, users: list[dict] | None = None,
                 fail: BaseException | None = None):
        self.tables = {"accounts": accounts if accounts is not None else copy.deepcopy(DIRECTORY_ROWS),
                       "users": users if users is not None else copy.deepcopy(USERS_ROWS)}
        self.fail = fail

    def table(self, name: str):
        return FakeQuery(self.tables.get(name, []), self.fail)


def scope_for(role: str, *, tenant: str = TENANT, user: str = "user-1",
              accounts: frozenset[str] = frozenset(), caps: frozenset[str] | None = None) -> BIAccessScope:
    company = role in ("owner", "manager", "back_office")
    return BIAccessScope(
        tenant_id=tenant, user_id=user, role=role,
        capabilities=caps if caps is not None else ROLE_CAPABILITIES[role],
        record_scope="company" if company else "assigned_accounts",
        allowed_account_ids=frozenset() if company else accounts,
        can_view_costs=role in ("owner", "manager"),
    )


def service(role: str = "owner", *, reads: FakeReads | None = None, adapter: FakeAdapter | None = None,
            db: FakeDB | None = None, cache: SnapshotCache | None = None, **scope_kw: Any):
    reads = reads or FakeReads()
    scope = scope_for(role, **scope_kw)
    gw = BusinessDataGateway(adapter or FakeAdapter(scope.tenant_id), scope, db=db or FakeDB(),
                             reads=reads, cache=cache, clock=clock)
    return LolaBIService(gw, clock=clock), reads
