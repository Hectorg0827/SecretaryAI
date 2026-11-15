#!/usr/bin/env python3
"""Fail-closed static audit of migration-defined PostgreSQL functions.

Resolve CREATE OR REPLACE and ALTER FUNCTION in lexicographic migration order.
Ignore semicolons inside dollar-quoted PL/pgSQL bodies. No database is touched.
"""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
NAME = r'(?:(?P<schema>public|secretary_internal)\.)?(?P<name>[a-z_][a-z_0-9]*)'
CREATE = re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+' + NAME + r'\s*\(', re.I)
ALTER = re.compile(r'\bALTER\s+FUNCTION\s+' + NAME + r'\s*\(', re.I)
PERMISSION = re.compile(r'\b(REVOKE|GRANT)\s+(?:ALL|EXECUTE)\s+ON\s+FUNCTION\s+' + NAME + r'\s*\(', re.I)
SAFE_PATH = re.compile(r'\bSET\s+search_path\s*=\s*pg_catalog\s*,\s*(?:public\s*,\s*)?pg_temp\b', re.I)


def statements(source: str):
    """Split SQL at real statement semicolons, skipping comments and bodies."""
    result = []
    current = []
    i = 0
    while i < len(source):
        if source.startswith("--", i):
            j = source.find("\n", i)
            i = len(source) if j < 0 else j
            current.append(" ")
            continue
        if source.startswith("/*", i):
            j = source.find("*/", i + 2)
            if j < 0:
                raise ValueError("Unterminated SQL comment")
            i = j + 2
            current.append(" ")
            continue
        if source[i] == "'":
            start = i
            i += 1
            while i < len(source):
                if source[i] == "'":
                    if i + 1 < len(source) and source[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            current.append(source[start:i])
            continue
        if source[i] == "$":
            match = re.match(r'\$[a-zA-Z_0-9]*\$', source[i:])
            if match:
                delimiter = match.group()
                end = source.find(delimiter, i + len(delimiter))
                if end < 0:
                    raise ValueError("Unterminated SQL dollar quote")
                current.append(" " + delimiter + " [body] " + delimiter + " ")
                i = end + len(delimiter)
                continue
        if source[i] == ";":
            statement = "".join(current).strip()
            if statement:
                result.append(statement)
            current = []
        else:
            current.append(source[i])
        i += 1
    if "".join(current).strip():
        result.append("".join(current).strip())
    return result


def parse_functions():
    found = {}
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        for stmt in statements(migration.read_text(encoding="utf-8")):
            created = CREATE.search(stmt)
            altered = ALTER.search(stmt)
            permission = PERMISSION.search(stmt)
            if created:
                key = ((created.group("schema") or "public").lower(), created.group("name").lower())
                old = found.get(key, {})
                found[key] = {
                    "path": migration.name,
                    "pinned": bool(SAFE_PATH.search(stmt)),
                    "definer": bool(re.search(r'\bSECURITY\s+DEFINER\b', stmt, re.I)),
                    # CREATE OR REPLACE retains existing privileges.
                    "allowed": old.get("allowed", {"PUBLIC", "anon", "authenticated"}).copy(),
                }
            elif altered:
                key = ((altered.group("schema") or "public").lower(), altered.group("name").lower())
                if key not in found:
                    raise AssertionError(f"ALTER FUNCTION targets unknown migration function {key}")
                if re.search(r'\bSET\s+search_path\s*=', stmt, re.I):
                    found[key]["pinned"] = bool(SAFE_PATH.search(stmt))
                    found[key]["path"] = migration.name
            elif permission:
                key = ((permission.group("schema") or "public").lower(), permission.group("name").lower())
                if key not in found:
                    raise AssertionError(f"Function privilege change targets unknown migration function {key}")
                recipient_match = re.search(r'\b(?:FROM|TO)\s+([^;]+)$', stmt, re.I)
                if not recipient_match:
                    raise AssertionError(f"Function privilege change missing recipients {key}")
                recipients = {r.strip().strip('"').lower() for r in recipient_match.group(1).split(",")}
                relevant = {"PUBLIC", "anon", "authenticated"} & {
                    "PUBLIC" if r == "public" else r for r in recipients
                }
                if permission.group(1).upper() == "REVOKE":
                    found[key]["allowed"] -= relevant
                else:
                    found[key]["allowed"] |= relevant
    if not found:
        raise AssertionError("No migration-defined functions were found")
    return found


def verify(found):
    problems = []
    for (schema, name), data in sorted(found.items()):
        if not data["pinned"]:
            problems.append(f"{schema}.{name}: missing safe pinned search_path (latest: {data['path']})")
        if data["definer"] and schema != "secretary_internal" and data["allowed"]:
            problems.append(f"{schema}.{name}: SECURITY DEFINER executable by {sorted(data['allowed'])}")
    if problems:
        raise AssertionError("\n".join(problems))


def sql_check(found):
    rows = ",\n".join(
        f"  ('{schema}', '{name}')" for schema, name in sorted(found)
    )
    return f"""-- Run only on a disposable PostgreSQL database after migrations.
WITH expected(schema_name, function_name) AS (VALUES
{rows}
)
SELECT CASE WHEN bool_and(
    p.oid IS NOT NULL AND
    EXISTS (
        SELECT 1 FROM unnest(coalesce(p.proconfig, ARRAY[]::text[])) AS setting
        WHERE setting ~ '^search_path=pg_catalog, *(public, *)?pg_temp$'
    )
) THEN 'ok' ELSE 'bad' END
FROM expected e
LEFT JOIN pg_namespace n ON n.nspname = e.schema_name
LEFT JOIN pg_proc p ON p.pronamespace = n.oid AND p.proname = e.function_name;
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", action="store_true", help="emit disposable pg_proc validation SQL")
    args = parser.parse_args()
    found = parse_functions()
    verify(found)
    if args.sql:
        print(sql_check(found))
    else:
        print(f"OK: {len(found)} migration functions have safe search_path; SECURITY DEFINER grants checked")


if __name__ == "__main__":
    main()
