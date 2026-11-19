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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MIGRATION_NAME = re.compile(r"^(\d{14})_[a-z0-9_]+\.sql$")
REQUIRED_BASELINE = ("companies", "users", "stock_warehouses")
SCHEMA_OBJECT_KINDS = ("tables", "views", "sequences", "functions", "triggers", "rls_policies")
SCHEMA_DIGEST_KINDS = (
    "columns", "constraints", "indexes", "functions",
    "triggers", "policies", "rls_flags", "grants",
    "views", "sequences", "sequence_acls",
)
MIGRATION_SUCCESS_STATES = {"MIGRATIONS_PASSED", "FUNCTIONS_DEPLOYED", "MIGRATIONS_APPLIED", "READY", "SUCCESS"}
STAGING_SUCCESS_STATES = {"FUNCTIONS_DEPLOYED", "READY", "SUCCESS"}
IDENTITY_FIELDS = ("source_main_project_ref", "source_preview_project_ref", "linked_github_branch", "candidate_commit")


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
        if path.stat().st_size > 5 * 1024 * 1024:
            errors.append("oversized_migration_file:" + version)
            continue
        content = path.read_bytes()
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


def _schema_object_counts(value: Any, label: str) -> dict[str, int]:
    if (not isinstance(value, dict)
            or set(value) != set(SCHEMA_OBJECT_KINDS)
            or any(type(value[k]) is not int or value[k] < 0
                   for k in SCHEMA_OBJECT_KINDS)):
        raise ValueError(label + " must specify nonnegative public object counts")
    return value


def _schema_digests(value: Any, label: str) -> dict[str, str]:
    if (not isinstance(value, dict)
            or set(value) != set(SCHEMA_DIGEST_KINDS)
            or any(not isinstance(value[k], str)
                   or not re.fullmatch(r"[0-9a-f]{32}", value[k])
                   for k in SCHEMA_DIGEST_KINDS)):
        raise ValueError(label + " must specify all required lowercase MD5 values")
    return value


def _snapshot_issues(snapshot: dict[str, Any], expected_identity: dict[str, str] | None) -> list[str]:
    issues: list[str] = []
    observed = snapshot.get("observed_at")
    if not isinstance(observed, str):
        issues.append("utc_observation_timestamp_missing")
    else:
        try:
            moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                issues.append("observation_timestamp_not_utc")
            elif not timedelta(0) <= datetime.now(timezone.utc) - moment <= timedelta(hours=1):
                issues.append("stale_or_future_observation")
        except ValueError:
            issues.append("observation_timestamp_invalid")
    for field in IDENTITY_FIELDS:
        value = snapshot.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append("snapshot_identity_missing:" + field)
        elif expected_identity is not None and expected_identity.get(field) != value:
            issues.append("snapshot_identity_mismatch:" + field)
    return issues


def _grant_exception_accepted(
    snapshot: dict[str, Any], branch_digest: str, reviewed_url: str | None,
) -> bool:
    exception = snapshot.get("grant_review")
    return bool(
        reviewed_url
        and isinstance(exception, dict)
        and exception.get("review_url") == reviewed_url
        and reviewed_url.startswith("https://github.com/")
        and isinstance(exception.get("reviewer"), str)
        and bool(exception["reviewer"].strip())
        and exception.get("approved_branch_grants_md5") == branch_digest
    )


