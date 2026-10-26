"""Mutation-test security regressions by deliberately undoing each guard.

This is a CI script, not production code. Restores every source file in finally.
A mutant passes only if the targeted test fails for the expected reason/count.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CASES = [
    (
        "cross-tenant Computer-Use visibility",
        "backend/app/api/computer_use.py",
        'if state["company_id"] != user["company_id"]:',
        'if False and state["company_id"] != user["company_id"]:',
        "test_cu_session_survives_independent_requests",
        1,
    ),
    (
        "connector write approval bypass",
        "backend/app/api/connectors.py",
        'if body.task_type in ("create_invoice", "create_po", "update_item"):',
        'if False and body.task_type in ("create_invoice", "create_po", "update_item"):',
        "test_unapproved_write_never_enters_connector_queue",
        3,
    ),
    (
        "connector plaintext-secret regression",
        "backend/app/api/connectors.py",
        '"connector_install_secret": "sha256:" + hashlib.sha256(raw_secret.encode()).hexdigest(),',
        '"connector_install_secret": raw_secret,',
        "test_connector_secret_digest_only_at_rest",
        1,
    ),
    (
        "Redis token revocation fail-open",
        "backend/app/auth/jwt.py",
        '''def is_token_revoked(jti: str) -> bool:
    """Return True if the JTI is on the Redis blacklist."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        return r.exists(f"{_REVOKE_PREFIX}{jti}") == 1
    except Exception as exc:
        _revocation_alarm("is_token_revoked", exc)
        raise RevocationStoreUnavailable("Redis revocation store unavailable") from exc''',
        '''def is_token_revoked(jti: str) -> bool:
    """Return True if the JTI is on the Redis blacklist."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        return r.exists(f"{_REVOKE_PREFIX}{jti}") == 1
    except Exception as exc:
        _revocation_alarm("is_token_revoked", exc)
        return False''',
        "test_redis_revocation_outage_always_blocks_and_emits_alarm",
        1,
    ),
    (
        "production login throttle fail-open",
        "backend/app/utils/rate_limiter.py",
        "if not settings.debug:",
        "if False and not settings.debug:",
        "test_production_login_limiter_fails_closed_without_redis",
        1,
    ),
    (
        "production request throttle fail-open",
        "backend/app/middleware/rate_limit.py",
        "if not get_settings().debug:",
        "if False and not get_settings().debug:",
        "test_production_middleware_rejects_requests_without_redis",
        1,
    ),
]


def main() -> None:
    for name, filename, needle, mutant, selector, expected_failures in CASES:
        path = ROOT / filename
        original = path.read_text(encoding="utf-8")
        if needle not in original:
            raise RuntimeError(f"Mutation target moved: {name}")
        mutated = original.replace(needle, mutant)
        try:
            path.write_text(mutated, encoding="utf-8")
            run = subprocess.run(
                [
                    sys.executable, "-m", "pytest", "-q",
                    "tests/test_phase4_release_security.py", "-k", selector,
                ],
                cwd=ROOT / "backend", text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=120, check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        failures = re.findall(r"(\d+) failed", run.stdout)
        if run.returncode == 0 or not failures or int(failures[-1]) != expected_failures:
            print(run.stdout[-3000:], file=sys.stderr)
            raise RuntimeError(
                f"Mutation NOT killed correctly: {name}; exit={run.returncode}, "
                f"expected {expected_failures} failed"
            )
        print(f"PASS: {name} mutant killed by {expected_failures} failing test(s)")
    print("PASS: all Phase 4 security mutants were killed")


if __name__ == "__main__":
    main()
