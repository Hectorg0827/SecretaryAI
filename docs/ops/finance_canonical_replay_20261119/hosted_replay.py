#!/usr/bin/env python3
"""Generate atomic, version-preserving SQL for disposable-only canonical replay.

Never connects to a database or mutates canonical migration source files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from hosted_preflight import EXPECTED_BASELINE_VERSIONS, validate_disposable_ref  # noqa: E402

_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_\x80-\xff][A-Za-z0-9_$\x80-\xff]*)?\$")
_FORBIDDEN_TOP_LEVEL_PATTERNS = (
    (re.compile(r"\b(?:ROLLBACK|ABORT|SAVEPOINT|RELEASE\s+SAVEPOINT)\b", re.I), "explicit rollback/savepoint"),
    (re.compile(r"\b(?:CREATE|DROP|REINDEX)\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY\b", re.I), "CONCURRENTLY index DDL"),
    (re.compile(r"^\s*(?:VACUUM|CLUSTER)\b", re.I), "non-transactional maintenance command"),
    (re.compile(r"\bALTER\s+TYPE\b[\s\S]*\bADD\s+VALUE\b", re.I), "ALTER TYPE ... ADD VALUE"),
    (re.compile(r"^\s*(?:CREATE|DROP)\s+DATABASE\b", re.I), "database-level DDL"),
    (re.compile(r"^\s*(?:SET\s+(?:LOCAL\s+|SESSION\s+)?ROLE|RESET\s+ROLE)\b", re.I), "top-level role switch"),
    (re.compile(r"^\s*SET\s+(?:SESSION\s+)?search_path\b", re.I), "non-LOCAL session search_path mutation"),
)


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _strip_leading_comments_and_ws(stmt: str) -> str:
    """Remove leading whitespace, -- line comments, and nested /* */ block comments."""
    i = 0
    n = len(stmt)
    while i < n:
        if stmt[i].isspace():
            i += 1
        elif stmt.startswith("--", i):
            nl = stmt.find("\n", i + 2)
            i = n if nl == -1 else nl + 1
        elif stmt.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth > 0:
                if stmt.startswith("/*", i):
                    depth += 1
                    i += 2
                elif stmt.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth != 0:
                raise ValueError("Unterminated block comment in SQL")
        else:
            break
    return stmt[i:].strip()


def split_top_level_sql_statements(source: str, filename: str = "<sql>") -> list[str]:
    """Split PostgreSQL source into top-level semicolon-terminated statements.

    Respects -- comments, nested /* */ comments, single-quoted and E'' strings,
    double-quoted identifiers, and $tag$...$tag$ dollar-quoted blocks.
    """
    statements: list[str] = []
    start = 0
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "-" and source.startswith("--", i):
            nl = source.find("\n", i + 2)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "/" and source.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth > 0:
                if source.startswith("/*", i):
                    depth += 1
                    i += 2
                elif source.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth != 0:
                raise ValueError(f"{filename}: unterminated block comment")
            continue
        if ch == "'":
            escaped = i > 0 and source[i - 1] in ("e", "E") and (i < 2 or not (source[i - 2].isalnum() or source[i - 2] == "_"))
            i += 1
            while i < n:
                if escaped and source[i] == "\\":
                    i += 2
                    continue
                if source[i] == "'":
                    if i + 1 < n and source[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                raise ValueError(f"{filename}: unterminated single-quoted string")
            continue
        if ch == '"':
            i += 1
            while i < n:
                if source[i] == '"':
                    if i + 1 < n and source[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                raise ValueError(f"{filename}: unterminated double-quoted identifier")
            continue
        if ch == "$":
            m = _DOLLAR_TAG_RE.match(source, i)
            if m:
                tag = m.group(0)
                end_idx = source.find(tag, m.end())
                if end_idx == -1:
                    raise ValueError(f"{filename}: unterminated dollar-quoted block {tag}")
                i = end_idx + len(tag)
                continue
        if ch == ";":
            raw_stmt = source[start:i]
            cleaned = _strip_leading_comments_and_ws(raw_stmt)
            if cleaned:
                statements.append(cleaned)
            start = i + 1
        i += 1

    trailing = _strip_leading_comments_and_ws(source[start:])
    if trailing:
        raise ValueError(f"{filename}: trailing SQL statement missing terminating semicolon")
    if not statements:
        raise ValueError(f"{filename}: migration contains zero executable SQL statements")
    return statements


def _is_tx_begin(cleaned_stmt: str) -> bool:
    return bool(re.fullmatch(r"(?:BEGIN(?:\s+(?:WORK|TRANSACTION))?|START\s+TRANSACTION)", cleaned_stmt, re.I))


def _is_tx_commit(cleaned_stmt: str) -> bool:
    return bool(re.fullmatch(r"(?:COMMIT|END)(?:\s+(?:WORK|TRANSACTION))?", cleaned_stmt, re.I))


def prepare_transactional_sql(source: str, filename: str = "<sql>") -> str:
    """Return executable SQL safe to embed inside an outer BEGIN ... COMMIT block.

    Strips balanced outer BEGIN;/COMMIT; file wrappers while rejecting any inner
    transaction control or non-transactional PostgreSQL DDL.
    """
    statements = split_top_level_sql_statements(source, filename=filename)
    first_is_begin = _is_tx_begin(statements[0])
    last_is_commit = _is_tx_commit(statements[-1])
    if first_is_begin != last_is_commit:
        raise ValueError(
            f"{filename}: unbalanced top-level transaction wrapper "
            f"(begin={first_is_begin}, commit={last_is_commit})"
        )
    if first_is_begin and last_is_commit:
        statements = statements[1:-1]
        if not statements:
            raise ValueError(f"{filename}: empty transaction wrapper")

    for idx, stmt in enumerate(statements, start=1):
        if _is_tx_begin(stmt) or _is_tx_commit(stmt) or re.match(
            r"^(?:BEGIN|START\s+TRANSACTION|COMMIT|END)\b", stmt, re.I
        ):
            raise ValueError(f"{filename}: nested top-level transaction statement #{idx} is forbidden")
        for pattern, reason in _FORBIDDEN_TOP_LEVEL_PATTERNS:
            if pattern.search(stmt):
                raise ValueError(f"{filename}: statement #{idx} contains forbidden {reason}")

    return "\n\n".join(stmt + ";" for stmt in statements) + "\n"


def generate_replay_sql(project_ref: str) -> str:
    validated_ref = validate_disposable_ref(project_ref)
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(
            [sys.executable, str(BASE / "materialize.py"), "--target", temp],
            check=True,
        )
        rows = json.loads((BASE / "manifest.json").read_text())["migrations"]
        baseline_set = set(EXPECTED_BASELINE_VERSIONS)
        if [r["version"] for r in rows[:5]] != list(EXPECTED_BASELINE_VERSIONS):
            raise RuntimeError("Canonical manifest first 5 versions do not match expected baseline")

        versions_sql = ",".join(literal(v) for v in EXPECTED_BASELINE_VERSIONS)
        ref_lit = literal(validated_ref)
        sql: list[str] = [
            "BEGIN;",
            "SET LOCAL lock_timeout = '5s';",
            "SELECT pg_catalog.set_config('app.replay_outer_xid', pg_catalog.pg_current_xact_id()::text, true);",
            "LOCK TABLE supabase_migrations.schema_migrations IN ACCESS EXCLUSIVE MODE;",
            f"""DO $preflight_guard$
