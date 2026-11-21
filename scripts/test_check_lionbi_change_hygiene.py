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

    def test_migration_archive_is_not_treated_as_active(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, surfaces = check_changes(
                [("M", "docs/ops/finance_canonical_replay_20261119/migrations/20240101000000_initial_schema.sql")],
                Path(temp),
            )
            self.assertEqual(errors, [])
            self.assertNotIn("active-migrations", surfaces)

    def test_git_change_lines(self):
        self.assertEqual(parse_change_list("A\tsupabase/migrations/abc.sql\n"), [("A", "supabase/migrations/abc.sql")])
        with self.assertRaises(ValueError):
            parse_change_list("bad input")


if __name__ == "__main__":
    unittest.main()
