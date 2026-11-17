# Lion BI — ordinary API database identity proof (staging release gate)

Date: 2026-11-16. Parent: draft PR #209.

## Why an authenticated proof is required

Simply revoking DELETE on `service_role` does not secure the tenant erasure path if the ordinary Railway API can still use its `DATABASE_URL` as `postgres`, another table owner or a role with `BYPASSRLS`. The staging `DATABASE_URL` is present but Railway's authenticated OAuth integration reveals names only and cannot prove its effective PostgreSQL privileges.

The preflight in `backend/app/auth/runtime_role_audit.py` connects as a separately provisioned **`secretary_runtime`** identity using a URL scoped to an exact Supabase project. It executes SELECT-only catalog checks; no SQL mutations. It rejects:
- Wrong `current_user` or `session_user` (including SET ROLE from a privileged connection).
- Superuser, `BYPASSRLS`, role creation, DB creation, replication, ANY role membership, and ownership of `public.companies` or `public.logistics_events`.
- Any effective `DELETE`/`TRUNCATE` capability on `public.companies`, or `DELETE`/`UPDATE`/`TRUNCATE` capability on the Logistics ledger.
- Loss of required Logistics SELECT/INSERT rights.
- Missing credentials, wrong project/role URL, malformed connection, unavailable catalog privileges.

This is only a narrow database identity audit, not full application authorization, broader SQL privilege inventory, erasure compliance, or legal-hold review. It must not be represented as a complete role deployment.

## Current isolated test coverage

CI creates synthetic `secretary_runtime` with no inherited memberships, grants only expected read/append privileges in **disposable PostgreSQL17**, and authenticates a real session with its own synthetic password. The test checks a clean pass; then gives this role `companies.DELETE` and proves refusal; revokes it and enables `BYPASSRLS`, proving refusal again; finally restores a safe role and validates a clean pass. It also verifies that owner and `service_role` DSN strings cannot be substituted before making a network connection.

All role creation, privilege changes, and tests occur exclusively in GitHub Actions' `localhost` disposable PostgreSQL, with `SECRETARYAI_TEST_DATABASE_ONLY=1`. No real Supabase secrets, accounts, tenants, grants, or roles change.

## Operational staging acceptance, not yet done

1. Existing organization `bxcsteqcocdnrjgcjulz` remains on Free; Supabase Pro branch creation was rejected. The $0/month standalone project route was separately rejected due the 2-active-free-project quota. No Supabase branch, standalone project, or invoice was created.
2. After an operator upgrades the **organization** (not a project) to a plan supporting branches, create a new isolated preview branch with fresh confirmation of the previously approved hourly cost.
3. Reconcile current staging schema and historical SQL sources. Disposable CI has **72 tables** while staging has **79**; six tables appear only in CI and 13 only in staging. Seven Compliance migrations remain absent from active staging. Do not apply current proposed SQL to active staging.
4. On a verified isolated preview, deploy independently reviewed registrar SQL into the private schema, protect default function EXECUTE, and provision a dedicated secret. Test correct atomic owner creation, duplicate failure rollback, policy parity and account JWTs.
5. Provision the distinct `secretary_runtime` role with least privilege. Pass the exact project-scoped runtime credential into the staging API while independently verifying **all** other ordinary modules; use the new authenticated audit on that identity before deploy. Never place a postgres/owner connection URL in the routine backend or its worker environment.
6. Apply a separately reviewed additive migration revoking ordinary parent companies DELETE/TRUNCATE and Logistics DELETE/UPDATE/TRUNCATE, preserving an independent lawful erasure executor. Test real attempts through the ordinary app, plus retention/legal hold, cross-tenant control, IdP, WORM evidence and rollback before considering production.

This proof is read-only and is not wired as a boot gate until a safe, actual staging runtime identity exists. **Do not enable it on current active staging with privileged DATABASE_URL**, and do not treat it as production signoff.
