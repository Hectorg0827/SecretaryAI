# Lola Stage 8 — Gmail history, Graph folder delta and atomic checkpoint schema

**Status:** development branch / stacked draft only. Do not apply hosted migration,
deploy Railway, connect live provider credentials or send any outbound messages.

## Read-only provider replay kernel

`backend/app/intelligence/lola/incremental_replay.py` provides internal async
replay readers. A caller injects a trusted authorized backend fetcher; it MUST
verify the employee's current tenant, connection, permissions and exact mailbox
before every provider request. A browser/LLM cannot supply these fetchers.

### Gmail

- Collects every `users.history.list` page using the SAME server-stored
  `startHistoryId` plus each exact `nextPageToken`.
- Parses message-added, message-deleted and label-change metadata without
  fetching message bodies or contact names.
- Treats history IDs as monotonically increasing, NOT consecutive.
- Rejects incomplete pages, regressing watermarks, malformed IDs, loops,
  duplicates, oversized page walks and provider request failures.
- The trusted transport MUST distinguish an actual Gmail HTTP 404 caused by
  an expired/invalid history cursor and raise `GmailHistoryExpired`.
  Only a separate authorized full synchronization can re-bootstrap; an expired
  cursor is NOT proof that no message arrived.
- Returns the private new history cursor and changed metadata for **atomic**
  persistence, alongside an opaque digest; not a live verified checkpoint.

### Microsoft Graph

- Reads `mailFolders/{verified_folder_id}/messages/delta` PER FOLDER.
- Follows exact `@odata.nextLink` URLs only for the verified folder and
  `https://graph.microsoft.com` origin; rejects cross-folder, cross-origin
  and incomplete reads.
- Requires a terminal `@odata.deltaLink` before returning an exhausted round.
- Treats the deltaLink as a secret-bearing opaque cursor for a future
  encrypted backend store. Never logs or exposes it in a client response.
- Tracks deletion markers without treating them as customer replies.
- Without a previously persisted deltaLink, the round is marked
  `initial_baseline=True`; an initial baseline is not complete historic
  correspondence and cannot prove no reply to a pre-baseline message.

Both result types explicitly report `checkpoint_persisted=False`,
`cross_channel_mapping_verified=False`, `reply_absence_proven=False`.
Staging cannot promote either to Lola's follow-up policy without additional
verified, backend-owned persisted evidence.

## Additive draft database migration (not applied)

File: `supabase/migrations/20261115181500_lola_provider_replay_checkpoints_staging.sql`.

- Introduces `lola_provider_replay_checkpoints` keyed by tenant, employee,
  provider and SHA-256-hashed provider connection/resource identities.
- Requires backend-side encryption for Gmail history cursors and Microsoft
  Graph deltaLink URLs; database contains ciphertext, a cursor digest,
  mapping revision, generation, verified replay timestamp and source digest.
- Introduces `lola_provider_replay_events` for SHA-256-keyed event metadata
  with no message body, contact name, phone or raw provider message ID.
- Both tables enable RLS and have no end-user policies; privileges are
  restricted to backend service role.
- Defines SECURITY INVOKER, service-role-only
  `lola_commit_provider_replay` that locks checkpoint rows, rejects stale
  revision writes, and commits event metadata and an encrypted cursor within
  one transaction. Failed event persistence rolls back cursor advancement.
- No DELETE grant is issued. No migration has been applied or tested
  against hosted staging/production databases.

## Mandatory integration and acceptance work (not implemented)

1. A server-only store must encrypt/decrypt opaque provider cursors with
   properly managed keys and read the persisted generation before CAS.
2. A backend adapter must prove employee-owned OAuth authorization and active
   consent, and must map provider 404/410/401/403/429/5xx precisely, with
   bounded retries and safe invalidation/recovery; no token or response
   body logs.
3. Full Gmail bootstrap must use stable snapshot rules and reconcile
   concurrent changes with history replay before moving to incremental.
4. Graph needs initial baseline and separate delta cursor PER folder, plus
   verified folder enumeration/coverage and failure propagation on missing
   folders or revoked authorization.
5. Backend must hash provider message IDs, construct bounded event batches,
   and run the single service-role CAS function. This integration does not
   exist in this branch; SQL is an unapplied schema contract.
6. Independently verified Stage 5 employee identity, phone ownership,
   work-item/contact mapping, tenant isolation and provider completeness
   remain mandatory before Lola may produce even a human-review suggestion.
7. WhatsApp official connection, app-echo handling, signed webhook delivery,
   persisted receipt/dedup checkpoints and verified message history are not
   solved by Gmail/Graph replay.
8. Perform authenticated provider-sandbox replay and cursor-expiry tests,
   new-schema RLS and concurrency tests, end-to-end staging QA and CodeQL
   before proposing any deployment.

## Test coverage

`backend/tests/test_lola_incremental_replay.py` uses synthetic async
transports and checks paging, noncontiguous Gmail history IDs, terminal
Graph delta tokens, expired cursors, cross-folder/foreign-origin URLs,
deduplication, malformed responses and failures that cannot imply replies.

**No production changes, database mutations, OAuth connector calls,
automated notifications or outbound WhatsApp/email sends.**

## Official protocol references

- Gmail history.list: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Gmail synchronization: https://developers.google.com/workspace/gmail/api/guides/sync
- Graph folder message delta: https://learn.microsoft.com/en-us/graph/delta-query-messages