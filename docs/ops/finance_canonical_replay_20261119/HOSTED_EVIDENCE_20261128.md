# Hosted disposable replay + real-Auth tenant acceptance — evidence (2026-11-28)

Operator approval: given in session on 2026-11-28 ("approve the Supabase test").
Target: disposable preview branch `ontqkqgofkghsdgtqwsm` (`with_data=false`,
parent `xerktvdgjvjozvckjyfp`, PostgreSQL 17.12). Reused the existing empty
2026-11-26 branch instead of provisioning a new one. **Deleted after the run.**

Transport: Supabase Management API `POST /v1/projects/{ref}/database/query`
(direct port 5432 is unreachable from the runner). The API executes each
request as one multi-statement batch.

| Step | Result |
|---|---|
| Read-only preflight | PASS: db `postgres`, 0 public tables, 0 `auth.users`, exactly 5 empty baseline versions |
| `hosted_replay.py` (46 canonical, single transaction, 7 catalog fingerprints, byte provenance) | PASS, committed: 46 versions with SQL, 78 public tables, 78/78 RLS enabled |
| `hosted_post_replay_least_privilege.py --mode rls-acceptance` | PASS. Independently verified: 0 tables TRUNCATE-able by `anon`/`authenticated`; no DELETE on `logistics_events` for `service_role`/`authenticated`; no `companies` DELETE for `anon`/`authenticated`; hook EXECUTE only for `supabase_auth_admin` |
| `tenant_rls_rollback.sql` (synthetic claims, rolled back) | PASS: `ALL_RLS_ASSERTIONS_PASSED`; 0 companies/users/accounts/conversations left |
| Custom Access Token Hook enabled via Management API auth config | `pg-functions://postgres/secretary_internal/custom_access_token_hook` |
| `scripts/rls_authenticated_acceptance.mjs` (PR #232, real Supabase Auth sessions) | PASS: own read, foreign read, anonymous read, reassignment, foreign delete, own insert/update, foreign insert, persisted ownership; fixture absence verified |
| Post-run residue | 0 companies, 0 accounts, 0 users, 0 `auth.users` |
| Teardown | Branch `c02999ba-…` deleted; runner network allowance removed |

Runner adaptation (no change to the checked SQL): `tenant_rls_rollback.sql`
uses a session TEMP table before an explicit `BEGIN … ROLLBACK`. Over the
batch API that ROLLBACK also discards the TEMP table, so the ACL snapshot was
taken in a prior committed request into a private, PUBLIC-revoked scratch
schema `rls_eval_scratch` (dropped afterwards, verified absent).

Not covered here: `finance-uat` mode, live QBO reconciliation, independent
security review, production cutover of the runtime/registrar identities.
