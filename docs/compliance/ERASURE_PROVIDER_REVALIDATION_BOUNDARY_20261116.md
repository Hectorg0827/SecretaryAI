# Lion BI — Company-erasure trusted-provider recheck contract (review-only)

**Date:** 2026-11-16  
**Parent:** Draft PR #203, signed evidence review  
**Status:** **NO-GO — no live provider integrations, no erase executor, no staging/production changes**

## Current repository finding
Code search of the default repository did not locate a configured company-erasure legal hold/retention service, workforce authorization provider, external evidence archive, or complete offboarding handler. Existing company-scoped Supabase audit events cannot independently survive an authorized company-level cascade. We MUST NOT treat test doubles or the booleans inside signed dataclass records as a source of live regulatory authority.

## This PR's implementation

`backend/app/company_erasure/provider_recheck.py` defines **five independently authenticated read-only provider contracts** that *must* be supplied by the future controlled deployment:

1. `CaseSource.current_case` — recheck exact request, company, initiator and active status; cancelled/withdrawn requests block.
2. `LegalSource.current_retention` and `current_hold` — recheck the complete jurisdiction set, references and clear outcomes against authoritative legal/hold registers **at review time**.
3. `WorkforceSource.current_identity` — recheck the requester and each independent legal/security approver for active status, role scope, tenant and revocation.
4. `InventorySource.current_scope` — verify the exact approved vendor, data and backup disposition inventory; unexpected changes block.
5. `IndependentArchiveSource.readback` — require authenticated read-after-write of exact tenant, request, evidence ID, signed-evidence commitment digest, external archive name, durability and immutable status.

**Execution sequence:** the PR #203 Ed25519 verifier runs first, using separately trusted issuer public keys; any invalid signature stops **before provider calls**. Missing providers, exceptions, network timeouts, stale (>5 minutes) or future observations, mismatched tenant/request, revoked approvers, a newly active hold, a cancelled case, changed vendor scope or an archive record missing exact digest will produce **BLOCKED**. The five-minute observation freshness bound is engineering policy, **not** a legal retention or warranty rule. No untrusted provider exception data is copied into results or logs.

The maximum successful outcome is `REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED` with `permits_deletion=False`; even signed and independently rechecked evidence **cannot erase records**. There is no SQL, DELETE, grant, network adapter implementation, provider secret, archival write, token issuance, or new API route. This code is not deployed.

## What real integrations still require

- Operator chooses and approves *actual* legal case/hold register and counsel-verified retention authority; country/state/product rules and freezes must be validated by people. The hard-coded years in wholesale governance must not substitute for this.
- IT/security independently configures a separate trusted workforce IdP. Verify roles and revocations via its authenticated server-to-server API at action time; never rely on user-editable JWT metadata or stale role claims.
- Compliance/IT selects an out-of-database WORM or equivalent externally administered archive, defines object-lock/retention rules, restricted roles and durable receipts. Test the provider's read-after-write guarantee, outages, revocation and external audit readback.
- The case system and full tenant asset/vendor inventory must be authoritative, versioned, tenant-scoped and independently operated.
- Use provider-specific certified adapters with pinned endpoints, mTLS/OAuth credentials in dedicated secret managers, strict timeout/retry discipline, least-privilege read scopes, no redirect to untrusted hosts and independent source security review. The `SourceBundle` is passed only by trusted server construction, never by a user/client or an LLM.
- The case coordinator still cannot authorize deletion; separately review and build the administrative executor with a one-time tenant-scoped authorization, atomic replay fence, fresh mandatory legal/identity checks, durable out-of-band journal, and cross-vendor/backup deletion-or-restriction lifecycle.
- Audit and replace overprivileged ordinary application `service_role` and shared DATABASE_URL usage; coordinate registrar transition and direct company/Logistics DELETE migration *after* lawfully scoped executor approval.

## Test evidence and remaining release blockers
`backend/tests/test_company_erasure_signed_evidence.py` now uses only local `AsyncMock` source adapters and ephemeral signing keys to test positive **review-only** completion and negative cases for missing providers, bad signatures (and no downstream I/O), cancelled cases, new legal holds, stale legal source, revoked/wrong-tenant current identities, vendor/backup drift, failed archive readback and provider outage.

All code is a **draft review candidate**. Independent cryptographic and legal review, live provider onboarding, external immutable archive verification, correct migration history, the seven Compliance migrations, three unmatched historical SQL source migrations, app/Supabase JWT authorization provenance, exact staging schema clone, full CI/CodeQL, authenticated staging acceptance and separate deployment approval are still required. No company deletion, external provider invocation, staging or production change or merge is authorized.
