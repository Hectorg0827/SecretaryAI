#!/usr/bin/env python3
"""Read-only consistency check for SecretaryAI's three SQL source trees.

This does not validate all legacy migrations or repair remote history. The
manual V1-V16 chain is canonical ONLY for the disposable PostgreSQL tests.
The Supabase CLI root must be selected separately before db push.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

MANUAL_TO_SUPABASE = {
    "schema.sql": "20240101000000_initial_schema.sql",
    "schema_v2_additions.sql": "20240601000000_v2_additions.sql",
    "schema_v3_integrations.sql": "20240901000000_v3_integrations.sql",
    "schema_v4_connector_secret_digest.sql": "20261017000000_connector_install_secret_digest.sql",
    "schema_v5_stock_ledger.sql": "20261017010000_stock_ledger.sql",
    "schema_v6_purchasing_receiving.sql": "20261017020000_purchasing_receiving.sql",
    "schema_v7_purchasing_evidence.sql": "20261017030000_purchasing_evidence.sql",
    "schema_v8_evidence_integrity.sql": "20261017040000_evidence_integrity.sql",
    "schema_v9_malware_review.sql": "20261017050000_malware_review.sql",
    "schema_v10_agent_execution_ledger.sql": "20261024015157_agent_execution_ledger.sql",
    "schema_v11_atomic_agent_execution_claims.sql": "20261024015412_atomic_agent_execution_claims.sql",
    "schema_v12_dedicated_execution_role.sql": "20261024020218_dedicated_execution_role.sql",
    "schema_v13_atomic_durable_draft_approval.sql": "20261024021157_atomic_durable_draft_approval.sql",
    "schema_v14_immutable_approved_email_payload.sql": "20261024023612_immutable_approved_email_payload.sql",
    "schema_v15_staging_email_allowlist_claim.sql": "20261024025115_staging_email_allowlist_claim.sql",
    "schema_v16_trigger_function_search_paths.sql": "20261024041600_trigger_function_search_paths.sql",
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.root
    primary = root / "supabase" / "migrations"
    secondary = root / "backend" / "supabase" / "migrations"
    manual = root / "database"

    errors: list[str] = []
    for name, target in MANUAL_TO_SUPABASE.items():
        a, b = manual / name, primary / target
        if not a.is_file() or not b.is_file():
            errors.append(f"Missing manual/primary pair: {a} / {b}")
        elif file_hash(a) != file_hash(b):
            errors.append(f"Different SQL in manual/primary pair: {name} / {target}")
        if name not in (
            "schema.sql", "schema_v2_additions.sql", "schema_v3_integrations.sql"
        ):
            other = secondary / target
            if not other.is_file():
                errors.append(f"Missing backend migration copy: {target}")
            elif b.is_file() and file_hash(b) != file_hash(other):
                errors.append(f"Different SQL in primary/backend copy: {target}")

    primary_names = {p.name for p in primary.glob("*.sql")}
    secondary_names = {p.name for p in secondary.glob("*.sql")}
    for name in sorted(primary_names & secondary_names):
        if file_hash(primary / name) != file_hash(secondary / name):
            errors.append(f"Different SQL in shared migration: {name}")
    for location, names in ((primary, primary_names), (secondary, secondary_names)):
        versions = [name.split("_", 1)[0] for name in names]
        if len(set(versions)) != len(versions):
            errors.append(f"Duplicate migration version in {location}")

    if errors:
        for error in errors:
            print("MIGRATION SOURCE DRIFT:", error, file=sys.stderr)
        return 1

    unmapped = primary_names - set(MANUAL_TO_SUPABASE.values())
    backend_only = secondary_names - primary_names
    print(f"V1-V16 content parity verified: {len(MANUAL_TO_SUPABASE)} source pairs")
    print(f"Primary-root migrations outside V1-V16: {len(unmapped)}")
    print(f"Backend-only migrations: {len(backend_only)}")
    print("PASS for source parity ONLY. Do not infer deployed-schema parity.")
    print("Do not run db push/repair until legacy migrations and remote versions are reconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
