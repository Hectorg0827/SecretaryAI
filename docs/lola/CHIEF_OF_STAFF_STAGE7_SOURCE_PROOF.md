# Lola Stage 7 — authenticated provider source evidence (staging / draft)

## Implemented in code

- `backend/app/intelligence/lola/source_proof.py`
  - Parses **Gmail messages.list** metadata (`messages`, `threadId`, opaque
    `nextPageToken`), never treats `resultSizeEstimate` as a complete total.
  - Parses **Microsoft Graph /me/messages** listing metadata and preserves
    the returned `@odata.nextLink` verbatim. Strictly rejects continuation
    URLs with an unexpected origin, mailbox path, credentials or fragment.
  - Validates contiguous page-to-page cursor hand-off, exhaustion, unique
    provider message identifiers and bounded page/message counts.
  - Hashes the exact authorized page walk and matches it to a distinct
    backend-sourced durable checkpoint for the SAME tenant, employee,
    provider identity and verified mapping revision.
  - Requires independent change-history replay through the checked watermark
    to be gap-free and durably recorded; rejects stale, missing, future or
    ambiguous sync evidence.
  - Returns opaque source digests, timestamps and counts; never provider IDs
    or message bodies. It NEVER claims contact mapping, no reply or authority
    to send any message.
- `backend/app/intelligence/lola/whatsapp_source_proof.py`
  - Verifies the raw Meta WhatsApp Business webhook POST HMAC signature
    `X-Hub-Signature-256` using an app secret before parsing JSON.
  - Enforces the expected verified WABA and business phone-number IDs.
  - Extracts bounded source identity, timestamp and metadata for signed
    inbound `messages` and outbound `statuses` fields only. No text, names,
    sender phone numbers, contacts or credentials are returned.
  - Generates stable keyed opaque source references for deduplication by
    a *future* durable store. It does not persist the events itself.
  - Signed `statuses` are not proof that an employee sent a message through
    the WhatsApp Business app, nor that a first draft was delivered.
  - The parser intentionally rejects other webhook fields including
    app-echo fields until their actual sanctioned schema and permissions
    are independently reviewed. It does not infer missing replies.
- Synthetic zero-network regression suites:
  - `backend/tests/test_lola_source_proof.py`
  - `backend/tests/test_lola_whatsapp_source_proof.py`

## Boundaries that are NOT solved by this commit

1. These are pure authenticated-adapter **input-verification functions**.
   They are NOT installed webhook endpoints, provider subscriptions,
   actual API syncs, end-user UI actions or background jobs.
2. A signed webhook provides source authenticity, not proof of exhaustive
   history or absence of missed events. Timestamped webhook observations
   cannot be used alone to assert that a customer has not replied.
3. A Gmail `messages.list` page walk does NOT capture concurrent mailbox
   changes. A true checkpoint requires using Gmail change-history APIs
   with a valid persisted history cursor and proper recovery from expired
   history tokens.
4. Microsoft Graph `messages/delta` is a **separate per-folder protocol**
   from `/me/messages` listing. Its opaque `@odata.nextLink` and terminal
   `@odata.deltaLink` must be separately consumed and persisted per folder.
   Neither that protocol nor a durable cursor store has been implemented.
5. The trusted checkpoint flag in this internal dataclass is a connector
   integration contract, not a cryptographically signed receipt. Never
   accept it from a browser, chat transcript or LLM output.
6. The future complete WhatsApp source needs official Meta permissions,
   Embedded Signup, consent/phone ownership, signed message/app-echo
   subscriptions, a durable replay/dedup store, and an independently
   verified full-history boundary. If that cannot be achieved, Lola must
   keep `EVIDENCE_UNAVAILABLE` rather than fabricate no-reply.
7. No provider-backed synchronization has been run or demonstrated.
   The existing Stage 6 timeline and Stage 5 readiness endpoints still
   intentionally report unverified provider coverage.

## Security and deployment

- No OAuth token logging, private message/body logging, raw provider IDs
  in returned proof, or external write.
- No Supabase migration created or applied, no Railway deployment, no
  production changes, no WhatsApp/email messages sent.
- The Stage 7 branch is stacked on draft Stage 6 PR #155 and remains
  draft until CI and CodeQL run green on its final head.
- Next gate: implement actual **backend-only** durable cursor storage and
  scoped Gmail history/Graph folder-delta readers; test replay, drift,
  revocation, cross-tenant and expired cursor recovery against staging
  provider sandboxes before enabling cross-channel follow-up claims.

## Platform references reviewed

- Microsoft Graph delta query for messages:
  https://learn.microsoft.com/en-us/graph/delta-query-messages
- Microsoft Graph delta-query overview:
  https://learn.microsoft.com/en-us/graph/delta-query-overview
- Meta WhatsApp Business webhook HMAC verification (official, archived SDK):
  https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/

**Important:** This is a meaningful verification kernel, not a complete
connected Lola agent. No source or user permission is invented.