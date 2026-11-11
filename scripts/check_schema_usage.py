#!/usr/bin/env python3
"""
Check that every table and column the backend code touches exists in the
database schema.

Why this exists: the code and the schema drifted apart silently (e.g. the code
wrote `password_hash` while the schema had `hashed_password`; audit rows were
written with columns the audit table did not have). Those failures only show
up at runtime, and several were swallowed by "non-fatal" error handling.

How it works: it reads the backend source (no imports, pure syntax tree), finds
every Supabase call chain `db.table("x").insert/update/upsert/select/eq/...`,
collects the column names used, and compares them with the live schema read
through `psql` (standard PG* environment variables).

Usage:
    PGHOST=... PGDATABASE=... python scripts/check_schema_usage.py
Exit code 1 if anything the code uses is missing from the schema.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_DIRS = [ROOT / "backend" / "app", ROOT / "backend" / "tasks"]

WRITE_METHODS = {"insert", "update", "upsert"}
FILTER_METHODS = {
    "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is_", "in_",
    "contains", "order", "not_",
}


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _chain_table(node) -> str | None:
    """Walk down a call chain and return the table name of `.table("x")`."""
    while True:
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                if f.attr == "table" and node.args and _str(node.args[0]):
                    return _str(node.args[0])
                node = f.value
                continue
            return None
        if isinstance(node, ast.Attribute):
            node = node.value
            continue
        return None


class _FunctionDicts(ast.NodeVisitor):
    """Collect dict-literal keys assigned to simple local names in a function."""

    def __init__(self):
        self.keys: dict[str, set[str]] = defaultdict(set)

    def _add(self, name: str, value) -> None:
        for d in _dict_nodes(value):
            for k in d.keys:
                if k is not None and _str(k):
                    self.keys[name].add(_str(k))

    def visit_Assign(self, node):
        for t in node.targets:
            if isinstance(t, ast.Name):
                self._add(t.id, node.value)
            # name["key"] = ...
            if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and _str(t.slice):
                self.keys[t.value.id].add(_str(t.slice))
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name) and node.value is not None:
            self._add(node.target.id, node.value)
        self.generic_visit(node)


def _dict_nodes(value):
    if isinstance(value, ast.Dict):
        yield value
    elif isinstance(value, (ast.List, ast.Tuple)):
        for e in value.elts:
            yield from _dict_nodes(e)
    elif isinstance(value, ast.IfExp):
        yield from _dict_nodes(value.body)
        yield from _dict_nodes(value.orelse)


def _payload_keys(arg, local_dicts: dict[str, set[str]]) -> set[str]:
    keys: set[str] = set()
    for d in _dict_nodes(arg):
        for k, v in zip(d.keys, d.values):
            if k is None:  # {**other}
                if isinstance(v, ast.Name):
                    keys |= local_dicts.get(v.id, set())
            elif _str(k):
                keys.add(_str(k))
    if isinstance(arg, ast.Name):
        keys |= local_dicts.get(arg.id, set())
    return keys


def _select_columns(text: str) -> set[str]:
    cols = set()
    for part in text.split(","):
        part = part.strip()
        if not part or part == "*" or "(" in part or ":" in part:
            continue
        cols.add(part.split("->")[0].strip())
    return cols


def collect_usage() -> dict[str, dict[str, set[str]]]:
    """{table: {column: {"file:line", ...}}}; column '' marks bare table use."""
    usage: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for base in SOURCE_DIRS:
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            rel = path.relative_to(ROOT)
            scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [tree]
            seen: set[int] = set()
            for scope in scopes + [tree]:
                fd = _FunctionDicts()
                fd.visit(scope)
                for node in ast.walk(scope):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                        continue
                    if id(node) in seen:
                        continue
                    table = _chain_table(node.func.value)
                    if table is None:
                        continue
                    seen.add(id(node))
                    where = f"{rel}:{node.lineno}"
                    usage[table][""].add(where)
                    m = node.func.attr
                    if m in WRITE_METHODS and node.args:
                        for k in _payload_keys(node.args[0], fd.keys):
                            usage[table][k].add(where)
                        for kw in node.keywords:
                            if kw.arg == "on_conflict" and _str(kw.value):
                                for c in _str(kw.value).split(","):
                                    usage[table][c.strip()].add(where)
                    elif m == "select" and node.args and _str(node.args[0]):
                        for c in _select_columns(_str(node.args[0])):
                            usage[table][c].add(where)
                    elif m in FILTER_METHODS and node.args and _str(node.args[0]):
                        usage[table][_str(node.args[0]).split("->")[0]].add(where)
    return usage


def schema_columns() -> dict[str, set[str]]:
    out = subprocess.run(
        ["psql", "-At", "-F", "\t", "-c",
         "select table_name, column_name from information_schema.columns where table_schema='public'"],
        check=True, capture_output=True, text=True,
    ).stdout
    cols: dict[str, set[str]] = defaultdict(set)
    for line in out.splitlines():
        t, c = line.split("\t")
        cols[t].add(c)
    return cols


def main() -> int:
    usage = collect_usage()
    schema = schema_columns()
    problems: list[str] = []
    for table in sorted(usage):
        if table not in schema:
            where = sorted(usage[table][""])[0]
            problems.append(f"MISSING TABLE   {table}   (used at {where})")
            continue
        for col in sorted(c for c in usage[table] if c):
            if col not in schema[table]:
                where = sorted(usage[table][col])[0]
                problems.append(f"MISSING COLUMN  {table}.{col}   (used at {where})")
    if problems:
        print("\n".join(problems))
        print(f"\n✗ {len(problems)} place(s) where the code uses something the database does not have.")
        return 1
    print(f"✓ All {len(usage)} tables and their columns used by the code exist in the schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
