# Lola Chief of Staff — Stage 4: verified cross-channel continuity

## Implementation scope

- Pure deterministic `backend/app/intelligence/lola/continuity.py` policy engine.
- Synthetic regression coverage: `backend/tests/test_lola_cross_channel_continuity.py`.
- Stage 4 branch starts from the unmerged Stage 3 PR #151 head; it does NOT update PR #151.
- No hosted migration, provider API call, email/WhatsApp send, scheduler, user memory write, action approval, or UI route.

## Example workflow

1. The employee receives an actionable *email request* (provider-backed event).
2. The same employee sends a first draft on WhatsApp Business; the actual provider echo/confirmation is essential. A composed but unsent draft does not count.
3. The canonical contact and business work-item ID must be linked in an employee-consented, verified identity registry. The engine never links on names, subject similarity or inferred semantic resemblance.
4. Independent email and WhatsApp coverage attest that all relevant events since the original request were retrieved and the last sync is sufficiently recent. The result exposes the earlier verified coverage end (`checked_through`) and never claims observation after that point. Both sources must cover the full response deadline before a due recommendation.
5. Lola evaluates a 24-hour response window. If both channels show no response, the engine returns `REVIEW_FOLLOW_UP`. A reply through either channel returns `REPLIED`; incomplete sync, cross-tenant data, stale data, missing identity evidence or conflicting source revisions returns `EVIDENCE_UNAVAILABLE`.
6. The result is a proposed internal review only. There is NO permission to send an email, message, push notification or other external action.

## Security / correctness contract

- All records and sync receipts require exact company and employee identity; no cross-tenant events accepted.
- Contact + work-item mapping must be verified at the source, with a stable registry revision and employee consent.
- Only permitted employee WhatsApp Business accounts using eligible Meta authorization may provide trusted WhatsApp evidence.
- `outbound_delivery_confirmed` is required for any WhatsApp draft-send claim, ideally from the official app-echo and appropriate message webhooks. Locally generated drafts are not considered sent.
- The absence of a reply is meaningful only if *both* provider sources have complete coverage from the original request to within the allowed sync lag.
- Events carry provider ID, provider revision and timezone-aware observation time; conflicting IDs and out-of-window records fail closed.
- The output contains only stable internal work-item/contact IDs and tenant-scoped opaque hashes of provider source IDs; raw provider IDs (which may contain addresses), message bodies and contact details are not returned. Trusted adapters retain the raw IDs for authorized source lookup.
- No automatic outreach. Lola only proposes an authenticated employee review.
- The fingerprint changes when relevant evidence, source timestamps, sync revision or verified sync watermark changes; it is not an execution idempotency key or permission grant.

## Not yet implemented

- The contact mapping/identity registry and employee-controlled consent persistence.
- Official WhatsApp Business Embedded Signup, Meta permissions and message/app echo webhook ingestion.
- Authorization-aware Gmail/Outlook adapters returning complete, versioned event windows.
- Durable event-store tables, source-cursor checkpoints and replay policies for each channel.
- Full tenant-scoped backend API and UI, and employee-configured notification cadence.
- Server-side end-to-end identity/consent and third-party webhook acceptance tests.

Until those providers and coverage checkpoints exist, **do not call this engine with fabricated or browser-asserted coverage to enable follow-ups**. It is a testable policy kernel, not a live notification service.

## Release gates

1. Full backend CI and CodeQL for this branch.
2. Formal review of verified mapping, consent revocation, message retention, Meta platform constraints and provider deduplication.
3. Authenticated read-only staging test with permitted provider sandbox data, explicit tenant boundary and disabled roles.
4. Independent manual review of at least one real cross-channel case before any UI exposure.
5. No production deployment or communication-send authority without separate approval.