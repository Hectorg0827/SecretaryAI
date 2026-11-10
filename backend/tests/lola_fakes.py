"""
In-memory Supabase stand-in and a seeded two-company org for Lola tests.

The fake implements just the PostgREST query-builder surface Lola uses
(select/insert/update/upsert + eq/in_/is_/lt/order/limit). Tables can be made
to fail on demand (``db.fail.add("policy_rules")``) to test fail-closed paths,
and unique constraints can be declared to test idempotency races.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional


class FakeResult:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "FakeDB", table: str):
        self.db, self.table = db, table
        self.op = "select"
        self.payload: Any = None
        self.filters: list = []
        self._orders: list[tuple[str, bool]] = []
        self._limit: Optional[int] = None
        self._range: Optional[tuple[int, int]] = None
        self._on_conflict: Optional[str] = None
        self._ignore_dup = False

    # builders
    def select(self, *_cols, **_kw):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self.op, self.payload, self._on_conflict, self._ignore_dup = "upsert", rows, on_conflict, ignore_duplicates
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def lt(self, col, val):
        self.filters.append(("lt", col, val))
        return self

    def order(self, col, desc=False):
        self._orders.append((col, desc))
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._limit = 1
        return self

    # evaluation
    def _match(self, row: dict) -> bool:
        for op, col, val in self.filters:
            cur = row.get(col)
            if op == "eq" and str(cur) != str(val):
                return False
            if op == "neq" and str(cur) == str(val):
                return False
            if op == "in" and str(cur) not in {str(v) for v in val}:
                return False
            if op == "is" and val == "null" and cur is not None:
                return False
            if op == "lt" and not (cur is not None and str(cur) < str(val)):
                return False
        return True

    def execute(self):
        if self.table in self.db.fail:
            raise RuntimeError(f"simulated outage: {self.table}")
        self.db.calls.append((self.table, self.op))
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [copy.deepcopy(r) for r in rows if self._match(r)]
            for col, desc in reversed(self._orders):          # stable: first order() wins
                out.sort(key=lambda r, c=col: str(r.get(c) or ""), reverse=desc)
            if self._range is not None:
                out = out[self._range[0]: self._range[1] + 1]
            if self._limit is not None:
                out = out[: self._limit]
            return FakeResult(out)
        if self.op == "insert":
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            for r in new:
                self.db._check_unique(self.table, r)
            for r in new:
                r = copy.deepcopy(r)
                r.setdefault("id", str(uuid.uuid4()))
                rows.append(r)
            return FakeResult(copy.deepcopy(new))
        if self.op == "update":
            changed = []
            for r in rows:
                if self._match(r):
                    r.update(copy.deepcopy(self.payload))
                    changed.append(copy.deepcopy(r))
            return FakeResult(changed)
        if self.op == "upsert":
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self._on_conflict or "id").split(",")]
            for r in new:
                hit = next((x for x in rows if all(str(x.get(k)) == str(r.get(k)) for k in keys)), None)
                if hit and not self._ignore_dup:
                    hit.update(copy.deepcopy(r))
                elif not hit:
                    rows.append(copy.deepcopy(r))
            return FakeResult(copy.deepcopy(new))
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            gone = len(rows) - len(keep)
            self.db.tables[self.table] = keep
            return FakeResult([{}] * gone)
        raise AssertionError(self.op)


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.fail: set[str] = set()
        self.unique: dict[str, list[tuple[str, ...]]] = {
            "lola_action_receipts": [("company_id", "idempotency_key")],
            "drafts": [("id",)],
        }
        self.calls: list[tuple[str, str]] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def _check_unique(self, table: str, row: dict) -> None:
        for cols in self.unique.get(table, []):
            for existing in self.tables.get(table, []):
                if all(str(existing.get(c)) == str(row.get(c)) for c in cols):
                    raise RuntimeError(f"duplicate key on {table}{cols}")

    def rows(self, table: str, **eq) -> list[dict]:
        return [r for r in self.tables.get(table, []) if all(str(r.get(k)) == str(v) for k, v in eq.items())]


# ─── Seeded organisation ─────────────────────────────────────────────────────

CO_A = "11111111-1111-1111-1111-111111111111"
CO_B = "22222222-2222-2222-2222-222222222222"

OWNER = "a0000000-0000-0000-0000-000000000001"
SALES_MGR = "a0000000-0000-0000-0000-000000000002"   # manager role, department manager of sales
REP1 = "a0000000-0000-0000-0000-000000000003"        # reports to SALES_MGR, territory West
REP2 = "a0000000-0000-0000-0000-000000000004"        # reports to SALES_MGR
REP3 = "a0000000-0000-0000-0000-000000000005"        # reports to OTHER_MGR (peer team)
OTHER_MGR = "a0000000-0000-0000-0000-000000000006"   # manager role, department manager of sales (East)
BACK_OFFICE = "a0000000-0000-0000-0000-000000000007"
FINANCE = "a0000000-0000-0000-0000-000000000008"     # back_office + financial clearance, finance dept
HR = "a0000000-0000-0000-0000-000000000009"          # back_office + hr clearance, hr dept
VIEWER = "a0000000-0000-0000-0000-00000000000a"
INACTIVE = "a0000000-0000-0000-0000-00000000000b"
EXEC_MGR = "a0000000-0000-0000-0000-00000000000c"    # manager role, no org profile → executive (legacy)
OWNER_B = "b0000000-0000-0000-0000-000000000001"


def seed(db: Optional[FakeDB] = None) -> FakeDB:
    db = db or FakeDB()
    users = [
        (OWNER, CO_A, "owner", True, "Olivia Owner"),
        (SALES_MGR, CO_A, "manager", True, "Sam Sales-Manager"),
        (REP1, CO_A, "sales_rep", True, "Rita Rep"),
        (REP2, CO_A, "sales_rep", True, "Ravi Rep"),
        (REP3, CO_A, "sales_rep", True, "Rosa Rep"),
        (OTHER_MGR, CO_A, "manager", True, "Omar Other-Manager"),
        (BACK_OFFICE, CO_A, "back_office", True, "Bea Back-Office"),
        (FINANCE, CO_A, "back_office", True, "Fiona Finance"),
        (HR, CO_A, "back_office", True, "Hank HR"),
        (VIEWER, CO_A, "viewer", True, "Vic Viewer"),
        (INACTIVE, CO_A, "sales_rep", False, "Ida Inactive"),
        (EXEC_MGR, CO_A, "manager", True, "Eve Exec-Manager"),
        (OWNER_B, CO_B, "owner", True, "Bob B-Owner"),
    ]
    db.tables["users"] = [
        {"id": u, "company_id": c, "role": r, "is_active": a, "full_name": n} for u, c, r, a, n in users
    ]
    db.tables["lola_org_members"] = [
        {"company_id": CO_A, "user_id": OWNER, "org_level": "executive", "department": "executive",
         "hr_clearance": True},
        {"company_id": CO_A, "user_id": SALES_MGR, "org_level": "department_manager", "department": "sales",
         "manager_user_id": OWNER, "action_limit": "5000"},
        {"company_id": CO_A, "user_id": REP1, "org_level": "employee", "department": "sales",
         "manager_user_id": SALES_MGR, "territories": ["West"]},
        {"company_id": CO_A, "user_id": REP2, "org_level": "employee", "department": "sales",
         "manager_user_id": SALES_MGR},
        {"company_id": CO_A, "user_id": OTHER_MGR, "org_level": "department_manager", "department": "sales_east",
         "manager_user_id": OWNER},
        {"company_id": CO_A, "user_id": REP3, "org_level": "employee", "department": "sales_east",
         "manager_user_id": OTHER_MGR},
        {"company_id": CO_A, "user_id": BACK_OFFICE, "org_level": "employee", "department": "operations"},
        {"company_id": CO_A, "user_id": FINANCE, "org_level": "employee", "department": "finance",
         "financial_clearance": True},
        {"company_id": CO_A, "user_id": HR, "org_level": "employee", "department": "hr", "hr_clearance": True},
    ]
    db.tables["accounts"] = [
        {"id": "acc-1", "company_id": CO_A, "qb_customer_id": "QB-1", "email": "buyer@acmefoods.test", "name": "Acme Foods",
         "territory": "West", "assigned_rep_id": REP1},
        {"id": "acc-2", "company_id": CO_A, "qb_customer_id": "QB-2", "email": "orders@bravo.test", "name": "Bravo Imports",
         "territory": "North", "assigned_rep_id": REP2},
        {"id": "acc-3", "company_id": CO_A, "qb_customer_id": "QB-3", "email": "ap@cedar.test", "name": "Cedar Trading",
         "territory": "East", "assigned_rep_id": REP3},
        {"id": "acc-4", "company_id": CO_A, "qb_customer_id": "QB-4", "name": "Delta Supply",
         "territory": "West", "assigned_rep_id": None},
        # Same QuickBooks id in another tenant — must never cross over.
        {"id": "acc-b1", "company_id": CO_B, "qb_customer_id": "QB-1", "name": "Zeta Corp",
         "territory": "West", "assigned_rep_id": OWNER_B},
    ]
    db.tables["lola_delegations"] = []
    db.tables["lola_memories"] = []
    db.tables["lola_action_receipts"] = []
    db.tables["drafts"] = []
    db.tables["policy_rules"] = []
    db.tables["company_features"] = []
    db.tables["conversations"] = []
    db.tables["audit_events"] = []
    db.tables["agent_executions"] = []
    return db


def user(uid: str, company: str = CO_A, role: str = "owner") -> dict:
    """A decoded-JWT-like dict. The role here is deliberately ignorable."""
    return {"sub": uid, "company_id": company, "role": role}


def delegate(db: FakeDB, grantor: str, grantee: str, types: list[str], *, account_ids=None,
             days: float = 7, revoked: bool = False, company: str = CO_A) -> dict:
    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()), "company_id": company, "grantor_user_id": grantor, "grantee_user_id": grantee,
        "resource_types": types, "account_ids": account_ids or [], "reason": "cover",
        "created_at": now.isoformat(), "expires_at": (now + timedelta(days=days)).isoformat(),
        "revoked_at": now.isoformat() if revoked else None,
    }
    db.tables["lola_delegations"].append(row)
    return row


# ─── Fake data adapter (QuickBooks-like) ─────────────────────────────────────


@dataclass
class _Customer:
    id: str
    qb_id: str
    name: str
    state: Optional[str]
    balance: Decimal


@dataclass
class _Invoice:
    id: str
    qb_id: str
    customer_id: str
    customer_name: str
    date: date
    due_date: Optional[date]
    total: Decimal
    balance: Decimal
    status: str = "open"


class FakeAdapter:
    def __init__(self, *, customers=None, fail: bool = False):
        today = date.today()
        self.fail = fail
        self.customers = customers if customers is not None else [
            _Customer("c1", "QB-1", "Acme Foods", "CA", Decimal("1200.00")),
            _Customer("c2", "QB-2", "Bravo Imports", "NY", Decimal("800.00")),
            _Customer("c3", "QB-3", "Cedar Trading", "FL", Decimal("9999.00")),
            _Customer("c4", "QB-4", "Delta Supply", "OR", Decimal("0")),
        ]
        self.invoices = [
            _Invoice("i1", "INV-1", "QB-1", "Acme Foods", today - timedelta(days=10), today - timedelta(days=2),
                     Decimal("1000"), Decimal("1000")),
            _Invoice("i2", "INV-2", "QB-1", "Acme Foods", today - timedelta(days=5), today + timedelta(days=25),
                     Decimal("200"), Decimal("200")),
            _Invoice("i3", "INV-3", "QB-2", "Bravo Imports", today - timedelta(days=3), today + timedelta(days=27),
                     Decimal("800"), Decimal("800")),
            _Invoice("i4", "INV-4", "QB-3", "Cedar Trading", today - timedelta(days=8), today - timedelta(days=1),
                     Decimal("9999"), Decimal("9999")),
        ]
        self.inventory = [
            {"qb_id": "SKU-1", "product_name": "Olive Oil 1L", "sku": "OO1", "total_qty": 4.0,
             "reorder_point": 10.0, "purchase_cost": 3.25, "source": "qb"},
            {"qb_id": "SKU-2", "product_name": "Sea Salt", "sku": "SS1", "total_qty": 400.0,
             "reorder_point": 50.0, "purchase_cost": 0.40, "source": "qb"},
        ]
        self.calls: list[str] = []

    def _check(self, name):
        self.calls.append(name)
        if self.fail:
            raise RuntimeError("QuickBooks unreachable")

    async def get_all_customers(self):
        self._check("get_all_customers")
        return list(self.customers)

    async def get_orders_last_n_days(self, days=30):
        self._check("get_orders_last_n_days")
        return list(self.invoices)

    async def get_invoice_summaries_last_n_days(self, days=30):
        self._check("get_invoice_summaries_last_n_days")
        return list(self.invoices)

    async def get_inventory_merged(self):
        self._check("get_inventory_merged")
        return [dict(i) for i in self.inventory]

    async def get_purchase_orders(self):
        self._check("get_purchase_orders")
        return [{"qb_id": "PO-1", "vendor_id": "V-7", "vendor_name": "Oliva SA", "status": "open", "total": 5000,
                 "date": "2026-09-01",
                 "line_items": [{"ItemBasedExpenseLineDetail": {"ItemRef": {"value": "SKU-1"}}}]}]
