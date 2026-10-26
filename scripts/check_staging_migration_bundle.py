#!/usr/bin/env python3
"""Read-only verification of the SecretaryAI-STAGING migration source bundle.

This checks bytes, versions and source provenance; it never connects to a DB,
repairs migration history, or certifies that a remote push is safe.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "staging_supabase" / "manifest.json"
MIGRATIONS = ROOT / "staging_supabase" / "supabase" / "migrations"
MAP = ROOT / "docs" / "staging-evidence" / "2026-10-24" / "migration-map.json"
STAGING_REF = "xerktvdgjvjozvckjyfp"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    errors: list[str] = []
    manifest = json.loads(MANIFEST.read_text())
    original = json.loads(MAP.read_text())
    entries = manifest["entries"]
    if manifest.get("staging_supabase_ref") != STAGING_REF or original.get("project_ref") != STAGING_REF:
        errors.append("Wrong staging project reference")
    if len(entries) != 33 or manifest.get("total") != 33:
        errors.append("Expected 33 migration files, 5 baseline + 28 recorded")
    versions = [e["version"] for e in entries]
    if len(set(versions)) != len(versions) or sorted(versions) != versions:
        errors.append("Versions not unique and strictly ordered")
    expected = set()
    for entry in entries:
        target = ROOT / entry["staging_path"]
        source = ROOT / entry["source"]
        expected.add(target.name)
        required_name = f'{entry["version"]}_{entry["name"]}.sql'
        if target.name != required_name or target.parent != MIGRATIONS:
            errors.append(f"Noncanonical migration path {entry['staging_path']}")
            continue
        if not target.is_file() or not source.is_file():
            errors.append(f"Missing staged/source migration {required_name}")
            continue
        if sha256(target) != entry["sha256"] or sha256(source) != entry["sha256"]:
            errors.append(f"SQL body checksum mismatch {required_name}")
    actual = {p.name for p in MIGRATIONS.glob("*.sql")}
    if actual != expected:
        errors.append(f"Staging migration filenames differ: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    applied = {(m["version"],m["name"],m["body_sha256"]) for m in original["recorded_migrations"]}
    staged_applied = {(e["version"],e["name"],e["sha256"]) for e in entries if e["kind"] == "remote_applied_verified"}
    if applied != staged_applied:
        errors.append("Recorded remote migration mapping differs from audited evidence")
    original_baseline = {x["canonical_file"]: x["sha256"] for x in original["untracked_baseline"]}
    staged_baseline = {x["source"]: x["sha256"] for x in entries if x["kind"] == "verified_unrecorded_baseline"}
    if original_baseline != staged_baseline:
        errors.append("Verified baseline mapping differs from audited evidence")
    for msg in errors:
        print("STAGING MIGRATION BUNDLE ERROR:", msg, file=sys.stderr)
    if errors:
        return 1
    print("PASS: 33 staging SQL files byte-exact, 28 recorded remote mappings and 5 verified baseline files.")
    print("This is source validation ONLY. Remote migration repair and db push dry-run remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
