"""Read-only Supabase preview migration replay preflight.

No DB connection, credential handling, migration writes, or network requests.
Inputs are an aggregate metadata snapshot and a checked-out migration directory.
Exit 2 means the canonical migration history cannot be trusted to replay.

Usage:
  python backend/scripts/audit_supabase_preview_replay.py \
      --snapshot docs/ops/finance_ar_replay_observation_20261118.json \
      --migrations supabase/migrations --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

MIGRATION_NAME = re.compile(r"^(\d{14})_[a-z0-9_]+\.sql$")
REQUIRED_BASELINE = ("companies", "users", "stock_warehouses")
SUCCESS_STATES = {"FUNCTIONS_DEPLOYED", "MIGRATIONS_APPLIED", "READY", "SUCCESS"}


def _git_blob_sha(content: bytes) -> str:
    """Match GitHub's blob ID; this is a reproducibility marker, not a secret."""
    header = b"blob " + str(len(content)).encode("ascii") + b"\x00"
    return hashlib.sha1(header + content).hexdigest()


def scan_migrations(directory: Path) -> tuple[set[str], dict[str, str], list[str]]:
    if not directory.is_dir():
        raise ValueError("Migration directory does not exist")
    versions: set[str] = set()
    hashes: dict[str, str] = {}
    errors: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if not match:
            errors.append("invalid_migration_filename:" + path.name)
            continue
        version = match.group(1)
        if version in versions:
            errors.append("duplicate_migration_version:" + version)
        versions.add(version)
        content = path.read_bytes()
        if len(content) > 5 * 1024 * 1024:
            errors.append("oversized_migration_file:" + version)
            continue
        hashes[version] = _git_blob_sha(content)
    return versions, hashes, errors


def _versions(items: Any, label: str) -> set[str]:
    if not isinstance(items, list) or not all(
        isinstance(item, str) and re.fullmatch(r"\d{14}", item)
        for item in items
    ):
        raise ValueError(label + " must be a list of 14-digit versions")
    if len(set(items)) != len(items):
        raise ValueError(label + " contains duplicate versions")
    return set(items)


def _fingerprints(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(version, str) or not re.fullmatch(r"\d{14}", version)
        or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
        for version, sha in value.items()
    ):
        raise ValueError("linked_git_blob_sha must map versions to Git blob IDs")
    return value


