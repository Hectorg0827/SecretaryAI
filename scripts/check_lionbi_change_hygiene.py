#!/usr/bin/env python3
"""Fast, local-only change-hygiene gate for Lion BI pull requests.

Does not connect to databases or external providers. This is a preliminary
gate; it never substitutes for the full CI, CodeQL, hosted acceptance, or
human release approval.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

ACTIVE_ROOTS = ("supabase/migrations/", "backend/supabase/migrations/", "staging_supabase/supabase/migrations/")
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{14})_[a-z0-9_]+\.sql$")


def check_changes(
    changes: list[tuple[str, str]], root: Path,
    *, base_paths: list[str] | None = None,
) -> tuple[list[str], set[str]]:
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
        relative_name = path[len(active_root):]
        if "/" in relative_name:
            errors.append(
                f"{path}: migration SQL must be a direct child of {active_root}"
            )
            continue
        name = Path(path).name
        match = MIGRATION_NAME.fullmatch(name)
        if not match:
            errors.append(f"{path}: added migration must use YYYYMMDDHHMMSS_description.sql")
            continue
        version = match.group("version")
        try:
            parsed = datetime.strptime(version, "%Y%m%d%H%M%S")
            if parsed.strftime("%Y%m%d%H%M%S") != version:
                raise ValueError("non-canonical timestamp")
        except ValueError:
            errors.append(f"{path}: invalid YYYYMMDDHHMMSS migration timestamp")
            continue
        # A branch can pass against its own checkout while the base changes.
        # Compare new versions with *the latest remote base tree*, not merely
        # the merge-base diff; still require serialized pre-merge validation.
        if base_paths is not None:
            base_conflicts = sorted(
                item for item in base_paths
                if item.startswith(active_root)
                and "/" not in item[len(active_root):]
                and item[len(active_root):].startswith(version + "_")
                and item.endswith(".sql")
            )
            if base_conflicts:
                errors.append(
                    f"{path}: latest base already contains migration version "
                    f"{version} in {active_root}: " + ", ".join(base_conflicts)
                )
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
    parser.add_argument("--changes", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--base-files", type=Path,
        help="Latest fetched base branch file list (git ls-tree -r --name-only)",
    )
    args = parser.parse_args()

    changes = parse_change_list(args.changes.read_text(encoding="utf-8"))
    base_paths = (
        args.base_files.read_text(encoding="utf-8").splitlines()
        if args.base_files is not None else None
    )
    errors, touched = check_changes(changes, args.root, base_paths=base_paths)
    print(f"PR fast preflight: {len(changes)} changed files; surfaces: {', '.join(sorted(touched)) or 'none'}")
    if errors:
        for error in errors:
            print("BLOCKED: " + error)
        print("This fast check is not approval to edit historical applied migrations.")
        return 1
    print("PASS: additive migration hygiene. Full CI, source parity and security review still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
