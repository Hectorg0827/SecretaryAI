# Lion BI — Restricted Registrar Pilot / Scoped GO Criteria
Date: 2026-11-16 — Review-only staging branch.

## Scoped classification

**GO for CI and independent source review:** the atomic onboarding implementation, private registrar SQL proof, signed erasure evidence review and fresh-provider contracts have passing synthetic tests and CodeQL in their preceding draft PRs. This branch adds a staging-only switch to the already-reviewed private registrar transport.

**NOT GO for live company erasure or production:** no trusted legal-hold register, independently verified workforce IdP, WORM audit archive, administrative erasure executor, or privilege revocation is deployed. Even positive erasure packet results cannot delete data.

## Staging switch

- `SECRETARYAI_RESTRICTED_REGISTRAR_ENABLED=false` (default): existing atomic PostgreSQL registration writer remains in use. This does NOT isolate the shared backend DATABASE_URL or remove company DELETE permissions.
- `SECRETARYAI_RESTRICTED_REGISTRAR_ENABLED=true`: enabled **only when SECRETARYAI_DEPLOYMENT_ENV=staging**. Startup validation demands `SECRETARYAI_REGISTRAR_DATABASE_URL` with dedicated registrar identity and exact `SECRETARYAI_EXPECTED_SUPABASE_PROJECT_REF`. No registrar URL or password appears in validation error output.
- When enabled, `/auth/register` calls `secretary_internal.register_owner_atomic` through the restricted identity; it NEVER retries or falls back to the general `DATABASE_URL` if the key, function, role or database is unavailable. Errors return HTTP 503 without minting a JWT, customer record, or audit event. Valid unique email violations remain HTTP 409.
- The private SQL candidate is still `docs/compliance/sql-proposals/registrar_rpc_REVIEW_ONLY.sql`, **not** a deployed or versioned Supabase migration. Do not enable the flag in actual staging yet. Provisioning the role requires an independently reviewed migration, appropriate secrets and authorization.
- The broader backend may still possess privileged database credentials for other modules, so enabling this switch alone is NOT sufficient to secure company erasure.

## Evidence needed for actual staging pilot authorization

1. Produce one additive, Supabase-CLI-generated migration after fixing historical migration source-vs-ledger mismatches. Run against a disposable current-staging clone with exact schema parity.
2. Provision a separate, scoped registrar login via an operator-controlled secret manager; ensure no direct DELETE/UPDATE/TRUNCATE/SELECT on tenant tables. Verify its private RPC only in disposable and staging test environments.
3. Independently review the `SECURITY DEFINER` SQL, search path, policy-default parity, orphan rollback, duplicate email and expired/revoked employee tests. Exercise the switch with synthetic accounts.
4. Review all other privileged database connections; remove routine API company and Logistics DELETE only AFTER lawful erasure service and onboarding migration are verified.
5. Keep seven Compliance migrations, 3 provenance mismatches, JWT claims, secure erasure, external WORM/IdP/legal providers and independent acceptance gates open.

No external secrets, data, roles, migrations or deployment were changed in this draft. The user's goal is **GO**; scope-specific GO must be supported by actual evidence, not by relabeling an unsafe production release.
