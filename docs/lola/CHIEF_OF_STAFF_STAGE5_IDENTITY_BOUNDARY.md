# Lola Stage 5 — identity registry and trustworthy provider source boundary

Status: **source-only, staging branch**. No hosted database mutations,
Railway deployment, provider credentials, live WhatsApp/Gmail/Outlook reads,
or outbound notifications have occurred.

## Added code

- `backend/app/intelligence/lola/identity_registry.py`
  - Validates the active authenticated employee against the current `users` row.
  - Restricts continuity to authorized employee-owned cases.
  - Requires exactly two independently provider-verified, actively connected,
    employee-consented identities (email and WhatsApp Business).
  - Requires a singular linked contact+work-item with separate employee
    confirmation and backend provider verification.
  - Checks revocation/expiration and rejects duplicate or cross-tenant rows.
  - Produces a private stable mapping revision for the Stage 4 `WorkScope`.
- `backend/app/intelligence/lola/continuity_service.py`
  - Calls registry authorization before any provider read.
  - Accepts only injected trusted backend readers bound to the two exact
    provider identities in the resolved case.
  - Requires complete pagination, a durable cursor checkpoint, connection
    verification and the same mapping revision on each provider snapshot.
  - Bounds the read and executes providers concurrently with a finite timeout.
  - Passes only correctly scoped events and coverage into Stage 4 policy.
  - Returns `EVIDENCE_UNAVAILABLE` rather than inventing that a user has not
    replied if records or checkpoints are incomplete.
- `backend/app/intelligence/lola/existing_private_readiness.py`
  - Read-only inventory of existing staging tables `employee_integrations`,
    `lola_employee_whatsapp_connections` and `lola_private_contacts`.
  - Avoids selecting credential tokens, phone numbers, email addresses,
    message bodies or summaries.
  - `identity_verified` on an existing private contact is a recorded flag,
    not proof of independent two-provider business-case attribution.
  - Even when a recorded WhatsApp connection is `active`, the response
    always reports `eligible_for_follow_up_engine: false` until source
    cursor completeness and a separate case mapping are verified.
- `GET /api/lola/chief-of-staff/continuity/readiness`
  - Authenticated active employee's own metadata only (current-role checked).
  - Optional UUID contact selector, no cross-employee access.
  - No-store, read-only endpoint; 503 when source evidence unavailable.
- Synthetic no-network tests:
  - `backend/tests/test_lola_identity_registry.py`
  - `backend/tests/test_lola_continuity_service.py`
  - `backend/tests/test_lola_existing_private_readiness.py`

## Existing staging schema assessed (read-only)

The connected **SecretaryAI-Staging** Supabase project already has private
Lola tables including `lola_employee_whatsapp_connections`,
`lola_private_contacts`, `lola_private_events`,
`lola_private_followup_state`, and `employee_integrations`.
Existing constraints scope private events to company, employee and contact;
the WhatsApp active-state constraint requires Meta verification,
employee consent and policy clearance metadata.

Those tables do **not** supply a documented complete provider page cursor
or independent two-channel work-item identity proof. This stage reuses them
for conservative readiness reporting instead of creating duplicate private
contacts or rewriting working migrations. No personal rows were read during
schema inspection; only column and constraint metadata was inspected.

## Data sources needed BEFORE real use

These *future* trusted-source table names are currently contracts only:

1. `lola_channel_authorizations`: company + employee + channel, verified
   provider identity ID and provider status, separate employee consent,
   verification timestamps, source revision, expiry and revocation.
2. `lola_verified_case_links`: company + employee + contact + work item,
   proven provider contact references for both channels, employee
   confirmation, independent identity verification, expiry and revision.

**The table migrations were NOT created or applied in Stage 5.** The working
container does not expose the Supabase CLI, and migration creation must follow
the installed CLI's commands and database schema validation workflow.
Until these sources exist, the resolver correctly reports unavailable.

### Write and verification authority

- Never accept client-supplied `verified`, `consented` or `connected` flags.
- Employee consent must be granted/revoked by the owning employee through
  an authenticated flow; signed provider facts are separate.
- Only official authorized connector/webhook ingestion may attest external
  identity and cursor completeness. All server-side Supabase accesses must
  be tenant+employee filtered; direct end-user table writes must be denied.
- Email/WhatsApp contact identity requires provider- and employee-backed
  evidence; matching a display name, mailbox `From` header or text is
  insufficient.
- Meta WhatsApp Business permissions, Embedded Signup, phone-number ownership
  verification and app-echo/message webhooks must be configured and reviewed
  before any production identity grant is trusted.
- No message bodies or contact phone numbers flow into the policy output;
  Stage 4 hashes source references and exposes the verified watermark.
- The `VerifiedChannelSnapshot` is a privileged adapter contract, **not**
  a cryptographically authenticated payload and must never be accepted over
  public API or populated by a general LLM.

## Remaining implementation

- Generate and review isolated staging migrations for registry and event
  checkpoints, including RLS, unique constraints, revocation semantics and
  least-privilege grants. Apply only after separate authorization.
- Wire official provider adapters to return persistent, versioned, complete
  evidence snapshots; verify page cursors and webhook authenticity.
- Implement employee consent/revocation UI with a separate provider setup.
- The employee-private metadata readiness endpoint exists; add a separate
  verified follow-up API only after source onboarding, approval of necessary
  persisted evidence, source-audit navigation and authenticated end-to-end
  staging acceptance.
- Only after those gates pass, add optional notification and execution
  paths subject to human authorization.

## No execution authority

All Stage 5 behavior is a pure read/check that may *suggest* a follow-up
after a verified no-reply interval. It cannot approve/send WhatsApp messages,
emails, invoices, prices, refunds, payments or shipment instructions.

## Test/run boundary

Stage 5 is stacked on draft Stage 4 PR #153, which is itself stacked on
Stage 3 PR #151. Do not merge or deploy without successful CI, CodeQL and
authenticated acceptance for the final head commit.