"""Offline regression tests for the name-only Finance UAT preflight."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from check_finance_uat_env import (
    REQUIRED_BOOT, REQUIRED_SANDBOX, check_names, load_variable_names, main,
)


class FinanceUATNameGateTests(unittest.TestCase):
    def _inputs(self, folder, live, staged):
        a = Path(folder) / "live.json"
        b = Path(folder) / "staged.json"
        a.write_text(json.dumps(live), encoding="utf-8")
        b.write_text(json.dumps(staged), encoding="utf-8")
        c = Path(folder) / "removed.json"
        c.write_text("[]", encoding="utf-8")
        return a, b, c

    def test_all_required_boot_names_pass_when_split_across_layers(self):
        live = {"SECRET_KEY", "DATABASE_URL"}
        staged = set(REQUIRED_BOOT) - live
        self.assertEqual(check_names(live, staged, "boot", billing_enabled=False, debug=False), [])

    def test_missing_required_secret_names_fail_closed(self):
        missing = {"SECRET_KEY", "DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                   "ANTHROPIC_API_KEY"}
        present = set(REQUIRED_BOOT) - missing
        self.assertEqual(check_names(set(), present, "boot", billing_enabled=False, debug=False), sorted(missing))

    def test_sandbox_requires_real_provider_configuration_names(self):
        self.assertEqual(
            check_names(set(REQUIRED_BOOT), set(), "sandbox", billing_enabled=False, debug=False),
            sorted(REQUIRED_SANDBOX - REQUIRED_BOOT),
        )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            check_names(set(), set(), "production", billing_enabled=False, debug=False)

    def test_rejects_value_maps_even_if_keys_look_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vars.json"
            path.write_text(json.dumps({"SECRET_KEY": "do-not-log-this"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_variable_names(path)

    def test_rejects_nonvariable_strings_and_nonstring_elements(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vars.json"
            for bad in (["PASSWORD=secret"], ["TEST", 123], "SECRET_KEY"):
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_variable_names(path)

    def test_staged_delete_removes_live_required_name(self):
        self.assertEqual(
            check_names(set(REQUIRED_BOOT), set(), "boot", removed={"SECRET_KEY"},
                        billing_enabled=False, debug=False),
            ["SECRET_KEY"],
        )

    def test_staged_add_and_delete_conflict_is_blocked(self):
        with self.assertRaises(ValueError):
            check_names(set(), {"SECRET_KEY"}, "boot", removed={"secret_key"},
                        billing_enabled=False, debug=False)

    def test_conditional_stripe_keys_required_only_when_billing_active(self):
        expected = ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"]
        self.assertEqual(
            check_names(set(REQUIRED_BOOT), set(), "boot",
                        billing_enabled=True, debug=False),
            expected,
        )
        self.assertEqual(
            check_names(set(REQUIRED_BOOT), set(), "boot",
                        billing_enabled=False, debug=False), [],
        )
        self.assertEqual(
            check_names(set(REQUIRED_BOOT), set(), "boot",
                        billing_enabled=True, debug=True), [],
        )
        self.assertEqual(
            check_names(set(REQUIRED_BOOT) | set(expected), set(), "boot",
                        billing_enabled=True, debug=False), [],
        )

    def test_encryption_key_rotation_requires_current_key(self):
        self.assertEqual(
            check_names(set(REQUIRED_BOOT) | {"ENCRYPTION_KEY_PREVIOUS"},
                        set(), "boot", billing_enabled=False, debug=False),
            ["ENCRYPTION_KEY"],
        )

    def test_mixed_case_variable_names_are_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "names.json"
            path.write_text('["secret_key", "DbUrl", "SUPABASE_URL"]', encoding="utf-8")
            self.assertEqual(load_variable_names(path),
                             {"SECRET_KEY", "DBURL", "SUPABASE_URL"})
        self.assertEqual(
            check_names({name.lower() for name in REQUIRED_BOOT}, set(),
                        "boot", billing_enabled=False, debug=False), [],
        )

    def test_unverified_non_secret_safety_flags_cannot_pass(self):
        with self.assertRaises(ValueError):
            check_names(set(REQUIRED_BOOT), set(), "boot",
                        billing_enabled=None, debug=False)

    def test_cli_exits_one_and_names_missing_without_value_exposure(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b, c = self._inputs(temp, [], sorted(REQUIRED_BOOT - {"SECRET_KEY"}))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = main(["--live-names", str(a), "--staged-names", str(b),
                           "--staged-removed-names", str(c),
                           "--billing-enabled", "false", "--debug", "false"])
            self.assertEqual(rc, 1)
            self.assertIn("MISSING: SECRET_KEY", stdout.getvalue())
            self.assertIn("NO-GO", stdout.getvalue())

    def test_required_backend_settings_are_covered_by_boot_contract(self):
        # Parse source without importing the app or loading any .env secrets.
        # A new mandatory Settings field must update this preflight as well.
        import ast

        config_path = (Path(__file__).resolve().parents[1] /
                       "backend" / "app" / "config.py")
        tree = ast.parse(config_path.read_text(encoding="utf-8"))
        settings = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Settings"
        )
        required = {
            node.target.id.upper()
            for node in settings.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is None
        }
        self.assertTrue(required, "Settings must define mandatory fields")
        missing = sorted(required - REQUIRED_BOOT)
        self.assertFalse(
            missing,
            f"New mandatory backend Settings not in Finance boot preflight: {missing}",
        )

    def test_cli_pass_is_not_false_financial_clearance(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b, c = self._inputs(temp, sorted(REQUIRED_SANDBOX), [])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = main(["--live-names", str(a), "--staged-names", str(b),
                           "--staged-removed-names", str(c),
                           "--billing-enabled", "false", "--debug", "false",
                           "--mode", "sandbox"])
            self.assertEqual(rc, 0)
            self.assertIn("UNVERIFIED", stdout.getvalue())

    def test_cli_rejects_malformed_json_without_echoing_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            a = Path(temp) / "bad.json"
            b = Path(temp) / "good.json"
            a.write_text('{"SECRET_KEY":"highly-sensitive-canary"}', encoding="utf-8")
            b.write_text("[]", encoding="utf-8")
            c = Path(temp) / "removed.json"
            c.write_text("[]", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = main(["--live-names", str(a), "--staged-names", str(b),
                           "--staged-removed-names", str(c),
                           "--billing-enabled", "false", "--debug", "false"])
            self.assertEqual(rc, 2)
            self.assertNotIn("highly-sensitive-canary", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
