"""Focused regression coverage for the fast PR migration-change preflight."""
import tempfile
import unittest
from pathlib import Path

from check_lionbi_change_hygiene import check_changes, parse_change_list


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

    def test_invalid_calendar_timestamp_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/20261301120000_invalid_month.sql"),
                 ("A", "backend/supabase/migrations/20260230093000_invalid_day.sql"),
                 ("A", "supabase/migrations/20261119246000_invalid_hour.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 3)
            self.assertTrue(all("invalid YYYYMMDDHHMMSS" in error for error in errors))

    def test_noncanonical_timestamp_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/00000101000000_invalid_year.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("invalid YYYYMMDDHHMMSS", errors[0])

    def test_leap_day_timestamp_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/20240229093000_leap_day.sql")],
                Path(temp),
            )
            self.assertEqual(errors, [])

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

    def test_migration_archive_is_not_treated_as_active(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, surfaces = check_changes(
                [("M", "docs/ops/finance_canonical_replay_20261119/migrations/20240101000000_initial_schema.sql")],
                Path(temp),
            )
            self.assertEqual(errors, [])
            self.assertNotIn("active-migrations", surfaces)

    def test_staging_migration_history_is_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, touched = check_changes(
                [
                    ("M", "staging_supabase/supabase/migrations/20240101000000_initial_schema.sql"),
                    ("A", "staging_supabase/supabase/migrations/20261301120000_bad.sql"),
                ],
                Path(temp),
            )
            self.assertEqual(len(errors), 2)
            self.assertIn("historical migration", errors[0])
            self.assertIn("invalid YYYYMMDDHHMMSS", errors[1])
            self.assertIn("active-migrations", touched)

    def test_unicode_decimal_version_is_not_valid_ascii_migration_name(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/٢٠٢٦١١٢١١٢٠٠٠٠_unicode.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("must use YYYYMMDDHHMMSS", errors[0])

    def test_nested_migration_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "staging_supabase/supabase/migrations/nested/20261121120000_bad.sql")],
                Path(temp),
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("must be a direct child", errors[0])

    def test_latest_base_version_collision_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/20261121120000_new.sql")],
                Path(temp),
                base_paths=["supabase/migrations/20261121120000_other.sql"],
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("latest base already contains", errors[0])

    def test_latest_base_same_filename_collision_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = "backend/supabase/migrations/20261121120000_new.sql"
            errors, _ = check_changes(
                [("A", path)], Path(temp), base_paths=[path],
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("latest base already contains", errors[0])

    def test_base_collisions_are_scoped_to_migration_root(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "staging_supabase/supabase/migrations/20261121120000_new.sql")],
                Path(temp),
                base_paths=["supabase/migrations/20261121120000_other.sql"],
            )
            self.assertEqual(errors, [])

    def test_staging_migration_valid_addition_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "staging_supabase" / "supabase" / "migrations"
            directory.mkdir(parents=True)
            (directory / "20261121120000_new.sql").write_text("-- new", encoding="utf-8")
            errors, surfaces = check_changes(
                [("A", "staging_supabase/supabase/migrations/20261121120000_new.sql")],
                Path(temp), base_paths=[],
            )
            self.assertEqual(errors, [])
            self.assertIn("active-migrations", surfaces)

    def test_base_nested_nonmigration_does_not_match(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = check_changes(
                [("A", "supabase/migrations/20261121120000_new.sql")],
                Path(temp),
                base_paths=["supabase/migrations/archive/20261121120000_old.sql"],
            )
            self.assertEqual(errors, [])

    def test_git_change_lines(self):
        self.assertEqual(parse_change_list("A\tsupabase/migrations/abc.sql\n"), [("A", "supabase/migrations/abc.sql")])
        with self.assertRaises(ValueError):
            parse_change_list("bad input")


if __name__ == "__main__":
    unittest.main()
