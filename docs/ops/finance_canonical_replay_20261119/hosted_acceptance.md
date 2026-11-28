# Disposable hosted acceptance runbook (draft; forbidden on parent or production)

## 1. Target isolation & authorization gate
- **Never** target shared staging parent `xerktvdgjvjozvckjyfp`, `gpeytsazslopipmplixe`, `toujmmvlgktprabjbccq`, `djfcginvavwuyfxxlyjv`, or `keopiqwmcrftydjvztev` (enforced in code by `FORBIDDEN_PROJECT_REFS`).
- Note: historical disposable preview ref `ontqkqgofkghsdgtqwsm` reported `MIGRATIONS_FAILED` / `NotFound` on 2026-11-26. Before provisioning any replacement `with_data=false` preview branch, obtain explicit operator cost confirmation (`$0.01344/hour`) and export `EXPECTED_DISPOSABLE_PROJECT_REF=<new_20_char_branch_ref>`.

## 2. Read-only preflight
- Export `EXPECTED_DISPOSABLE_PROJECT_REF`, `DISPOSABLE_DB_PASSWORD`, and `DISPOSABLE_DB_CA` from a protected secret store (never echo or log credentials).
- Run `python3 docs/ops/finance_canonical_replay_20261119/hosted_preflight.py --project-ref "$EXPECTED_DISPOSABLE_PROJECT_REF"`.
- Preflight connects to `db.<ref>.supabase.co:5432` with `PGSSLMODE=verify-full` and `default_transaction_read_only=on`, failing non-zero unless `public_tables = 0`, `auth.users = 0`, and `supabase_migrations.schema_migrations` contains the exact 5 empty baseline versions (`20240101000000`..`20261017010000`).

## 3. Atomic canonical 46-migration replay
- Generate SQL offline:
  `python3 docs/ops/finance_canonical_replay_20261119/hosted_replay.py --project-ref "$EXPECTED_DISPOSABLE_PROJECT_REF" --output /tmp/disposable_replay.sql`
- `hosted_replay.py` strips balanced outer `BEGIN;`/`COMMIT;` wrappers from executable DDL (preventing premature mid-replay commits in `20261102092256`, `20261102232409`, and `20261103022426`), rejects non-transactional hazards, updates only the 5 empty baseline rows, strictly inserts the 41 remaining versions with full canonical SQL provenance, verifies all 7 `assert_schema.sql` MD5 catalog fingerprints, and asserts unbroken `pg_current_xact_id()` continuity before `COMMIT;`.
- Execute over TLS-verified `psql -X -w -v ON_ERROR_STOP=1` in the same session after setting `SET app.disposable_project_ref = '<ref>';`.

## 4. Least-privilege post-replay bootstrap & Auth hook
- Generate mode-scoped SQL offline:
  `python3 docs/ops/finance_canonical_replay_20261119/hosted_post_replay_least_privilege.py --project-ref "$EXPECTED_DISPOSABLE_PROJECT_REF" --mode rls-acceptance --output /tmp/disposable_rls_grants.sql` (or `--mode finance-uat`).
- Revokes `TRUNCATE` from `PUBLIC`, `anon`, and `authenticated` across all 78 public tables, revokes `DELETE` on `public.logistics_events` (and `public.companies` in `finance-uat` mode), installs `secretary_internal.custom_access_token_hook(jsonb)` for `supabase_auth_admin` only, and re-verifies all 7 `assert_schema.sql` fingerprints.
- Enable `secretary_internal.custom_access_token_hook` in the disposable branch's Supabase Auth Custom Access Token Hook config (never synthesize JWTs).

## 5. Authenticated acceptance & mandatory teardown
- Run reviewed PR #232 `node scripts/rls_authenticated_acceptance.mjs` from a protected runner with branch-only `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `EXPECTED_DISPOSABLE_PROJECT_REF`, and `ALLOW_DISPOSABLE_AUTH_TESTS=yes`.
- Capture pass/cleanup evidence (zero residual `companies`, `accounts`, or `auth.users`), then **immediately delete the disposable Supabase branch** to stop hourly compute charges.
