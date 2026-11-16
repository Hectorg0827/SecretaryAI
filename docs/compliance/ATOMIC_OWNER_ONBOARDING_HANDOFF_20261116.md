# Lion BI — atomic owner onboarding handoff (review only)

Date: 2026-11-16 | Parent security gate: draft PR #198 | **NO-GO**

## Problem

Current staging permits `service_role DELETE` on `public.companies`, which bypasses RLS and cascades to Logistics and audit evidence. Draft PR #197 removes *direct* Logistics DELETE but cannot prevent that parent-cascade route. Existing `backend/app/api/auth.py::register` previously attempted to remove partially created companies with `service_role` company DELETE. Parent privilege revocation would break that cleanup behavior.

## Code-only remediation on this branch

- `backend/app/auth/registration.py::create_company_owner_transactionally` creates a company, its safety feature defaults, current `_default_rules` policy templates and active owner **within one asyncpg PostgreSQL transaction**. On any seed or user insert error it rolls back, without parent company DELETE, and closes its connection. All values are bound parameters; permissible policy columns come from a static whitelist.
- `backend/app/api/auth.py::register` now hashes the password before opening the transaction, invokes this helper, maps only `users_email_key` violations to HTTP 409 and all unknown failures to HTTP 503, retains the issued JWT contract and best-effort audit after commit, and never uses the ordinary API `service_role` for company DELETE.
- Existing `_seed_company_defaults` remains unchanged for compatibility but is **no longer invoked by registration**. It must not be substituted for the atomic path.
- `backend/tests/test_atomic_registration.py` tests policy completeness, rollback on feature/rule/owner errors, rejected template keys, duplicate-email response and stable JWT payload.
- `scripts/test_atomic_owner_registration_integration.py` runs only against **local disposable CI PostgreSQL** guarded by explicit env/database checks. It creates synthetic records, verifies successful tenant setup and rejects duplicate email/failed seeding without leaving an orphan company. It deletes only its own CI fixture with a privileged test owner.
- `.github/workflows/ci.yml` executes the integration after the PG17 schema validation and pinned asyncpg installation.

## Explicit restrictions

This is a **staging-only review candidate**, not permission to deploy the new API route. The actual Railway staging `DATABASE_URL` connection, TLS/pooler/permissions, transaction behavior, availability and timeouts must be verified with authorized staging test accounts and an isolated disposable database first; synthetic CI is not an exact clone of the 45-version staging history. The application may still use the `service_role` for other admin operations; independent code search and access review are mandatory before revoking company DELETE.

**Do not deploy parent DELETE revocation yet.** A separately privileged offboarding/erasure mechanism with legal hold checks, dual authorization, external durable audit, proper data/backup disposal and cross-tenant replay defenses is required. Neither this branch nor draft PR #198 implements it.

The seven Compliance migrations, three historical stored-SQL provenance mismatches, and JWT role/company metadata freshness and trust-boundary concerns remain unresolved. No Supabase staging/production writes, historical migration repair, external accounting actions, PR merges or plan upgrades are authorized.

## Review gates

1. Exact head CI (backend pytest, disposable PostgreSQL integration, schema checks) + CodeQL.
2. Evidence that the existing PolicyEngine templates yield identical state to old registration, including authorization behavior.
3. Concurrent duplicate registration test (database unique constraint) and failure injection for feature/rule/owner inserts; no orphan or token on failure.
4. Validate Railway staging database connection, principal, pooler and scheme using approved synthetic accounts without changing production.
5. Implement independent erasure path, review company Delete blast radius, add additive migration via the Supabase CLI with verified bundle parity, and demonstrate caller separation before revoking permissions.
6. Resolve the Compliance migrations and provenance checks; obtain independent QA/legal approval, and **separate express staging deployment approval**.

**Current outcome: source candidate only; release NO-GO.**
