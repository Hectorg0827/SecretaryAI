"""Focused regression coverage for the fast PR migration-change preflight."""
import tempfile
import unittest
from pathlib import Path

from check_lionbi_change_hygiene import check_changes, check_migration_tree, parse_change_list


class FastGateTests(unittest.TestCase):
    def test_non_schema_changes_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, surfaces = check_changes(
                [("M", "backend/app/api/finance.py"), ("A", "docs/ops/notes.md")],
                Path(temp),
            )
            self.assertEqual(errors, [])
            self.assertEqual(surfaces, {"backend", "docs"})

    def test_new_migration_has_unique_version(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "supabase" / "migrations"
            directory.mkdir(parents=True)
            (directory / "20261119120000_new_constraint.sql").write_text("-- new", encoding="utf-8")
            errors, surfaces = check_changes(
                [("A", "supabase/migrations/20261119120000_new_constraint.sql")],
                Path(temp),
            )
            self.assertEqual(errors, [])
            self.assertIn("active-migrations", surfaces)

    def test_duplicate_migration_version_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "supabase" / "migrations"
            directory.mkdir(parents=True)
            (directory / "20261119120000_first.sql").write_text("-- first", encoding="utf-8")
            (directory / "20261119120000_second.sql").write_text("-- second", encoding="utf-8")
            errors, _ = check_changes(
                [("A", "supabase/migrations/20261119120000_second.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("duplicate version", errors[0])

    def test_renaming_or_modifying_historical_migration_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("D", "supabase/migrations/20240101000000_initial_schema.sql"),
                 ("M", "backend/supabase/migrations/20261024000000_old.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 2)

    def test_bad_new_migration_name_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/create_new_table.sql")], Path(temp)
            )
            self.assertEqual(len(errors), 1)

    def test_unicode_decimal_digits_in_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/２０２６１１１９１２００００_unicode.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("YYYYMMDDHHMMSS", errors[0])


    def test_staging_migration_edit_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, surfaces = check_changes(
                [("M", "staging_supabase/supabase/migrations/20261119120000_existing.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("active-migrations", surfaces)

    def test_staging_duplicate_version_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "staging_supabase/supabase/migrations"
            directory.mkdir(parents=True)
            (directory / "20261119120000_one.sql").write_text("-- existing", encoding="utf-8")
            (directory / "20261119120000_two.sql").write_text("-- new", encoding="utf-8")
            errors, _ = check_changes(
                [("A", "staging_supabase/supabase/migrations/20261119120000_two.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("duplicate version", errors[0])

    def test_nested_migration_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/subdir/20261119120000_nested.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("direct child", errors[0])

    def test_impossible_calendar_timestamp_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for version in ("20261301120000", "20260230010101", "20261119246000"):
                with self.subTest(version=version):
                    errors, _ = check_changes(
                        [("A", f"supabase/migrations/{version}_invalid.sql")],
                        Path(temp),
                    )
                    self.assertEqual(len(errors), 1)
                    self.assertIn("real calendar date/time", errors[0])

    def test_valid_leap_day_timestamp_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/20240229010101_valid.sql")],
                Path(temp),
            )
            self.assertEqual(errors, [])

    def test_migration_archive_is_not_treated_as_active(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, surfaces = check_changes(
                [("M", "docs/ops/finance_canonical_replay_20261119/migrations/20240101000000_initial_schema.sql")],
                Path(temp),
            )
            self.assertEqual(errors, [])
            self.assertNotIn("active-migrations", surfaces)

    def test_integration_tree_rejects_collisions_from_two_green_prs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "supabase/migrations"
            directory.mkdir(parents=True)
            first = "20261121120000_alpha.sql"
            second = "20261121120000_beta.sql"
            (directory / first).write_text("-- first PR", encoding="utf-8")
            self.assertEqual(check_migration_tree(Path(temp)), [])
            (directory / second).write_text("-- independently green second PR", encoding="utf-8")
            errors = check_migration_tree(Path(temp))
            self.assertEqual(len(errors), 1)
            self.assertIn("duplicate migration version 20261121120000", errors[0])

    def test_merge_group_tree_rejects_nested_sql_without_diff(self):
        # A merge_group job does not run the PR name-status diff guard.
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "staging_supabase/supabase/migrations/archive"
            directory.mkdir(parents=True)
            (directory / "20261121120000_hidden.sql").write_text("-- not applied", encoding="utf-8")
            errors = check_migration_tree(Path(temp))
            self.assertEqual(len(errors), 1)
            self.assertIn("direct child", errors[0])
            self.assertIn("archive/20261121120000_hidden.sql", errors[0])

    def test_integration_tree_scopes_collisions_per_root(self):
        with tempfile.TemporaryDirectory() as temp:
            for active in ("supabase/migrations", "staging_supabase/supabase/migrations"):
                directory = Path(temp) / active
                directory.mkdir(parents=True)
                (directory / "20261121120000_additive.sql").write_text("-- independent", encoding="utf-8")
            self.assertEqual(check_migration_tree(Path(temp)), [])

    def test_required_fast_feedback_is_not_pr_path_filtered(self):
        # Required status checks must report on docs-only and mixed PRs too.
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/lionbi-pr-fast-feedback.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("  pull_request:", workflow)
        self.assertIn("  merge_group:", workflow)
        pr_events = workflow.split("  pull_request:", 1)[1].split("  merge_group:", 1)[0]
        self.assertNotIn("paths:", pr_events)
        self.assertNotIn("paths-ignore:", pr_events)

    def test_ci_and_codeql_run_on_merge_groups(self):
        # Merge queues run checks on synthetic merge refs, not ordinary PR refs.
        workflow_root = Path(__file__).resolve().parents[1] / ".github/workflows"
        for workflow_name in ("ci.yml", "codeql.yml"):
            with self.subTest(workflow=workflow_name):
                workflow = (workflow_root / workflow_name).read_text(encoding="utf-8")
                trigger_block = workflow.split("on:" + chr(10), 1)[1]
                self.assertIn("  merge_group:" + chr(10), trigger_block)

    def test_git_change_lines(self):
        self.assertEqual(parse_change_list("A\tsupabase/migrations/abc.sql\n"), [("A", "supabase/migrations/abc.sql")])
        with self.assertRaises(ValueError):
            parse_change_list("bad input")


if __name__ == "__main__":
    unittest.main()