def diagnose(
    snapshot: dict[str, Any], candidate_migrations: Path,
    *, expected_identity: dict[str, str] | None = None,
    approved_grant_review_url: str | None = None,
) -> dict[str, Any]:
    """Return source-only evidence. Never extrapolate source parity into a pass."""
    if not isinstance(snapshot, dict):
        raise ValueError("Metadata snapshot must be a JSON object")
    observation_issues = _snapshot_issues(snapshot, expected_identity)
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
    # Git blob IDs are computed over exact UTF-8 file bytes. Store the SHA of
    # archived main SQL only when it can be reconstructed identically; absent
    # or mismatched proof blocks readiness rather than guessing equivalence.
    main_hashes = _fingerprints(snapshot.get("main_sql_blob_sha", {}))
    candidate, candidate_hashes, errors = scan_migrations(candidate_migrations)
    errors.extend(observation_issues)
    main_hash_missing = sorted((main - main_without_sql) - main_hashes.keys())
    main_hash_extra = sorted(main_hashes.keys() - (main - main_without_sql))
    main_sql_mismatch = sorted(
        v for v in (main - main_without_sql) & candidate & main_hashes.keys()
        if main_hashes[v] != candidate_hashes.get(v)
    )
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
    main_objects = _schema_object_counts(
        snapshot.get("main_schema_objects"), "main_schema_objects"
    )
    branch_objects = _schema_object_counts(
        snapshot.get("branch_schema_objects"), "branch_schema_objects"
    )
    if (main_objects["tables"] != main_count
            or branch_objects["tables"] != public_count):
        raise ValueError("schema object table count contradicts public table count")
    main_digests = _schema_digests(
        snapshot.get("main_schema_digests"), "main_schema_digests"
    )
    branch_digests = _schema_digests(
        snapshot.get("branch_schema_digests"), "branch_schema_digests"
    )
    # A pre-production candidate can intentionally add migrations. Requiring
    # preview == main would reject those changes, while comparing only counts
    # would accept unverified SQL. Require a separately observed expected schema.
    candidate_is_extended = bool(candidate - main)
    if candidate_is_extended:
        if "candidate_schema_objects" not in snapshot or "candidate_schema_digests" not in snapshot:
            errors.append("candidate_schema_expectations_missing")
            expected_objects, expected_digests = main_objects, main_digests
        else:
            expected_objects = _schema_object_counts(
                snapshot["candidate_schema_objects"], "candidate_schema_objects"
            )
            expected_digests = _schema_digests(
                snapshot["candidate_schema_digests"], "candidate_schema_digests"
            )
    else:
        expected_objects, expected_digests = main_objects, main_digests
    digest_drift = [
        kind for kind in SCHEMA_DIGEST_KINDS
        if expected_digests[kind] != branch_digests[kind]
    ]
    grant_exception_accepted = _grant_exception_accepted(
        snapshot, branch_digests["grants"], approved_grant_review_url
    )
    if grant_exception_accepted and "grants" in digest_drift:
        digest_drift.remove("grants")
    grant_differs_from_main = main_digests["grants"] != branch_digests["grants"]

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
    if public_count != expected_objects["tables"]:
        errors.append("public_table_count_mismatch")
    if expected_objects != branch_objects:
        errors.append("public_schema_object_count_mismatch")
    if digest_drift:
        errors.append("public_schema_definition_or_grant_drift")
    # Main history must be preserved, but verified new candidate versions are
    # allowed on a preview and do not need to exist on production main.
    if main_not_in_branch or (branch_not_in_main and not candidate_is_extended):
        errors.append("branch_migration_versions_not_equal_to_main")
    if candidate_not_in_branch or branch_not_in_candidate:
        errors.append("preview_migration_versions_not_equal_to_candidate")
    if main_not_in_candidate:
        errors.append("main_migration_versions_missing_in_candidate")
    if main_not_in_linked:
        errors.append("main_migration_versions_missing_in_linked_git")
    if linked_not_in_main and not candidate_is_extended:
        errors.append("linked_git_versions_not_in_main")
    if linked_hash_missing or linked_hash_extra:
        errors.append("linked_git_fingerprint_coverage_incomplete")
    if main_hash_missing or main_hash_extra:
        errors.append("main_stored_sql_fingerprint_coverage_incomplete")
    if main_sql_mismatch:
        errors.append("main_stored_sql_content_differs_from_candidate")
    if linked_content_mismatch:
        errors.append("linked_git_sql_content_differs_from_candidate")
    if linked_not_in_candidate or candidate_not_in_linked:
        errors.append("linked_git_history_not_equal_to_candidate")
    status = snapshot.get("branch_status")
    if not isinstance(status, str):
        raise ValueError("branch_status must be a string")
    if status not in MIGRATION_SUCCESS_STATES:
        errors.append("branch_not_migration_ready")
    elif status not in STAGING_SUCCESS_STATES:
        errors.append("branch_functions_not_deployed")
    if grant_differs_from_main and not grant_exception_accepted:
        errors.append("branch_grant_difference_requires_independent_approval")

    return {
        "ready_for_staging": not errors,
        "migration_replay_reported_success": status in MIGRATION_SUCCESS_STATES,
        "grant_exception_accepted": grant_exception_accepted,
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
        "main_schema_objects": main_objects,
        "branch_schema_objects": branch_objects,
        "mismatched_schema_definition_or_grant_kinds": digest_drift,
        "mismatched_schema_object_kinds": [
            kind for kind in SCHEMA_OBJECT_KINDS
            if expected_objects[kind] != branch_objects[kind]
        ],
        "main_versions_without_sql": sorted(main_without_sql),
        "branch_versions_without_sql": sorted(branch_without_sql),
        "main_versions_missing_in_candidate": main_not_in_candidate,
        "main_versions_missing_in_linked_git": main_not_in_linked,
        "linked_git_versions_not_in_main": linked_not_in_main,
        "linked_git_fingerprint_missing_versions": linked_hash_missing,
        "linked_git_fingerprint_extra_versions": linked_hash_extra,
        "linked_git_sql_content_mismatch_versions": linked_content_mismatch,
        "main_sql_fingerprint_missing_versions": main_hash_missing,
        "main_sql_fingerprint_extra_versions": main_hash_extra,
        "main_sql_content_mismatch_versions": main_sql_mismatch,
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
    parser.add_argument("--expect-main-project")
    parser.add_argument("--expect-preview-project")
    parser.add_argument("--expect-linked-git")
    parser.add_argument("--expect-candidate-commit")
    parser.add_argument("--approved-grant-review-url")
    args = parser.parse_args(argv)

    try:
        observation = json.loads(args.snapshot.read_text(encoding="utf-8"))
        binding = {
            "source_main_project_ref": args.expect_main_project,
            "source_preview_project_ref": args.expect_preview_project,
            "linked_github_branch": args.expect_linked_git,
            "candidate_commit": args.expect_candidate_commit,
        } if args.strict else None
        report = diagnose(
            observation, args.migrations, expected_identity=binding,
            approved_grant_review_url=args.approved_grant_review_url,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if args.strict and not report["ready_for_staging"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
