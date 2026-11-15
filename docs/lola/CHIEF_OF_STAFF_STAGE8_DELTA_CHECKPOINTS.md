# Lola Stage 8 — Gmail history, Graph folder delta and atomic checkpoint contract

Status: staging-only source code stacked on Stage 7. No merge, Railway
deployment, hosted Supabase DDL/DML, provider message sends, notifications
or production changes.

## 1. Provider replay

- `backend/app/intelligence/lola/delta_replay.py` implements bounded
  Gmail `users.history.list` replay from a saved `startHistoryId`, walking
  every `nextPageToken`, validating increasing (not necessarily contiguous)
  history record IDs, token continuity and a terminal `historyId`.
- Expired Gmail history (404) is a full-sync requirement, never silence.
- Graph uses per-folder `messages/delta`, NOT `/me/messages` listing.
  Every continuation and final delta URL must remain on the exact
  authenticated Graph origin and mail folder. A terminal
  `@odata.deltaLink` is required; pagination gaps and malicious
  links fail closed. Each Graph folder has its own independent cursor.
- `DeltaCandidate` stores internal cursor and changed IDs with hidden
  representations and a tenant/employee/connection/folder-bound digest.
  It does not claim contact identity, WhatsApp coverage or no-reply.

## 2. Authorized read-only provider transports

- `backend/app/connectors/lola_delta_readers.py` injects EXISTING
  authorized Gmail/Outlook connectors and requires a fresh server-side
  employee consent/authorization check before EVERY page read.
- Gmail calls users.history.list via the existing Google connector and
  translates 404 to an expired-history error.
- Outlook uses the existing connector's authenticated Graph headers and
  GET requests with redirects prohibited, fixed folder scope and
  strict 404/410 expiration handling.
- The adapters are NOT mounted as endpoints or scheduled jobs.

## 3. Atomic checkpoint contract

- `backend/app/intelligence/lola/delta_checkpoint.py` defines a
  backend-only AtomicRoundStore protocol. An implementation MUST commit
  all deduplicated event changes and the terminal token in ONE transaction,
  check the exact tenant+employee+provider+folder+mapping revision and
  optimistic prior version/cursor, and recheck current employee consent.
- An accepted transaction must be read back from the primary store.
  Stale, ambiguous or partially applied commits never qualify.
- Gmail requires a prior persisted history baseline. Graph may start a
  new per-folder delta without an earlier cursor.
- A successful outcome represents a bounded provider delta round only.
  It never grants permission for a follow-up or asserts no reply.
- **No concrete hosted database writer is installed yet.** The tests
  use synthetic in-memory atomic behavior, not actual Supabase storage.

## 4. Regression coverage

- `backend/tests/test_lola_delta_replay.py`: Gmail cursor expiry, change
  ordering, Graph folder scope, token loops, malformed pages, source privacy.
- `backend/tests/test_lola_delta_readers.py`: fresh consent on each page,
  revocation, safe transport and no provider error-body exposure.
- `backend/tests/test_lola_delta_checkpoint.py`: atomic CAS and primary
  readback, simulated concurrency, ambiguity and partial-write failure.

## 5. Still required for end-to-end authorized use

1. Implement a real least-privilege durable checkpoint/event-batch store
   through a reviewed Supabase migration and atomic transaction RPC.
   Enable RLS, uniqueness, tenant/employee authorization, encrypted opaque
   cursors, immutable event provenance and safe resync after expiry.
   No migration was generated or applied here: the required Supabase CLI
   was not available in this session.
2. Authenticate actual employee-owned Gmail/Outlook connections, perform
   initial baselines and preserve the correct folder/sent-message coverage.
3. Complete official Meta WhatsApp Business onboarding, required
   permissions, app-echo verification, signed webhooks, consent/revocation,
   durable dedupe and missed-event recovery.
4. Implement the Stage 5 verified contact/work-item mapping registry and
   authenticated staging acceptance for cross-channel replies and no-reply.
5. Obtain separate approval before any staging deployment; production
   and outbound messaging are explicitly out of scope.

## Official reference documentation

- Gmail sync: https://developers.google.com/workspace/gmail/api/guides/sync
- Gmail history: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Graph delta: https://learn.microsoft.com/en-us/graph/delta-query-messages
- Meta webhooks: https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages

Stage 8 is a source-only, auditable protocol and persistence boundary.
It does NOT yet provide live, autonomous cross-channel monitoring.