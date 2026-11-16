# Lion BI — Company-erasure Phase 1 review-only policy gate
Date: 2026-11-16 | State: **NO-GO** | Scope: staging review only, no delete executor
Stack: draft PRs #197, #198, #199, #200.

## Threat and policy context
The current staging catalog still grants ordinary service_role DELETE on both public.companies and public.logistics_events. service_role bypasses RLS, and the logistics_events company FK cascades on parent deletion. The parent-delete bypass was reproduced in disposable PostgreSQL in PR #198. Atomic onboarding and the registrar proposal have passed synthetic CI but have not been deployed.

The wholesale governance module currently embeds example retention periods (operational records 7 years, customer PII 5 years, action audit 10 years, conversations 2 years). These have not been validated for particular state, country, product, industry, legal hold or contract, and MUST NOT determine automatic company-erasure eligibility.

## Phase 1 implementation
The pure backend/app/company_erasure/preflight.py validator assesses a review evidence packet: target and request identities, current retention-law and legal-hold clearance with jurisdiction, distinct legal and security approvals (neither the requester and neither revoked), exact tenant scope including vendors/backups, and an independently acknowledged immutable audit receipt outside the company cascade. It bounds review-packet validity to a proposed 24-hour engineering freshness period, **not a legal retention period**.

**Crucial:** dataclass fields such as source_verified, identity_verified and externally_verified do NOT verify a signature or source on their own. Until server-controlled authoritative sources and attested external audit receipts are integrated, all provided evidence must be treated as untrusted. Even when every condition passes, the result is REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED and permits_deletion is **always False**. This module contains no database clients, SQL DELETE, network access, executor or authorization token.

## What must be built and independently approved next
1. Legal/regulatory decision service validating each applicable country, state, regulation, business document type, contractual schedule, active litigation/holds, and authorized data subject. Neither Lola nor generic code may invent law or retention periods.
2. Independent identity provider verifying requester, legal approver and security approver, up-to-date authorizations, company scope and role revocations. Re-evaluate immediately before any action. Never trust user-editable JWT user_metadata.
3. Durable external evidence store (WORM/equivalent independent archive) collecting request, authorizations, hold checks, scope, approvals and intent before any deletion. Confirm read-back, immutable audit, independent operator permissions, and post-result receipts. Tenant-scoped Supabase audit_events cannot survive FK cascades.
4. Separate administrative erasure service, outside web/API workers, with one-time short-lived approval capability, a scoped request/tenant fence, replay prevention, concurrency locks, cancel/hold checks, and fail-closed recovery. No routine service_role, shared DATABASE_URL, registrar, chat agent or employee has erasure capability.
5. Verified integration and secure migration rollout. Only after staging onboarding is switched to restricted registrar and every privileged connection is audited, create a NEW additive migration through the Supabase CLI to revoke routine company and Logistics DELETE while preserving independent lawful erasure. Avoid applying the review-only registrar SQL as though it were a migration.
6. Validate against an actual isolated current-staging schema clone, test seven pending Compliance migrations in order, reconcile three historic SQL provenance mismatches and Supabase JWT claim origin, complete authenticated role and staging acceptance and secure independent QA/legal signoff. A prior green synthetic CI does not authorize deployment.

## Phase 1 negative tests
Tests include: hold active/unknown, expired or wrong-tenant legal evidence, revoked, duplicate, self or single approver, wrong tenant, nonindependent identity source, expired request, missing vendor/backup plan, unverified or nonimmutable archive receipt, bad evidence digest, invalid request identifiers and an untrusted clock. A valid packet still has zero execution permission.

## No-action record
No SQL migration, company deletion, Supabase role change, external audit write, regulated communication, PR merge, staging deployment, production change or plan upgrade was performed. The current staging exposure remains open until actually fixed.