BEGIN
  IF current_setting('app.disposable_project_ref', true) IS DISTINCT FROM {ref_lit} THEN
    RAISE EXCEPTION 'Disposable target marker missing or mismatched for %', {ref_lit};
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public') THEN
    RAISE EXCEPTION 'Target not empty: public tables already exist';
  END IF;
  IF EXISTS (SELECT 1 FROM auth.users) THEN
    RAISE EXCEPTION 'Target has users';
  END IF;
  IF (SELECT count(*) FROM supabase_migrations.schema_migrations) <> 5
     OR EXISTS (
       SELECT 1 FROM supabase_migrations.schema_migrations
       WHERE version NOT IN ({versions_sql})
          OR coalesce(cardinality(statements), 0) <> 0
     ) THEN
    RAISE EXCEPTION 'Unexpected history: target must have exact 5 empty baseline rows';
  END IF;
END $preflight_guard$;""",
        ]

        expected_lengths: list[str] = []
        for row in rows:
            filename = row["filename"]
            version = row["version"]
            name = filename[15:-4]
            raw_bytes = (Path(temp) / "supabase" / "migrations" / filename).read_bytes()
            source = raw_bytes.decode("utf-8")
            executable_sql = prepare_transactional_sql(source, filename=filename)
            sql.append(f"-- Canonical migration {version} ({name})")
            sql.append(executable_sql.rstrip("\n"))

            ver_lit = literal(version)
            name_lit = literal(name)
            src_lit = literal(source)
            expected_lengths.append(f"({ver_lit}, {len(raw_bytes)})")

            if version in baseline_set:
                sql.append(
                    f"""DO $ledger_baseline_{version}$
