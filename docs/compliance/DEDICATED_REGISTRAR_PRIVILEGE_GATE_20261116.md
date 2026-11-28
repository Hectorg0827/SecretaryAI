# Lion BI — Dedicated Registrar Privilege Gate (review only)

**Date:** November 16, 2026  
**Staging/production:** **NO-GO** — no migrations applied; no new role or credentials provisioned  
**Stacked on:** draft PR #199 atomic owner registration, PR #198 parent-cascade bypass, PR #197 Logistics direct DELETE

## Why PR #199 alone is insufficient
The atomically correct onboarding implementation in PR #199 uses the backend's shared `DATABASE_URL`. The existing deployment configuration may have a broadly privileged PostgreSQL identity on that connection. Revoking the PostgREST `service_role` DELETE grant cannot eliminate the company-erasure attack surface if the same API runtime has access to an unrestricted database owner credential.

Staging catalog (read-only, 2026-11-16):
- The `public.companies`, `users`, `policy_rules`, `company_features` tables are owned by `postgres`; RLS enabled, not FORCE RLS.
- `secretary_internal` is an existing private schema owned by `postgres`, with no PUBLIC USAGE.
- No `secretary_registrar` role exists on the connected staging project.
- Ordinary `service_role` still has direct DELETE on `companies` and `logistics_events`.

## Proposed authorization boundary

1. **Private database identity:** create `secretary_registrar NOLOGIN NOINHERIT` and grant only USAGE on `secretary_internal` and EXECUTE on a named `secretary_internal.register_owner_atomic(text,text,text,text)`. Grant **no direct SELECT/INSERT/UPDATE/DELETE/TRUNCATE on any tenant table**, no BYPASSRLS, superuser, create-role or inheritance. Out-of-band secret provisioning would make this role LOGIN only after separate approval; the GitHub CI fixture enables LOGIN with an obviously synthetic local-only password.
2. **One narrow SECURITY DEFINER function, in an unexposed schema:** pinned `search_path=pg_catalog, pg_temp`; checks `session_user='secretary_registrar'`; generates the tenant and owner IDs internally, never accepting caller-specified tenant/role; seeds the same five default policies as `app.domain.policy._default_rules`; one SQL call commits all records or none. Revoke default PUBLIC function EXECUTE and prohibit all routine PostgREST roles from executing it. Treat this as elevated code: independently review function owner, grants, dependencies and error handling.
3. **Opt-in application transport:** `backend/app/auth/registrar_rpc.py` validates the dedicated role and expected Supabase project ID before connecting. It never falls back to general `DATABASE_URL`, never exposes a DELETE, and is **not wired to the registration route yet**. For non-local use, `expected_project_ref` is mandatory. No runtime credential was configured.
4. **No company erasure in this registration component.** A separate offboarding process must perform verified regulatory retention/hold evaluation, two-person approval, scoped one-time action, credential separation, deletion receipts stored outside the cascaded database, backup/storage lifecycle and interruption/retry testing. Only a dedicated isolated administrative service may hold that erasure credential, not a web/API worker.
5. **Only after 1–4 are independently signed off:** wire `/auth/register` to the registrar transport; remove the legacy broad-DSN registration option; review all other API usages of the broad `DATABASE_URL`; revoke application `service_role` company DELETE via a new additive CLI-generated migration; test for cascading bypass and that approved separate administrative erasure still works.

## Proposal artifacts and CI scope

- `docs/compliance/sql-proposals/registrar_rpc_REVIEW_ONLY.sql` is **not a migration**. The Supabase CLI was unavailable in this environment. A real new migration must be generated with the CLI, source-reconciled, reviewed and ordered after the 45 recorded staging versions and the seven pending Compliance changes, with no modification of historical migration files.
- `scripts/test_registrar_privileges.sql` validates no client-role RPC execution and no direct registrar table DML.
- `backend/app/auth/registrar_rpc.py` and `backend/tests/test_registrar_rpc.py` check role/host scoping, fail-closed credential errors, and no broad DSN fallback.
- `scripts/test_registrar_rpc_integration.py` connects to the **CI local PostgreSQL** as the synthetic dedicated role. It checks table DELETE/INSERT rejection, atomic onboarding, exact policy/feature parity with the existing PolicyEngine, and rollback after duplicate email.
- CI runs the proposal **after exporting the disposable-schema manifest** to preserve the baseline comparison and never calls a live Supabase project.

## Blocking issues and acceptance

The proof-of-concept function is privileged, even though private. Do not deploy it before independent security review of `SECURITY DEFINER` behavior, defaults/constraints, concurrent registration, arbitrary input, cross-tenant access and PostgreSQL role/membership inheritance. It has not been tested against the current 45-version staging schema clone. RLS/JWT claim provenance and three historical migration-source mismatches remain unresolved. Seven Compliance migrations are still pending actual staging acceptance.

**Crucial:** deploying only this dedicated registrar does **not** remove general `DATABASE_URL` or `service_role` privileges from the rest of the API runtime. Until runtime secret isolation and all privileged routes are audited, the system remains able to perform company-level destructive actions. Do not claim that the end-to-end company-erasure boundary is secured.

No production/staging credentials, DDL, deletions, app deploy, merge, accounting operations or regulator actions were performed.
