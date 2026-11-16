# Lola Stage 14A — Gmail snapshot integrity and anchored replay (read-only)

Scope: **draft source code only**. This change is an isolated proof kernel above
the existing Stage 13 snapshot storage. It creates no SQL migration, route,
scheduler, worker, Gmail OAuth connection, durable replay checkpoint, WhatsApp
operation, or reminder.

## What is now inspectable

- The complete, employee-scoped Stage 13 page/receipt/message-reference ledger
  must be supplied by a privileged reader as one consistent collection.
- Exact page count, record count, HMAC message uniqueness, employee/tenant/
  provider scope and the Postgres SHA-256 page-chain algorithm are checked.
- The anchor from the original Gmail profile is the *only* permitted starting
  history cursor. Existing bounded history replay is reused, so malformed,
  expired, repeated or truncated provider pages fail closed.
- Additions and deletions are reconciled against the HMAC message set without
  copying raw Gmail IDs into the returned preview.
- Baseline proofs **never** assert `no_reply`, full historical mailbox
  coverage, verified cross-channel linkage, or approval for a send.

## Important security boundaries

The proof accepts only verified database receipts and references. It **does not
fetch the ledger from Supabase**. The future trusted reader must provide all
records from a stable transaction, with scoping, completeness and current
employee consent proven. A list assembled by a browser or an LLM is not trusted.

The Gmail fetcher must be a real, authorized employee-owned provider transport,
with current OAuth principal verification and reauthorization before and after
each page. Metadata-only history reads still have provider privacy implications.

The proof's record-digest result is *not* a durable checkpoint; HMAC membership
does not by itself establish whether a human replied to a work item, especially
when the history cursor was reset or a thread could not be mapped conclusively.

## Next stage 14B (not implemented)

1. Trusted consistent ledger reader/transaction, privilege checks and
   independent consent revalidation.
2. A new **additive** Stage 14 finalization migration, generated through the
   approved Supabase migration workflow, combining the snapshot receipt,
   reconciled history evidence and encrypted checkpoint in one CAS transaction.
3. Idempotent job consumption, snapshot epoch/generation pinning, retry safety,
   audit-only output and duplicate suppression.
4. Disposable PostgreSQL integration, cross-user/tenant negative tests,
   authenticated test mailbox interrupted-sync acceptance, exact-head CI,
   CodeQL and independent QA signoff.

**Never** apply Stage 8/10/12/13 migrations to hosted staging or production as
part of this proof-only change. No merge or deployment is authorized by it.

Tracking: GitHub #190, based on Stage 13 draft PR #172.
