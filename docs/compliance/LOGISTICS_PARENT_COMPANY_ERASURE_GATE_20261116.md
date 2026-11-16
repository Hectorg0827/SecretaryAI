# Lion BI — company-erasure bypass acceptance gate

**Date:** 2026-11-16  
**Scope:** review-only, synthetic tests, sanitized read-only staging catalog observations.  
**Release status:** **NO-GO** (staging and production).

## Live staging metadata observed (read-only)

- `public.companies`: RLS enabled; `service_role` has DELETE permission and `rolbypassrls=true`. No `pg_policies` rows were found for `public.companies`. `anon`/`authenticated` also have table-level DELETE grants but, absent policies, RLS denies their ordinary access. The central bypass is **service_role**, not the published client roles.
- `public.logistics_events`: `service_role DELETE=true` in current staging. The staged candidate in draft PR #197 revokes direct DELETE/UPDATE/TRUNCATE on this child table but is **not deployed**.
- `public.logistics_events.company_id` has `ON DELETE CASCADE` to `public.companies.id`. Many other company-scoped evidence and audit tables also have cascading references.
- `backend/app/api/auth.py` directly performs `db.table("companies").delete().eq("id", company_id)` for orphan cleanup following registration failures. Blind revocation of `service_role DELETE` on `companies` will disrupt this existing application path until a safe atomic replacement is implemented.
- Staging migrations unchanged: 45 recorded; seven Compliance Phase 1 migrations remain unapplied; three historical recorded SQL bodies still lack exact Git-blob source matches.

## New rollback-only threat-model regression

`scripts/test_logistics_parent_cascade_bypass.sql` creates one disposable company and event after the Logistics child DELETE revocation, simulates the real company DELETE grant, then uses `SET LOCAL ROLE service_role` to delete that company. It confirms the parent and event disappear by foreign-key cascade, then **ROLLBACK** restores the entire fixture and temporary grant.

The expected output begins with **EXPOSURE**, not PASS. This is a **negative security characterization**. The CI warning is deliberate; success of the test does **not** indicate release readiness or authorize erasure.

This fixture is compatible with the repo's disposable PostgreSQL 17 CI database. It is **not** a full clone of current staging. Supabase native branching was attempted only after user consent and cost confirmation, but the platform rejected the branch because the project plan does not support branches. No paid branch, production access or remote SQL writes occurred.

## Required solution design (separate review and migration)

1. Separate company *offboarding* from privileged *erasure*. Ordinary API `service_role` may create/read/update authorized company records but must not directly DELETE a company or indirectly erase logistics or audit evidence. Route creation and registration cleanup through an atomic onboarding transaction or safe, idempotent orphan-reconciliation path that does not provide a general company-delete primitive.
2. Place actual erasure behind a separately managed, least-privileged administrative identity **not reachable from ordinary application API credentials**. Enforce independent human approval, exact tenant targeting, validated regulatory retention/hold checks, explicit scoped request idempotency and short-lived authorization.
3. Write an immutable decision/authorization receipt to a **separate protected evidence system** before and after erasure, since a company-level cascade can delete same-database `audit_events`. Review backup/replica/storage/customer legal obligations. Never claim that a receipt in the cascaded tenant database survives.
4. Stage new additive changes only after source-to-history/CLI-bundle parity and a legal/technical review. Test failure/retry, stale permissions, tenant swaps, deliberate duplicate requests, cross-tenant isolation, approval revocation and rollback. Preserve legitimate erasure and existing onboarding behavior.
5. Require exact-head CI/CodeQL success, a disposable clone independently confirmed equivalent to current staging schema, all seven Compliance migrations in order, the three historical source-provenance checks, JWT claim authority review, and separate **explicit staging deployment approval**.

## Non-actions

This audit does **not** authorize a company DELETE in staging, a production migration, `db push`, `migration repair`, a Supabase plan upgrade, QuickBooks access, filing, notifications, or PR merging. No legitimate retention duration is set without jurisdiction-specific review.

## Grant-revocation mitigation spike (not a migration)

`scripts/test_company_delete_revoke_proposal.sql` additionally tests a **rollback-only** countermeasure: revoking `DELETE` on `public.companies` from `service_role`, `anon`, `authenticated` and `PUBLIC` denies a direct service-role parent deletion, keeps Logistics SELECT/INSERT available, and leaves an authorized database-owner company cascade operational. This does **not** satisfy the production retention/erasure architecture: the existing API registration-failure cleanup still depends on direct `service_role` DELETE, and there is no approved erasure role, human/legal gate, durable out-of-band receipt or rollback-safe onboarding replacement. No SQL from this spike may be applied remotely.
