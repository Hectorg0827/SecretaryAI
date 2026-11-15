# Lola Stage 11 — Gmail bootstrap and Graph delta-token recovery

**Staging-only draft. No hosted Supabase migration applied, no production,
Railway, cron, OAuth account, webhook, or outbound-message operation changed.**

## Implemented

- New `backend/app/intelligence/lola/gmail_bootstrap.py`: server-scoped
  metadata-only full-sync collector. Fetches an authenticated Gmail
  getProfile `historyId` BEFORE listing, walks every bounded messages.list
  page and verifies each message reference by messages.get with
  `format=metadata` and `fields=id,threadId,historyId`.
- After the full snapshot finishes, consumes every users.history.list page
  starting from the **same initial Gmail historyId**. Concurrent additions,
  deletions and label changes become part of the initial replay evidence.
  The snapshot and history evidence must both fit within a hard upper bound
  (400 baseline messages, 500 total staged events, 100 listing pages and
  the existing independent 100-page history limit). Exceeding limits fails
  closed and commits nothing; no partial success is inferred.
- The authenticated `GmailBootstrapTransport` checks the employee's
  current backend registry mapping before and after every Gmail metadata
  call and checks `users.getProfile` against the independently verified
  OAuth mailbox email before listing or retrieving messages.
- The previously installed AES-256-GCM cursor vault and atomic service-role
  compare-and-swap replay writer can now commit a new **Gmail baseline**
  (with no previous checkpoint) only from the explicit
  `GmailBootstrapEvidence` type after both snapshot and history are exhausted.
  This is a baseline only: the committed cursor remains encrypted, and
  synthetic event keys remain keyed digests instead of raw message IDs.
- `email_sync_service.py` invokes the new bootstrap on a Gmail connection
  without a checkpoint. A 404 during initial reconciliation returns
  `requires_full_gmail_bootstrap_retry` without a write.
- Existing expired Gmail cursors return `requires_full_gmail_resync` and
  remain unchanged. A separate audited checkpoint reset/epoch workflow
  is still required for an expired already-persisted cursor.
- A known Graph 410 delta-token expiry is now propagated as a typed
  sanitized error and translated to `requires_graph_folder_resync` without
  modifying the prior delta checkpoint.

## Explicit evidence limits

1. This is NOT a complete lifetime history of a Gmail mailbox.
   Old deleted messages or contact replies before the first snapshot cannot
   necessarily be recovered. Initial, incremental and recovered baselines
   never alone prove that a customer has not replied.
2. The 400-message cap is deliberately protective. Larger mailboxes cannot
   be silently truncated; the current batch operation fails closed until an
   authorized windowed/paged durable ingestion design is implemented.
3. Default Gmail `messages.list` may exclude Spam/Trash and scope settings
   could omit messages. A verified full-folder coverage plan remains a
   requirement for later actionable cross-channel continuity.
4. No per-message sender, direction, recipient, timing, contact match,
   employee-approved work-item relationship or WhatsApp app-echo event is
   interpreted or exposed by this stage.
5. No provider connection loader is registered as a public route or job;
   trusted employee consent and OAuth principal enrollment must precede
   an authenticated staging provider test.
6. The Stage 8 and 10 migrations are **unapplied** to hosted databases.
   The existing Stage 10 disposable PostgreSQL RLS/consent and concurrency
   tests remain the authoritative SQL safety gate for this stacked branch.
7. The Stage 11 Gmail full sync does not automatically replace an expired
   persisted checkpoint; doing so requires separate epoch/recovery design
   and explicit authorization.

## Synthetic tests

- `backend/tests/test_lola_gmail_bootstrap.py`: anchor-before-snapshot,
  multiple pages, concurrent changes, empty mailbox, expired history,
  duplicate refs, cursor loops, missing pages, message identity drift,
  oversized mailbox and oversized history changes.
- `backend/tests/test_lola_gmail_bootstrap_transport.py`: authorized Gmail
  owner, expected API methods, metadata-only calls, revoked mapping and
  suppressed provider failure data.
- Updated `backend/tests/test_lola_replay_persistence.py`: encrypted
  first Gmail cursor, atomic event writing, no false reply or outbound
  approval, existing-checkpoint rejection and subsequent incremental replay.
- Updated `backend/tests/test_lola_email_sync_service.py`: first Gmail
  baseline, expired initial anchor, bounded-source failure and Graph 410.

## Final gates before any deployment

- Full GitHub CI + CodeQL on the final PR commit;
- provider sandbox tests for Gmail 404 and Graph 410, OAuth refresh
  durability, stale consent and revoked connection, 429/rate limits,
  observed mailbox coverage, and eventual consistency;
- verified employee-owned OAuth connection loader, official WhatsApp
  per-employee consent/app-echo, cross-channel attribution, human review;
- no automated notification or send until independently accepted.

## Official protocol references

- Gmail full/partial sync: https://developers.google.com/workspace/gmail/api/guides/sync
- Gmail history.list: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Gmail users.getProfile: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile
- Microsoft Graph delta reset: https://learn.microsoft.com/en-us/graph/delta-query-overview

**No messages sent and no production or hosted staging change made.**