DECLARE updated_rows integer;
BEGIN
  UPDATE supabase_migrations.schema_migrations
     SET name = {name_lit},
         statements = ARRAY[{src_lit}]
   WHERE version = {ver_lit}
     AND coalesce(cardinality(statements), 0) = 0;
  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  IF updated_rows <> 1 THEN
    RAISE EXCEPTION 'Expected exactly 1 empty baseline row for version %, updated %', {ver_lit}, updated_rows;
  END IF;
END $ledger_baseline_{version}$;"""
                )
            else:
                sql.append(
                    "INSERT INTO supabase_migrations.schema_migrations(version, name, statements) "
                    f"VALUES ({ver_lit}, {name_lit}, ARRAY[{src_lit}]);"
                )

        sql.append((BASE / "assert_schema.sql").read_text().rstrip("\n"))
        values_clause = ",\n    ".join(expected_lengths)
        sql.append(
            f"""DO $provenance_and_tx_guard$
DECLARE
  empty_rows integer;
  mismatched_rows integer;
BEGIN
  IF current_setting('app.replay_outer_xid', true) IS DISTINCT FROM pg_catalog.pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'Outer transaction continuity broken during canonical replay';
  END IF;
  SELECT count(*) INTO empty_rows
    FROM supabase_migrations.schema_migrations
   WHERE coalesce(cardinality(statements), 0) <> 1
      OR statements[1] IS NULL
      OR octet_length(statements[1]) <= 20;
  IF empty_rows <> 0 THEN
    RAISE EXCEPTION 'Migration provenance incomplete: % rows without single statement body', empty_rows;
  END IF;
  WITH expected(version, expected_bytes) AS (
    VALUES
    {values_clause}
  )
  SELECT count(*) INTO mismatched_rows
    FROM expected e
     FULL OUTER JOIN supabase_migrations.schema_migrations sm USING (version)
   WHERE sm.version IS NULL
      OR e.version IS NULL
      OR octet_length(sm.statements[1]) <> e.expected_bytes;
  IF mismatched_rows <> 0 THEN
    RAISE EXCEPTION 'Migration provenance byte-length mismatch on % rows', mismatched_rows;
  END IF;
  RAISE NOTICE 'PASS: single-transaction continuity and 46-row SQL byte provenance verified';
END $provenance_and_tx_guard$;"""
        )
        sql.append("COMMIT;")
        return "\n".join(sql) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate disposable-only atomic canonical replay SQL.")
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sql_text = generate_replay_sql(args.project_ref)
    with args.output.open("x", encoding="utf-8") as f:
        f.write(sql_text)


if __name__ == "__main__":
    main()
