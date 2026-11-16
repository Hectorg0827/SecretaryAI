#!/usr/bin/env python3
"""Synthetic filesystem regression tests: no Supabase, provider or network IO."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from compliance_migration_release_preflight import REQUIRED_MIGRATIONS, evaluate, sha256

SCRIPT = Path(__file__).with_name("compliance_migration_release_preflight.py")


def fixture_root(root: Path) -> Path:
    entries = []
    for filename in REQUIRED_MIGRATIONS:
        version, label = filename.split("_", 1)
        source_prefix = (
            root / "supabase" / "migrations"
            if filename.startswith("20261114162323_")
            else root / "backend" / "supabase" / "migrations"
        )
        source_prefix.mkdir(parents=True, exist_ok=True)
        source = source_prefix / filename
        # Each file has distinguishable deterministic bytes.
        source.write_text("-- synthetic " + filename + "\n", encoding="utf-8")
        primary = root / "supabase" / "migrations" / filename
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_bytes(source.read_bytes())
        staged = root / "staging_supabase" / "supabase" / "migrations" / filename
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(source.read_bytes())
        entries.append({
            "version": version,
            "name": label.removesuffix(".sql"),
            "sha256": sha256(source),
            "staging_path": "staging_supabase/supabase/migrations/" + filename,
        })
    manifest = root / "staging_supabase" / "manifest.json"
    manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return root


class ComplianceMigrationReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = fixture_root(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_all_local_files_align_but_remote_gate_still_no_go(self):
        report = evaluate(self.root)
        self.assertEqual(report["structural_alignment"], "passed")
        self.assertEqual(report["source_blockers"], [])
        self.assertEqual(report["status"], "NO_GO")
        self.assertTrue(report["unverified_release_gates"])
        self.assertFalse(report["remote_migration_history_accessed"])
        self.assertFalse(report["deployment_authorized_by_this_report"])
        self.assertFalse(report["staging_migration_applied"])
        self.assertEqual(len(report["required_migrations"]), 8)

    def test_missing_backend_source_is_not_interpreted_as_applied(self):
        filename = REQUIRED_MIGRATIONS[2]
        (self.root / "backend" / "supabase" / "migrations" / filename).unlink()
        report = evaluate(self.root)
        self.assertIn("required_source_missing:" + filename, report["source_blockers"])
        self.assertEqual(report["status"], "NO_GO")

    def test_missing_default_cli_copy_blocks_bundle_alignment(self):
        filename = REQUIRED_MIGRATIONS[4]
        (self.root / "supabase" / "migrations" / filename).unlink()
        report = evaluate(self.root)
        self.assertIn(
            "missing_or_different_default_cli_migration:" + filename,
            report["source_blockers"],
        )
        self.assertEqual(report["structural_alignment"], "blocked")

    def test_staging_migration_sha_drift_is_detected(self):
        filename = REQUIRED_MIGRATIONS[5]
        path = self.root / "staging_supabase" / "supabase" / "migrations" / filename
        path.write_text("-- unauthorized changed migration\n", encoding="utf-8")
        report = evaluate(self.root)
        self.assertIn(
            "missing_or_different_staging_bundle_migration:" + filename,
            report["source_blockers"],
        )

    def test_staging_manifest_checksum_drift_is_detected(self):
        manifest = self.root / "staging_supabase" / "manifest.json"
        data = json.loads(manifest.read_text())
        data["entries"][0]["sha256"] = "not-matching"
        manifest.write_text(json.dumps(data))
        report = evaluate(self.root)
        self.assertIn(
            "missing_or_different_staging_manifest_entry:" + REQUIRED_MIGRATIONS[0],
            report["source_blockers"],
        )

    def test_staging_manifest_missing_is_not_a_clean_zero(self):
        (self.root / "staging_supabase" / "manifest.json").unlink()
        report = evaluate(self.root)
        self.assertIn("staging_manifest_unavailable", report["source_blockers"])
        self.assertEqual(report["status"], "NO_GO")

    def test_duplicate_manifest_entry_is_detected(self):
        manifest = self.root / "staging_supabase" / "manifest.json"
        data = json.loads(manifest.read_text())
        data["entries"].append(data["entries"][0])
        manifest.write_text(json.dumps(data))
        report = evaluate(self.root)
        self.assertIn(
            "staging_manifest_duplicate_filename:" + REQUIRED_MIGRATIONS[0],
            report["source_blockers"],
        )

    def test_strict_release_gate_always_rejects_unverified_remote(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "--require-ready"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "NO_GO")
        self.assertFalse(json.loads(result.stdout)["supabase_connection_attempted"])

    def test_report_write_only_to_explicit_local_path(self):
        output = self.root / "local-evidence.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "--report", str(output)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(output.read_text())["status"], "NO_GO")


if __name__ == "__main__":
    unittest.main()
