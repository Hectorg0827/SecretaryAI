#!/usr/bin/env python3
"""Read-only release preflight for Compliance Phase 1 staging migrations.

CI's disposable PostgreSQL applies backend/supabase SQL files directly, but
Supabase CLI roots and the signed-off staging bundle are distinct inputs.
An isolated CI schema PASS must NEVER imply that db push would apply the patch.

This checker does not call GitHub/Supabase, read secrets, mutate DB state,
repair remote migration history, merge, or approve a deployment. Its result
is always NO_GO absent separately verified remote history and human approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_MIGRATIONS = (
    "20261114162323_pricing_pricebook_cost_basis_staging_20261114.sql",
    "20261115000000_compliance_atomic_policy_audit.sql",
    "20261115010000_compliance_price_posting_rulebook.sql",
    "20261115020000_price_posting_human_attestation.sql",
    "20261115030000_price_posting_preparation_ledger.sql",
    "20261115040000_price_posting_specific_policy_guard.sql",
    "20261115050000_price_posting_accounting_observations.sql",
    "20261115060000_price_posting_capture_bindings.sql",
)

RELEASE_HUMAN_GATES = (
    "remote_migration_history_not_authenticated",
    "staging_schema_and_existing_migration_dependencies_not_reconciled",
    "staging_deployment_not_explicitly_authorized",
    "authenticated_staging_acceptance_not_performed",
    "financial_and_regulatory_human_signoff_not_verified",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(root: Path) -> dict[str, Any]:
    """Return a deterministic, redacted local source-alignment report."""
    root = root.resolve()
    primary = root / "supabase" / "migrations"
    backend = root / "backend" / "supabase" / "migrations"
    staged = root / "staging_supabase" / "supabase" / "migrations"
    manifest_path = root / "staging_supabase" / "manifest.json"

    errors: list[str] = []
    rows: list[dict[str, Any]] = []

    versions = []
    for filename in REQUIRED_MIGRATIONS:
        if not re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", filename):
            errors.append("invalid_required_filename:" + filename)
        versions.append(filename.split("_", 1)[0])
    if versions != sorted(versions) or len(set(versions)) != len(versions):
        errors.append("required_migration_version_order_invalid")

    manifest: dict[str, Any] | None = None
    if not manifest_path.is_file():
        errors.append("staging_manifest_unavailable")
    else:
        try:
            candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict) or not isinstance(candidate.get("entries"), list):
                raise ValueError("Not a manifest mapping")
            manifest = candidate
        except (OSError, UnicodeError, ValueError):
            errors.append("staging_manifest_invalid")
    manifest_by_filename: dict[str, dict] = {}
    if manifest is not None:
        for entry in manifest["entries"]:
            if not isinstance(entry, dict):
                errors.append("staging_manifest_entry_invalid")
                continue
            name = entry.get("staging_path")
            if not isinstance(name, str):
                errors.append("staging_manifest_entry_path_invalid")
                continue
            filename = Path(name).name
            if filename in manifest_by_filename:
                errors.append("staging_manifest_duplicate_filename:" + filename)
            else:
                manifest_by_filename[filename] = entry

    for filename in REQUIRED_MIGRATIONS:
        # Only the pricebook foundation is already in the root CLI tree;
        # all later Compliance candidate migrations originated in backend/.
        source = (primary if filename.startswith("20261114162323_") else backend) / filename
        cli = primary / filename
        target = staged / filename
        entry = manifest_by_filename.get(filename)
        item = {
            "filename": filename,
            "source_present": source.is_file(),
            "default_cli_root_aligned": False,
            "staging_bundle_aligned": False,
            "staging_manifest_aligned": False,
            "source_sha256": None,
        }
        if not source.is_file():
            errors.append("required_source_missing:" + filename)
            rows.append(item)
            continue
        actual_hash = sha256(source)
        item["source_sha256"] = actual_hash
        item["default_cli_root_aligned"] = cli.is_file() and sha256(cli) == actual_hash
        item["staging_bundle_aligned"] = target.is_file() and sha256(target) == actual_hash

        if entry is not None:
            version, label = filename.split("_", 1)
            expected_path = "staging_supabase/supabase/migrations/" + filename
            item["staging_manifest_aligned"] = (
                entry.get("version") == version
                and entry.get("name") == label.removesuffix(".sql")
                and entry.get("sha256") == actual_hash
                and entry.get("staging_path") == expected_path
            )
        if not item["default_cli_root_aligned"]:
            errors.append("missing_or_different_default_cli_migration:" + filename)
        if not item["staging_bundle_aligned"]:
            errors.append("missing_or_different_staging_bundle_migration:" + filename)
        if not item["staging_manifest_aligned"]:
            errors.append("missing_or_different_staging_manifest_entry:" + filename)
        rows.append(item)

    # Local alignment is necessary but not sufficient: remote history,
    # operator authorization, legal source attestation and real acceptance
    # are outside any local source-only checker and must be signed off.
    return {
        "scope": "Lion BI Compliance Phase 1 migration release preflight",
        "status": "NO_GO",
        "structural_alignment": "passed" if not errors else "blocked",
        "required_migrations": rows,
        "source_blockers": errors,
        "unverified_release_gates": list(RELEASE_HUMAN_GATES),
        "remote_migration_history_accessed": False,
        "supabase_connection_attempted": False,
        "staging_migration_applied": False,
        "deployment_authorized_by_this_report": False,
        "filing_or_price_publication_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path, default=None,
                        help="Write a local JSON evidence report; never upload/deploy")
    parser.add_argument("--require-ready", action="store_true",
                        help="Fail closed for release; not used to assert CI source tests")
    args = parser.parse_args()
    result = evaluate(args.root)
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 2 if args.require_ready else 0


if __name__ == "__main__":
    raise SystemExit(main())
