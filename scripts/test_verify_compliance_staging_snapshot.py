"""Offline-only regression tests for the staging snapshot evidence checker."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_compliance_staging_snapshot import ROOT, SNAPSHOT, check


class StagingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        snapshot = json.loads((ROOT / SNAPSHOT).read_text(encoding="utf-8"))
        destination = self.root / SNAPSHOT
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(snapshot), encoding="utf-8")
        for row in snapshot["pending_compliance"]:
            source = ROOT / row["source_path"]
            target = self.root / row["source_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, mutator):
        path = self.root / SNAPSHOT
        data = json.loads(path.read_text(encoding="utf-8"))
        mutator(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_snapshot_sources_pass_but_deployment_always_no_go(self):
        result = check(self.root)
        self.assertEqual(result["source_checks"], "passed")
        self.assertEqual(result["status"], "NO_GO")
        self.assertEqual(result["exact_recorded_git_blob_matches_snapshot"], 9)
        self.assertEqual(len(result["pending_compliance_versions_snapshot"]), 7)
        self.assertFalse(result["deployment_permitted"])
        self.assertFalse(result["remote_history_refreshed"])

    def test_modified_pending_sql_blob_fails(self):
        source = next((self.root / "backend" / "supabase" / "migrations").glob("2026111500*.sql"))
        source.write_text("-- altered source\n", encoding="utf-8")
        self.assertEqual(check(self.root)["source_checks"], "blocked")

    def test_missing_pending_sql_fails(self):
        source = next((self.root / "backend" / "supabase" / "migrations").glob("2026111506*.sql"))
        source.unlink()
        self.assertEqual(check(self.root)["source_checks"], "blocked")

    def test_forged_applied_migration_fails(self):
        self.edit(lambda d: d["pending_compliance"][0].update(remote_recorded=True))
        self.assertEqual(check(self.root)["source_checks"], "blocked")

    def test_forged_release_ready_fails(self):
        self.edit(lambda d: d.update(release_status="GO"))
        self.assertIn("release_status_must_remain_no_go", check(self.root)["errors"])

    def test_missing_historical_github_blob_cannot_be_claimed_matched(self):
        self.edit(lambda d: d["post_baseline_migrations"][4].update(
            repo_source_evidence="historical_git_blob_match"
        ))
        self.assertIn(
            "unmatched_git_source_must_remain_unverified",
            check(self.root)["errors"],
        )

    def test_existing_pricebook_foundation_cannot_be_reapplied(self):
        self.edit(lambda d: d["important_existing_versions"][0].update(
            must_not_reapply=False
        ))
        self.assertIn(
            "previously_applied_foundation_not_preserved",
            check(self.root)["errors"],
        )

    def test_tenant_or_environment_cannot_be_changed_silently(self):
        self.edit(lambda d: d.update(project_ref="production-project"))
        self.assertIn("not_secretaryai_staging", check(self.root)["errors"])

    def test_strict_release_gate_exits_nonzero_even_with_valid_source(self):
        script = ROOT / "scripts" / "verify_compliance_staging_snapshot.py"
        r = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), "--release-gate"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stdout)["status"], "NO_GO")

    def test_snapshot_is_required_not_assumed_empty(self):
        (self.root / SNAPSHOT).unlink()
        self.assertIn("staging_snapshot_missing", check(self.root)["errors"])


if __name__ == "__main__":
    unittest.main()
