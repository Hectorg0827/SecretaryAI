import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import hosted_post_replay_least_privilege as post_priv  # noqa: E402
import hosted_preflight as preflight  # noqa: E402
import hosted_replay as replay  # noqa: E402


class HostedReplayTests(unittest.TestCase):
    def test_reject_parent_and_protected_refs(self):
        for bad_ref in preflight.FORBIDDEN_PROJECT_REFS:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "replay.sql"
                result = subprocess.run(
                    [sys.executable, str(BASE / "hosted_replay.py"), "--project-ref", bad_ref, "--output", str(out)],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, f"Expected rejection for {bad_ref}")
                self.assertFalse(out.exists())

    def test_reject_parent_even_if_env_overridden(self):
        with patch.dict(os.environ, {"EXPECTED_DISPOSABLE_PROJECT_REF": "xerktvdgjvjozvckjyfp"}):
            with self.assertRaises(ValueError):
                preflight.validate_disposable_ref("xerktvdgjvjozvckjyfp")

    def test_reject_unconfirmed_arbitrary_ref(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXPECTED_DISPOSABLE_PROJECT_REF", None)
            with self.assertRaises(ValueError):
                preflight.validate_disposable_ref("abcdefghijklmnopqrst")

    def test_accept_explicitly_designated_new_disposable_ref(self):
        new_ref = "abcdefghijklmnopqrst"
        with patch.dict(os.environ, {"EXPECTED_DISPOSABLE_PROJECT_REF": new_ref}):
            self.assertEqual(preflight.validate_disposable_ref(new_ref), new_ref)
            self.assertEqual(preflight.host_for_ref(new_ref), f"db.{new_ref}.supabase.co")

    def test_canonical_sequence_and_single_outer_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "replay.sql"
            subprocess.run(
                [sys.executable, str(BASE / "hosted_replay.py"), "--project-ref", preflight.REF, "--output", str(out)],
                check=True,
            )
            sql = out.read_text()
            rows = json.loads((BASE / "manifest.json").read_text())["migrations"]

            # First 5 baseline rows must use guarded UPDATE (no INSERT ... ON CONFLICT);
            # remaining 41 rows must use strict INSERT without ON CONFLICT.
            baseline_positions = [
                sql.index(f"$ledger_baseline_{r['version']}$") for r in rows[:5]
            ]
            insert_positions = [
                sql.index(f"VALUES ('{r['version']}',") for r in rows[5:]
            ]
            all_positions = baseline_positions + insert_positions
            self.assertEqual(len(all_positions), 46)
            self.assertEqual(all_positions, sorted(all_positions))

            # Verify outer transaction wrapper and zero nested top-level BEGIN/COMMIT statements!
            top_stmts = replay.split_top_level_sql_statements(sql, filename="replay.sql")
            ledger_inserts = [
                s for s in top_stmts
                if s.startswith("INSERT INTO supabase_migrations.schema_migrations")
            ]
            self.assertEqual(len(ledger_inserts), 41)
            for stmt in ledger_inserts:
                self.assertTrue(stmt.endswith("'])"), f"Unexpected clause after VALUES(...): {stmt[-60:]}")
            self.assertTrue(replay._is_tx_begin(top_stmts[0]))
            self.assertTrue(replay._is_tx_commit(top_stmts[-1]))
            for idx, stmt in enumerate(top_stmts[1:-1], start=2):
                self.assertFalse(
                    replay._is_tx_begin(stmt) or replay._is_tx_commit(stmt),
                    f"Statement #{idx} leaked nested transaction control: {stmt[:80]}",
                )

            # Verify unmodified canonical SQL (including original 'begin;'/'commit;') is preserved
            # inside ARRAY['...'] provenance for the 3 wrapped migrations!
            for wrapped_name in (
                "20261102092256_secretaryai_stripe_billing_safety.sql",
                "20261102232409_billing_checkout_historical_invoice_safety.sql",
                "20261103022426_onboarding_audit_consistency.sql",
            ):
                original = (BASE / "migrations" / wrapped_name).read_text()
                self.assertIn("ARRAY[" + replay.literal(original) + "]", sql)

    def test_prepare_transactional_sql_strips_balanced_wrapper_and_preserves_plpgsql(self):
        sample = """
        -- header comment
        BEGIN;
        CREATE OR REPLACE FUNCTION demo() RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM 1;
        END;
        $$;
        COMMIT;
        -- trailing comment without semicolon
        """
        cleaned = replay.prepare_transactional_sql(sample, filename="sample.sql")
        stmts = replay.split_top_level_sql_statements(cleaned, filename="cleaned.sql")
        self.assertEqual(len(stmts), 1)
        self.assertIn("BEGIN\n          PERFORM 1;", stmts[0])

    def test_prepare_transactional_sql_rejects_hazards(self):
        hazards = [
            "BEGIN; CREATE TABLE t(id int);",  # unbalanced BEGIN
            "CREATE TABLE t(id int); COMMIT;",  # unbalanced COMMIT
            "CREATE TABLE t(id int); BEGIN; CREATE TABLE t2(id int); COMMIT;",  # mid-file tx
            "CREATE INDEX CONCURRENTLY idx_t ON t(id);",
            "VACUUM public.companies;",
            "ALTER TYPE my_enum ADD VALUE 'x';",
            "SAVEPOINT sp1;",
            "ROLLBACK;",
            "SET ROLE authenticated;",
            "SET search_path = public;",
            "CREATE TABLE t(id int)",  # missing semicolon
        ]
        for bad_sql in hazards:
            with self.assertRaises(ValueError, msg=f"Failed to reject hazard: {bad_sql}"):
                replay.prepare_transactional_sql(bad_sql, filename="hazard.sql")

    def test_endpoint_is_pinned_and_readonly(self):
        with tempfile.NamedTemporaryFile() as ca, patch.dict(
            os.environ, {"PGHOST": "wrong", "PGSERVICE": "wrong", "PGHOSTADDR": "127.0.0.1"}
        ):
            env = preflight.connection_env("fake", ca.name)
            self.assertEqual(env["PGHOST"], preflight.HOST)
            self.assertEqual(env["PGSSLMODE"], "verify-full")
            self.assertIn("default_transaction_read_only=on", env["PGOPTIONS"])
            self.assertNotIn("PGHOSTADDR", env)
            self.assertNotIn("PGSERVICE", env)

    def test_missing_secret(self):
        with self.assertRaises(ValueError):
            preflight.connection_env(None, None)

    def test_resolve_psql_binary_missing(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                preflight.resolve_psql_binary()

    def test_post_replay_least_privilege_modes(self):
        for mode in ("finance-uat", "rls-acceptance"):
            sql = post_priv.generate_post_replay_sql(preflight.REF, mode)
            stmts = replay.split_top_level_sql_statements(sql, filename=f"{mode}.sql")
            self.assertTrue(replay._is_tx_begin(stmts[0]))
            self.assertTrue(replay._is_tx_commit(stmts[-1]))
            self.assertIn("REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM PUBLIC, anon, authenticated;", sql)
            self.assertIn("secretary_internal.custom_access_token_hook(event jsonb)", sql)
            self.assertIn("fdf6e1eeb1cc2165a79441c1d40a19be", sql)
            if mode == "finance-uat":
                self.assertIn("REVOKE DELETE ON TABLE public.companies, public.users, public.logistics_events", sql)
            else:
                self.assertIn("GRANT SELECT ON TABLE public.accounts\n  TO anon;", sql)

        with self.assertRaises(ValueError):
            post_priv.generate_post_replay_sql("xerktvdgjvjozvckjyfp", "finance-uat")


if __name__ == "__main__":
    unittest.main()
