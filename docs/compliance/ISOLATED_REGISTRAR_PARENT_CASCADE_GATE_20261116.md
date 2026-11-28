# Lion BI — Isolated Registrar and Parent-Cascade Privilege Gate

**Date:** 2026-11-16  
**Scope:** isolated, synthetic PostgreSQL 17 validation; draft branch only  
**Production/staging action:** **NONE**. Do not apply review-only SQL to active staging.

## User-authorized objective

1. Provision and validate a dedicated registration identity in an **isolated** database environment.
2. Remove ordinary application authorization to DELETE a parent company and cascade-erase tenant records, preserving independent lawful erasure.

This draft implements both behaviors in a disposable PostgreSQL 17 GitHub Actions database only. It does **not** claim to have created a persistent isolated Supabase branch or modified the active SecretaryAI-Staging database. The Supabase branch-creation service demanded a **cost confirmation ID** before it would create an isolated branch; paid provisioning has not been approved.

## Verified staging facts (read-only)

- Supabase isolated staging project: `xerktvdgjvjozvckjyfp`. A separate `secretaryai` Supabase project exists and must NOT be touched by this phase.
- Existing staging has `service_role` DELETE privileges on `public.companies` and `public.logistics_events`.
- `logistics_events.company_id` and numerous additional business and audit tables have an ON DELETE CASCADE FK to `public.companies`.
- `secretary_registrar` does NOT exist in existing staging.
- An ordinary API process has a configured `DATABASE_URL`; Railway's connected API returns variable **names only**, so the actual database principal and granted privileges for that secret are NOT verified. Privilege revocation on `service_role` alone cannot close this risk while a shared database-owner secret remains reachable from ordinary application workers.
- The seven Compliance migrations are absent in existing staging; the existing schema source and recorded migration history require reconciliation. A green disposable-CI run is not a parity-clone acceptance.

## Isolated test procedure and invariant

The GitHub Actions disposable `postgres:17` job already runs the entire schema fixture, registrar proposal, no-login check, and synthetic registration integrity tests. New additions:

1. `docs/compliance/sql-proposals/parent_company_runtime_revoke_REVIEW_ONLY.sql` applies a standalone **review proposal** only inside this CI database: REVOKE DELETE/TRUNCATE on parent `public.companies`, and REVOKE DELETE/UPDATE/TRUNCATE on `public.logistics_events` from ordinary `PUBLIC, anon, authenticated, service_role`. It checks no destructive privileges remain for those identities or the isolated registrar, and that normal event SELECT/INSERT grants survive.
2. CI enables `service_role` LOGIN with an obvious synthetic, GitHub-job-local password. This is never provisioned in Supabase. It is **not** an ordinary authentication model for Supabase Data API.
3. `scripts/test_registrar_and_runtime_parent_delete_integration.py` connects via **two separate actual PostgreSQL roles**: restricted `secretary_registrar` and ordinary `service_role`; an independent `postgres` fixture admin is retained only in the disposable test. Registrar creates company, owner, security features and five policy defaults atomically. The ordinary session attempts direct parent DELETE/TRUNCATE and direct Logistics DELETE/TRUNCATE, all denied by PostgreSQL. A registrar direct parent DELETE is also denied.
4. The ordinary role must still SELECT and INSERT Logistics events. The test checks tenant company, owner, policies and historical Logistics events remain intact after all denied attacks.
5. A separate *privileged fixture transaction* demonstrates that the database owner retains an administrator-only company erasure path; that example is **rolled back** and the synthetic tenant exists until CI cleanup. Actual lawful erasure still needs its own separately authorized service with approvals, retention decisions and immutable audit records.
6. The fixture refuses to run unless `SECRETARYAI_TEST_DATABASE_ONLY=1`, `PGHOST=localhost`, `PGDATABASE=sai`, `PGUSER=postgres`. No Supabase or Railway credentials are passed into the test job. The entire database disappears with the CI runner.

## Important security and release distinction

This tests the *correct PostgreSQL permission mechanism*, not the effective live security posture. Revoking only ordinary `service_role` privileges does NOT prevent a backend still holding a `postgres`/owner `DATABASE_URL` from deleting companies. The live ordinary backend's access to elevated database credentials **must be audited and removed**; privileged erasure authority must live outside the routine API and be separately approved.

The proposed registrar uses a private, review-only `SECURITY DEFINER` registration function. Real deployment requires external security review of function owner, session_user check, schema search path, public EXECUTE revocation, and secret handling. Only a CLI-generated additive migration with reconciled existing migration history can deploy it.

**Do not** remove live company delete grants before confirming there is no registration cleanup path that requires them and an independent legal erasure executor exists. An unsafe rollback that re-grants ordinary company DELETE is prohibited. On failure, keep registration disabled/fail closed while fixing the dedicated pathway.

## Blockers to persistent isolated Supabase staging test

- Supabase branch creation requires cost consent through a separate branch quote / confirmation. No paid branch exists yet.
- The real staging schema must be cloned or recreated without customer data, and verified for source history parity (45 recorded migrations, 7 pending and 3 mismatches noted separately).
- The isolated branch needs a separately provisioned registrar identity and secret, not the synthetic CI password.
- A staging-only app deployment must use the opt-in restricted registrar flag and pass direct/failed/duplicate registration, JWT, policy-seed, integration and tenant boundary tests.
- To finally secure ordinary API workers, review its actual `DATABASE_URL` principal and all other privileged connections; migrate them to least-privilege roles. Verify `service_role` parent DELETE revocation and that **no owner credential** is accessible to the ordinary API/worker.
- Actual independent legal retention/hold, workforce IdP, WORM archive provider and separately authorized erasure executor remain required for lawful real erasure, with interruption/replay and rollback tests.

**Release classification:** isolated-CI security verification may earn GO after exact-head passing tests. Persistent isolated staging, active staging permission changes, and production remain individually unverified until their own evidence exists.
