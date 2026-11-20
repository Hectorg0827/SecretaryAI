#!/usr/bin/env python3
"""Name-only configuration gate for isolated Finance AR acceptance.

This script never reads variable values or contacts Railway, Supabase, Intuit
or any external endpoint. It is not a substitute for authenticated UAT.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

VARIABLE_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")

# Settings fields without defaults plus deliberately explicit staging safety
# switches. These are *name-presence* checks, not secret-value validation.
REQUIRED_BOOT = frozenset({
    "SECRET_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "SECRETARYAI_DEPLOYMENT_ENV",
    "SECRETARYAI_EXPECTED_SUPABASE_PROJECT_REF",
    "INTUIT_ENVIRONMENT",
    "COMPUTER_USE_ENABLED",
    "BILLING_ENABLED",
    "SECRETARYAI_AR_PAYMENT_PREVIEW_ENABLED",
    "SECRETARYAI_EMAIL_DELIVERY_MODE",
})

# A GET-only QuickBooks sandbox acceptance requires more than successful boot.
REQUIRED_SANDBOX = REQUIRED_BOOT | frozenset({
    "REDIS_URL",
    "INTUIT_CLIENT_ID",
    "INTUIT_CLIENT_SECRET",
    "INTUIT_REDIRECT_URI",
})


def load_variable_names(path: Path) -> set[str]:
    """Accept a sanitized JSON array of variable names only, never a value map."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of variable names (not values)")
    if any(not isinstance(item, str) or not VARIABLE_NAME.fullmatch(item)
           for item in data):
        raise ValueError("Invalid variable name; do not supply values or objects")
    return set(data)


def check_names(live: set[str], staged: set[str], mode: str) -> list[str]:
    if mode not in {"boot", "sandbox"}:
        raise ValueError("Unsupported acceptance mode")
    required = REQUIRED_BOOT if mode == "boot" else REQUIRED_SANDBOX
    return sorted(required - (live | staged))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-names", type=Path, required=True,
                        help="Sanitized JSON array from Railway live variableNames")
    parser.add_argument("--staged-names", type=Path, required=True,
                        help="Sanitized JSON array of staged added/updated variable names")
    parser.add_argument("--mode", choices=("boot", "sandbox"), default="boot")
    args = parser.parse_args(argv)
    try:
        live = load_variable_names(args.live_names)
        staged = load_variable_names(args.staged_names)
        missing = check_names(live, staged, args.mode)
    except (OSError, ValueError, json.JSONDecodeError):
        print("BLOCKED: invalid name-only inputs; do not supply variable values")
        return 2

    print(f"Finance UAT {args.mode} name-only preflight: "
          f"{len(live)} live + {len(staged)} staged names")
    if missing:
        for name in missing:
            print(f"MISSING: {name}")
        print("NO-GO: required configuration names are absent")
        return 1
    print("PASS: required names present. Values, project binding, safe flags, "
          "credentials, real principals and provider parity remain UNVERIFIED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