def diagnose(snapshot: dict[str, Any], candidate_migrations: Path) -> dict[str, Any]:
    """Return source-only evidence. Never extrapolate source parity into a pass."""
    if not isinstance(snapshot, dict):
        raise ValueError("Metadata snapshot must be a JSON object")
    main = _versions(snapshot.get("main_applied"), "main_applied")
    linked = _versions(snapshot.get("linked_git_versions"), "linked_git_versions")
    branch = _versions(snapshot.get("branch_applied"), "branch_applied")
    main_without_sql = _versions(
        snapshot.get("main_versions_without_sql"), "main_versions_without_sql"
    )
    branch_without_sql = _versions(
        snapshot.get("branch_versions_without_sql"), "branch_versions_without_sql"
    )
    if not main_without_sql.issubset(main):
        raise ValueError("main_versions_without_sql must belong to main_applied")
    if not branch_without_sql.issubset(branch):
        raise ValueError("branch_versions_without_sql must belong to branch_applied")
    linked_hashes = _fingerprints(snapshot.get("linked_git_blob_sha"))
    candidate, candidate_hashes, errors = scan_migrations(candidate_migrations)
    linked_hash_missing = sorted(linked - linked_hashes.keys())
    linked_hash_extra = sorted(linked_hashes.keys() - linked)
    linked_content_mismatch = sorted(
        version for version in linked & candidate & linked_hashes.keys()
        if linked_hashes[version] != candidate_hashes.get(version)
    )

    public_count = snapshot.get("branch_public_tables")
    main_count = snapshot.get("main_public_tables")
    if type(public_count) is not int or public_count < 0:
        raise ValueError("branch_public_tables must be a nonnegative integer")
    if type(main_count) is not int or main_count < 0:
        raise ValueError("main_public_tables must be a nonnegative integer")

    relations = snapshot.get("branch_relations")
    if not isinstance(relations, dict) or any(
        relation not in relations or type(relations[relation]) is not bool
        for relation in REQUIRED_BASELINE
    ):
        raise ValueError("branch_relations must include boolean baseline relations")
    missing_relations = [
        relation for relation in REQUIRED_BASELINE if not relations[relation]
    ]

    main_not_in_candidate = sorted(main - candidate)
    main_not_in_linked = sorted(main - linked)
    linked_not_in_main = sorted(linked - main)
    linked_not_in_candidate = sorted(linked - candidate)
    candidate_not_in_linked = sorted(candidate - linked)
    main_not_in_branch = sorted(main - branch)
    branch_not_in_main = sorted(branch - main)
    candidate_not_applied = sorted(candidate - main)
    candidate_not_in_branch = sorted(candidate - branch)
    branch_not_in_candidate = sorted(branch - candidate)

    if main_count > 0 and public_count == 0 and branch:
        errors.append("applied_history_without_public_schema")
    if main_without_sql:
        errors.append("main_migration_history_missing_sql_definitions")
    if branch_without_sql:
        errors.append("branch_migration_history_missing_sql_definitions")
    if missing_relations:
        errors.append("missing_required_baseline_relations")
    if public_count != main_count:
        errors.append("public_table_count_mismatch")
    if main_not_in_branch or branch_not_in_main:
        errors.append("branch_migration_versions_not_equal_to_main")
    if candidate_not_in_branch or branch_not_in_candidate:
        errors.append("preview_migration_versions_not_equal_to_candidate")
    if main_not_in_candidate:
        errors.append("main_migration_versions_missing_in_candidate")
    if main_not_in_linked:
        errors.append("main_migration_versions_missing_in_linked_git")
    if linked_not_in_main:
        errors.append("linked_git_versions_not_in_main")
    if linked_hash_missing or linked_hash_extra:
        errors.append("linked_git_fingerprint_coverage_incomplete")
    if linked_content_mismatch:
        errors.append("linked_git_sql_content_differs_from_candidate")
    if linked_not_in_candidate or candidate_not_in_linked:
        errors.append("linked_git_history_not_equal_to_candidate")
    if snapshot.get("branch_status") not in SUCCESS_STATES:
        errors.append("branch_not_migration_ready")

    return {
        "ready_for_staging": not errors,
        "issues": sorted(set(errors)),
        "counts": {
            "main_applied": len(main),
            "main_without_stored_sql": len(main_without_sql),
            "main_public_tables": main_count,
            "linked_git_files": len(linked),
            "candidate_migration_files": len(candidate),
            "branch_applied": len(branch),
            "branch_without_stored_sql": len(branch_without_sql),
            "branch_public_tables": public_count,
        },
        "missing_baseline_relations": missing_relations,
        "main_versions_without_sql": sorted(main_without_sql),
        "branch_versions_without_sql": sorted(branch_without_sql),
        "main_versions_missing_in_candidate": main_not_in_candidate,
        "main_versions_missing_in_linked_git": main_not_in_linked,
        "linked_git_versions_not_in_main": linked_not_in_main,
        "linked_git_fingerprint_missing_versions": linked_hash_missing,
        "linked_git_fingerprint_extra_versions": linked_hash_extra,
        "linked_git_sql_content_mismatch_versions": linked_content_mismatch,
        "linked_git_versions_not_in_candidate": linked_not_in_candidate,
        "candidate_versions_not_in_linked_git": candidate_not_in_linked,
        "main_versions_missing_in_branch": main_not_in_branch,
        "branch_versions_not_in_main": branch_not_in_main,
        "candidate_versions_missing_in_branch": candidate_not_in_branch,
        "branch_versions_not_in_candidate": branch_not_in_candidate,
        "candidate_versions_not_in_main_history": candidate_not_applied,
        "branch_status": snapshot.get("branch_status"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--migrations", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    try:
        observation = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report = diagnose(observation, args.migrations)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if args.strict and not report["ready_for_staging"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
