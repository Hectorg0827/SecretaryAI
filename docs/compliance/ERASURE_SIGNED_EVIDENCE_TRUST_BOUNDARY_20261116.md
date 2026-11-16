# Lion BI — Signed Company-Erasure Evidence Verification (review-only)

**Date:** 2026-11-16  
**Branch:** stacked after draft PR #202  
**Status:** NO-GO — no deletion executor, provider connection, audit writing, database migration, merge, or deployment

## What changed
The Phase 1 review packet in PR #202 previously accepted caller-provided Boolean fields such as `source_verified=True`, `identity_verified=True`, or `externally_verified=True`. Such flags by themselves are not authenticity proofs and could be forged by an untrusted caller.

`backend/app/company_erasure/signed_evidence.py` now requires independent **Ed25519 signatures** on every full evidence record, bound to both the exact `request_id` and `company_id`, an issuer key ID, evidence type and timestamp. The canonical JSON payload is versioned and domain-separated, and no issuer private key or signing function exists in the application. Different legal, workforce, case management, inventory-scope and external-archive issuer domains must use different public keys. Unknown roots, fake signatures, stale or future records, swapped requests or tenants, duplicate cross-domain key material and malformed encodings fail closed.

The independent archive receipt must attest to a **SHA-256 commitment of the entire signed case evidence** (request, retention, legal hold, all workforce approvals and vendor/backup scope). A valid signature on an unrelated or stale receipt cannot satisfy this check. The receipt itself is excluded from the digest to avoid a circular reference.

When all signatures verify, the existing review-only policy gate still checks jurisdiction/hold clearance, two independent nonrevoked legal/security approvals, tenant scope and receipt content. The highest possible result remains **REVIEW_PACKET_COMPLETE_NOT_AUTHORIZED**, with `permits_deletion=False` permanently. The code exposes no SQL, network operation, role grant, data deletion command, service account or authorization token.

## Trust anchors are NOT yet provisioned
No actual records have been signed by legal counsel, an identity provider or an external archive. No production key IDs, public keys, HSM/secret manager entries, provider integrations or external archive certificates have been provisioned. All keys used by tests are generated **ephemerally inside synthetic unit tests**.

A cryptographically valid signature proves only that the holder of a trusted issuer key signed the specified claims. It does NOT prove the claims are legally sufficient, current under an independently consulted legal-hold register, or that a record is physically immutable in external storage.

Before using the verifier in a deployed service, identity and permission status must be read from a known authoritative workforce IdP, legal holds and retention from independently reviewed source systems, and archive receipts obtained by authenticated read-after-write from a genuinely independent WORM/equivalent store. Trust roots must come from **server-controlled configuration**, not an API request, LLM, user-editable JWT metadata, tenant settings, or the database being erased. No erasure decision may be made with a cached-only signature or without fresh action-time revocation checks.

## Security acceptance still outstanding
1. Independent review of cryptographic format, trust-key rotation/revocation, attestations, canonicalization, replay assumptions and external archive binding.
2. Connect actual supported legal, workforce and archive providers with separate credentials; test outages, expired certificates, signer revocation, operator compromise and tenant replay.
3. Design an entirely separate administrative executor with dual human approval recheck, one-time authorization, tenant-level idempotency, legal-hold cancellation, durable external journal and backup/vendor reconciliation. The current verifier never issues an execution token.
4. Remove broadly privileged application `DATABASE_URL` and `service_role DELETE` only after registrar transition and separate erasure access are independently tested.
5. Resolve the seven pending Compliance migrations, three historical SQL source mismatches, Supabase/app JWT trust boundary, exact-staging isolated schema clone tests and independent staging QA; obtain express staging deployment approval.

**Safety record:** Existing Supabase staging and production databases remain unchanged. No company records were erased or archived, no external legal or workforce approvals solicited, and no PR was merged.
