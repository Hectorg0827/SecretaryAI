"""
A small in-memory stand-in for the Supabase client, for tests.

Unlike a MagicMock it actually stores rows and applies filters, so a test can
assert on what ended up in a table ("the draft is back to pending", "no audit
row was written") instead of on which mock methods were called.

Supports the subset of the query builder the backend uses:
    db.table(name).select(...).eq(...).in_(...).order(...).limit(...).execute()
    db.table(name).insert(row | [rows]).execute()
    db.table(name).update({...}).eq(...).execute()
    db.table(name).upsert({...}, on_conflict="a,b").execute()
    db.table(name).delete().eq(...).execute()

`fail_tables` makes every call on those tables raise, to simulate an outage
of one table (e.g. the audit log) without touching the others.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db: "FakeSupabase", table: str):
        self._db = db
        self._table = table
        self._op = "select"
        self._payload: Any = None
        self._filters: list = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None
        self._single = False
        self._maybe_single = False
        self._want_count = False
        self._on_conflict: list[str] = []

    # ── operations ──────────────────────────────────────────────────────────
    def select(self, _cols: str = "*", count: str | None = None):
        self._op = "select"
        self._want_count = bool(count)
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def upsert(self, payload, on_conflict: str = "id"):
        self._op, self._payload = "upsert", payload
        self._on_conflict = [c.strip() for c in on_conflict.split(",")]
        return self

    def delete(self):
        self._op = "delete"
        return self

    # ── filters ─────────────────────────────────────────────────────────────
    def _f(self, fn):
        self._filters.append(fn)
        return self

    def eq(self, col, val):
        return self._f(lambda r: r.get(col) == val)

    def neq(self, col, val):
        return self._f(lambda r: r.get(col) != val)

    def in_(self, col, vals):
        vals = list(vals)
        return self._f(lambda r: r.get(col) in vals)

    def lt(self, col, val):
        return self._f(lambda r: r.get(col) is not None and r.get(col) < val)

    def lte(self, col, val):
        return self._f(lambda r: r.get(col) is not None and r.get(col) <= val)

    def gt(self, col, val):
        return self._f(lambda r: r.get(col) is not None and r.get(col) > val)

    def gte(self, col, val):
        return self._f(lambda r: r.get(col) is not None and r.get(col) >= val)

    def is_(self, col, val):
        return self._f(lambda r: r.get(col) is None if val in (None, "null") else r.get(col) == val)

    def order(self, col, desc: bool = False):
        self._order.append((col, desc))
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def range(self, start: int, end: int):
        self._range = (start, end)
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    # ── execution ───────────────────────────────────────────────────────────
    def _match(self, row) -> bool:
        return all(f(row) for f in self._filters)

    def execute(self):
        if self._table in self._db.fail_tables:
            raise RuntimeError(f"simulated outage of table '{self._table}'")
        rows = self._db.tables.setdefault(self._table, [])

        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for item in items:
                row = copy.deepcopy(item)
                row.setdefault("id", str(uuid.uuid4()))
                rows.append(row)
                out.append(copy.deepcopy(row))
            return _Result(out)

        if self._op == "upsert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for item in items:
                existing = next(
                    (r for r in rows if all(r.get(c) == item.get(c) for c in self._on_conflict)), None
                )
                if existing is not None:
                    existing.update(copy.deepcopy(item))
                    out.append(copy.deepcopy(existing))
                else:
                    row = copy.deepcopy(item)
                    row.setdefault("id", str(uuid.uuid4()))
                    rows.append(row)
                    out.append(copy.deepcopy(row))
            return _Result(out)

        if self._op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(copy.deepcopy(self._payload))
                    out.append(copy.deepcopy(r))
            return _Result(out)

        if self._op == "delete":
            kept, removed = [], []
            for r in rows:
                (removed if self._match(r) else kept).append(r)
            self._db.tables[self._table] = kept
            return _Result(removed)

        # select
        found = [copy.deepcopy(r) for r in rows if self._match(r)]
        total = len(found)
        for col, desc in reversed(self._order):
            found.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else ""), reverse=desc)
        if self._range is not None:
            found = found[self._range[0]: self._range[1] + 1]
        if self._limit is not None:
            found = found[: self._limit]
        if self._single:
            if len(found) != 1:
                raise RuntimeError("single() expected exactly one row")
            return _Result(found[0], total)
        if self._maybe_single:
            return _Result(found[0] if found else None, total)
        return _Result(found, total if self._want_count else None)


class FakeSupabase:
    def __init__(self, **tables: list[dict]):
        self.tables: dict[str, list[dict]] = {k: copy.deepcopy(v) for k, v in tables.items()}
        self.fail_tables: set[str] = set()

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    # convenience for assertions
    def rows(self, name: str) -> list[dict]:
        return self.tables.get(name, [])

    def events(self, event_type: str | None = None) -> list[dict]:
        rows = self.rows("audit_events")
        return [r for r in rows if event_type is None or r.get("event_type") == event_type]
