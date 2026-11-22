#!/usr/bin/env python3
"""Fast, local-only change-hygiene gate for Lion BI pull requests.

Does not connect to databases or external providers. This is a preliminary
gate; it never substitutes for the full CI, CodeQL, hosted acceptance, or
human release approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import re
from pathlib import Path

ACTIVE_ROOTS = ("supabase/migrations/", "backend/supabase/migrations/", "staging_supabase/supabase/migrations/")
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{14})_[a-z0-9_]+\.sql$")


def check_changes(changes: list[tuple[str, str]], root: Path) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    touched: set[str] = set()

    for status, path in changes:
        path = path.removeprefix("./")
        if path.startswith("backend/"):
            touched.add("backend")
        if path.startswith("frontend/"):
            touched.add("frontend")
        if path.startswith("docs/"):
            touched.add("docs")
        if "lola" in path.lower():
            touched.add("lola")

        active_root = next((prefix for prefix in ACTIVE_ROOTS if path.startswith(prefix)), None)
        if not active_root or not path.endswith(".sql"):
            continue
        touched.add("active-migrations")
        operation = status[0].upper() if status else "?"
        if operation in {"M", "D", "R", "T"}:
            errors.append(
                f"{path}: historical migration modified/deleted/renamed; "
                "use a newly versioned additive migration and independent schema review"
            )
            continue
        if operation != "A":
            continue
        relative_path = path[len(active_root):]
        if "/" in relative_path:
            errors.append(f"{path}: migration SQL must be a direct child of {active_root}")
            continue
        name = Path(path).name
        match = MIGRATION_NAME.fullmatch(name)
        if not match:
            errors.append(f"{path}: added migration must use YYYYMMDDHHMMSS_description.sql")
            continue
        version = match.group("version")
        try:
            datetime.strptime(version, "%Y%m%d%H%M%S")
        except ValueError:
            errors.append(f"{path}: migration timestamp is not a real calendar date/time")
            continue
        migrations_dir = root / active_root
        if migrations_dir.is_dir():
            conflicts = sorted(
                item.name for item in migrations_dir.glob("*.sql")
                if item.name != name and item.name.startswith(version + "_")
            )
            if conflicts:
                errors.append(
                    f"{path}: duplicate version {version} within {active_root}: "
                    + ", ".join(conflicts)
                )
    return errors, touched



def check_migration_tree(root: Path) -> list[str]:
    """Verify version uniqueness across each complete integration checkout.

    Checking all migration files (not just the PR diff) makes a merge-queue
    synthetic merge detect colliding versions from independently green PRs.
    A passing PR-head check is not a substitute for merge-queue enforcement.
    """
    errors: list[str] = []
    for active_root in ACTIVE_ROOTS:
        migration_dir = root / active_root
        if not migration_dir.is_dir():
            continue
        versions: dict[str, list[str]] = {}
        for sql in sorted(migration_dir.glob("*.sql")):
            match = MIGRATION_NAME.fullmatch(sql.name)
            if not match:
                errors.append(f"{active_root}{sql.name}: malformed migration name")
                continue
            version = match.group("version")
            try:
                datetime.strptime(version, "%Y%m%d%H%M%S")
            except ValueError:
                errors.append(f"{active_root}{sql.name}: invalid migration timestamp")
            versions.setdefault(version, []).append(sql.name)
        for version, names in sorted(versions.items()):
            if len(names) > 1:
                errors.append(
                    f"{active_root}: duplicate migration version {version}: "
                    + ", ".join(names)
                )
    return errors


def parse_change_list(value: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("Unexpected git diff --name-status input")
        result.append((parts[0], parts[1]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changes", type=Path)
    parser.add_argument("--check-tree", action="store_true", help="Validate entire integration checkout")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    if not args.changes and not args.check_tree:
        parser.error("specify --changes and/or --check-tree")
    changes = parse_change_list(args.changes.read_text(encoding="utf-8")) if args.changes else []
    errors, touched = check_changes(changes, args.root)
    if args.check_tree:
        errors.extend(check_migration_tree(args.root))
    print(f"PR fast preflight: {len(changes)} changed files; surfaces: {', '.join(sorted(touched)) or 'none'}")
    if errors:
        for error in errors:
            print("BLOCKED: " + error)
        print("This fast check is not approval to edit historical applied migrations.")
        return 1
    print("PASS: migration hygiene. Merge queue, full CI and independent review still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
