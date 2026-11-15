# Lola Stage 9 — Supabase atomic provider checkpoint store (source-only)

**Status: staging draft; no hosted database migration or deployed RPC.**
Stage 9 adds a REAL Python adapter for future Postgres transaction RPCs and
synthetic integration tests. It must fail closed on uninstalled RPCs.

## Working code

- `backend/app/intelligence/lola/supabase_delta_store.py`: internal
  `SupabaseAtomicRoundStore`, implementing Stage 8 `AtomicRoundStore`.
- Read/commit are restricted to the authenticated current user and an exact
  tenant, employee, provider, connection, mailbox, folder and mapping revision.
- Rechecks the current active `users` membership through `_actor` before
  EVERY RPC, and calls an externally supplied **fresh server-derived**
  `current_authorization(scope)` callback for current provider consent,
  account identity/ownership and mapping. The callback must not be a
  hardcoded true or sourced from browser/LLM requests.
- Uses AES-GCM (existing `app.utils.encryption`) for provider cursors at
  rest. Uses a separate secret HMAC-SHA256 key for cursor equality and
  replay-stable event/thread references. Never sends raw provider message
  IDs, thread IDs or message bodies to the checkpoint RPC.
- Calls ONLY `lola_delta_checkpoint_read` (primary read) and
  `lola_delta_checkpoint_commit` (one atomic transaction). No direct
  multi-step `table.upsert` or update fallback is allowed.
- Requires exact scope columns, single primary result, decryptable cursor,
  HMAC digest agreement, monotonically increasing version, valid source
  digest, and independently asserted DB transaction/authorization proof.
- Commit RPC must respond with exactly JSON `{ "committed": true|false }`.
  Anything else is an ambiguous failure, NOT a successful write.
- Stage 8's readback now allows a database transaction commit timestamp
  up to 30 seconds after the caller's pre-write clock, but rejects larger
  future timestamps and mismatched content.
- In all cases `CommitOutcome.no_reply_verified=false`,
  `follow_up_allowed=false` and `external_messages_sent=false`.

## Required database migration — NOT generated or applied

The installed Supabase CLI is absent from the current container. Per CLI
workflow, an engineer must FIRST install/pin a supported Supabase CLI,
review `supabase --help`, and run
`supabase migration new lola_delta_checkpoint_atomic_store` from the repo,
then add exactly ONE new migration in the appropriate staging migration
track. Never modify an existing migration or apply to production.

Design that migration for these entities:

1. `public.lola_delta_checkpoints`: composite unique identity
   `(company_id,employee_id,provider,provider_connection_ref,
   provider_identity_ref,mailbox_ref,folder_id,mapping_revision)`.
   Since PostgreSQL treats NULL values as distinct, use a nonnullable
   normalized folder key or NULLS NOT DISTINCT uniqueness to avoid
   duplicate Gmail checkpoints.
   Store `cursor_ciphertext`, `cursor_digest`, integer `version`,
   `last_round_digest`, `changes_applied`, `source_coverage_start`,
   `committed_at`, source status and immutable provenance.
2. `public.lola_delta_event_refs`: composite FK to the exact checkpoint
   identity, unique per-source `event_ref` SHA256 HMAC, type, HMAC
   `thread_ref`, source checkpoint version, insertion timestamp.
   Never store provider message bodies, addresses, phones or raw IDs.
3. Enable RLS on BOTH tables, REVOKE `anon`, `authenticated`,
   `PUBLIC` and broad default grants, grant only minimal `service_role`
   rights required by the audited SQL RPCs. No end-user RLS policies
   should expose this employee-private checkpoint state.
4. `lola_delta_checkpoint_read` must be `SECURITY INVOKER`,
   explicitly `REVOKE EXECUTE FROM PUBLIC, anon, authenticated`,
   `GRANT EXECUTE TO service_role` only, set a fixed `search_path`,
   and verify exact scope and ACTIVE employee ownership/authorization
   from the database. Return JSON NULL or exactly one scoped result.
5. `lola_delta_checkpoint_commit` must similarly be backend-only
   and run all validations, all `event_ref` upserts, and cursor/version
   update inside ONE Postgres transaction. Lock the exact checkpoint
   (`FOR UPDATE`) and compare expected version and HMAC cursor digest.
   `INSERT ... ON CONFLICT` must dedupe by event_ref, with no partial
   event inserts surviving a failed cursor transition.
6. Recheck current consent, revoked status, provider ownership and mapping
   revision **inside the transaction**, not by trusting an `authorized`
   parameter supplied by the RPC client. Until the Stage 5 authorization
   registry has an independently verified schema and population path,
   the RPC must reject every write.
7. The RPC's success response is ONLY `{ "committed": true }` after
   the transaction has atomically persisted the entire event batch
   and terminal cursor. On stale version, revoked consent, incomplete
   source or invalid scope, return false or an error without partial
   persistence. The caller independently performs a primary readback.
8. Treat the cursor ciphertext and keyed digest as secret values.
   Use distinct, rotated encryption and HMAC keys provided only to the
   trusted server; never include values in logs, errors, frontend props,
   link query strings or analytics.

## Existing staging schema verified (read-only)

- `public.users`: UUID `id` and `company_id`, role, `is_active`.
- `public.employee_integrations`: UUID company/user, `provider`,
  credential fields and timestamps. A connected row alone does NOT
  demonstrate independent employee consent or provider-owned mapping.
- `public.lola_employee_whatsapp_connections`: existing Meta verification
  and employee consent fields, but NOT a complete historical webhook ledger.
- Stage 5's `lola_channel_authorizations` and verified case-link tables
  are not yet installed in staging. Stage 9 cannot safely bypass them.

## Synthetic acceptance

`backend/tests/test_lola_supabase_delta_store.py` covers: correct primary
read and round commit, missing RPC, wrong tenant/employee, disabled user,
consent revoke before read/commit/readback, encrypted cursor integrity,
source ID redaction, HMAC refs, partial or tampered results, CAS conflict,
ambiguous success, valid bounded database latency and far-future timestamps.

**Tests do not verify actual hosted SQL atomicity.** For real acceptance:

- Generate the migration via local CLI, review SQL, apply in an isolated
  local Postgres/Supabase container and test rollback on failed insert.
- Prove two workers racing on same cursor yield exactly one commit.
- Prove wrong company/user and missing/revoked consent yield zero rows.
- Prove event identities are deduplicated and cursor+events stay atomic.
- Prove no ordinary JWT/anon role can invoke either RPC.
- Prove stale/wrong encryption and HMAC keys fail closed.
- Only after local verification request separate approval to apply on
  **SecretaryAI-Staging**, and NEVER apply to the live SecretaryAI project
  from this branch.
- Run authenticated provider sandbox tests and independent reconciliation
  before claiming Lola can identify missing cross-channel replies.

## Not enabled

No scheduler, public endpoint, WhatsApp app-echo subscription, real provider
history ingestion, database migration, deployed SQL RPC, message send,
notification, acceptance signoff or production change. This branch is not
a fully operational personal business agent.

## References

- Supabase Data API security: https://supabase.com/docs/guides/api/securing-your-api
- Supabase CLI: https://supabase.com/docs/reference/cli/introduction
- Gmail change history: https://developers.google.com/workspace/gmail/api/guides/sync
- Microsoft Graph delta: https://learn.microsoft.com/en-us/graph/delta-query-messages