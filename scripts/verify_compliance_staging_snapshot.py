#!/usr/bin/env python3
"""Validate point-in-time staging release evidence OFFLINE.

This checker validates the seven pending Compliance SQL files and the sanitized
read-only staging snapshot. It does NOT query the live database, claim current
history, generate a Supabase CLI migration bundle, or approve a deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = Path("docs/compliance/staging_migration_snapshot_20261116.json")
EXPECTED_PENDING = (
    "20261115000000",
    "20261115010000",
    "20261115020000",
    "20261115030000",
    "20261115040000",
    "20261115050000",
    "20261115060000",
)
EXPECTED_POST_BASELINE = (
    "20261102092256",
    "20261102232409",
    "20261103022426",
    "20261108034956",
    "20261111005641",
    "20261111005840",
    "20261111010558",
    "20261112043411",
    "20261114033708",
    "20261114041719",
    "20261114162323",
    "20261114163243",
)
UNMATCHED_GIT_SOURCES = {
    "20261111005641",
    "20261111005840",
    "20261112043411",
}
NO_GO = "NO_GO"


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(
        f"blob {len(data)}\0".encode("ascii") + data,
        usedforsecurity=False,
    ).hexdigest()


def check(root: Path) -> dict:
    root = root.resolve()
    errors: list[str] = []
    path = root / SNAPSHOT
    if not path.is_file():
        return {
            "status": NO_GO,
            "source_checks": "blocked",
            "errors": ["staging_snapshot_missing"],
            "remote_history_refreshed": False,
            "deployment_permitted": False,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {
            "status": NO_GO,
            "source_checks": "blocked",
            "errors": ["staging_snapshot_invalid"],
            "remote_history_refreshed": False,
            "deployment_permitted": False,
        }
    if not isinstance(data, dict):
        errors.append("snapshot_object_invalid")
        data = {}
    if data.get("schema_version") != "lionbi.compliance.staging-read-only-evidence.v1":
        errors.append("unexpected_evidence_schema")
    if data.get("project_ref") != "xerktvdgjvjozvckjyfp" or data.get("project_label") != "SecretaryAI-Staging":
        errors.append("not_secretaryai_staging")
    if data.get("recorded_count") != 45 or data.get("previous_baseline_count") != 33:
        errors.append("recorded_count_invalid")
    if data.get("previous_baseline_version_name_matches") != 33 or data.get("post_baseline_count") != 12:
        errors.append("baseline_count_invalid")

    recorded = data.get("post_baseline_migrations")
    if not isinstance(recorded, list) or len(recorded) != 12:
        errors.append("recorded_history_incomplete")
        recorded = []
    if [x.get("version") for x in recorded if isinstance(x, dict)] != list(EXPECTED_POST_BASELINE):
        errors.append("recorded_versions_out_of_order_or_missing")
    matched = 0
    for row in recorded:
        if not isinstance(row, dict):
            errors.append("invalid_recorded_row")
            continue
        version = row.get("version")
        evidence = row.get("repo_source_evidence")
        blob = row.get("recorded_statement_git_blob_sha1")
        if not isinstance(blob, str) or not re.fullmatch("[a-f0-9]{40}", blob):
            errors.append("recorded_sql_blob_identity_invalid")
        if row.get("remote_recorded") is not True:
            errors.append("historically_recorded_marked_pending")
        if version in UNMATCHED_GIT_SOURCES:
            if evidence != "no_matching_git_blob_found":
                errors.append("unmatched_git_source_must_remain_unverified")
        elif evidence in ("canonical_file_match", "historical_git_blob_match"):
            matched += 1
        else:
            errors.append("previously_matched_blob_missing_evidence")
    if matched != 9:
        errors.append("nine_recorded_sql_blobs_not_matched")

    pending = data.get("pending_compliance")
    if not isinstance(pending, list) or len(pending) != 7:
        errors.append("seven_compliance_sources_missing")
        pending = []
    if [x.get("version") for x in pending if isinstance(x, dict)] != list(EXPECTED_PENDING):
        errors.append("pending_versions_out_of_order_or_missing")
    for row in pending:
        if not isinstance(row, dict):
            errors.append("invalid_pending_row")
            continue
        version,name = row.get("version"),row.get("name")
        if (not isinstance(version, str) or not isinstance(name,str)
                or not re.fullmatch("[a-z0-9_]+", name)):
            errors.append("invalid_pending_migration_identity")
            continue
        expected = f"backend/supabase/migrations/{version}_{name}.sql"
        if row.get("source_path") != expected:
            errors.append("pending_source_path_invalid:" + version)
            continue
        source = root / expected
        if not source.is_file():
            errors.append("pending_source_missing:" + version)
        elif git_blob_sha1(source) != row.get("source_git_blob_sha1"):
            errors.append("pending_source_modified:" + version)
        if row.get("remote_recorded") is not False:
            errors.append("pending_version_already_claimed_applied:" + version)

    existing = data.get("important_existing_versions")
    if not isinstance(existing, list) or [
        (v.get("version"),v.get("recorded"),v.get("must_not_reapply"))
        for v in existing if isinstance(v,dict)
    ] != [
        ("20261114162323",True,True),
        ("20261114163243",True,True),
    ]:
        errors.append("previously_applied_foundation_not_preserved")
    if data.get("release_status") != NO_GO:
        errors.append("release_status_must_remain_no_go")
    if data.get("database_changes_made") is not False or data.get("deployment_authorized") is not False:
        errors.append("snapshot_claims_unauthorized_actions")
    return {
        "status": NO_GO,
        "source_checks": "passed" if not errors else "blocked",
        "errors": errors,
        "recorded_history_count_snapshot": 45,
        "post_baseline_recorded_snapshot": 12,
        "exact_recorded_git_blob_matches_snapshot": matched,
        "recorded_without_git_blob_match": sorted(UNMATCHED_GIT_SOURCES),
        "pending_compliance_versions_snapshot": list(EXPECTED_PENDING),
        "snapshot_date": data.get("evidence_date"),
        "remote_history_refreshed": False,
        "file_contents_considered_current_remote": False,
        "deployment_permitted": False,
        "regulatory_filing_permitted": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--report", type=Path, default=None)
    p.add_argument("--release-gate", action="store_true",
                   help="Always nonzero until a separately authorized release flow exists")
    args = p.parse_args()
    result = check(args.root)
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    if args.release_gate:
        return 2
    return 1 if result["source_checks"] != "passed" else 0


if __name__ == "__main__":
    sys.exit(main())
