# Lola Stage 13 — Resumable Gmail Snapshot Staging

**Scope:** Source-only, UNAPPLIED additive migration and dormant backend module.
No hosted Supabase modification, no OAuth account load, no Railway deployment,
no worker/scheduler, no outbound messages, no production changes, no merge.

## Why

Stage 11 safely rejects Gmail mailboxes exceeding 400 messages. Stage 13
introduces **durable page-level snapshot staging**, preserving the initial
history anchor and encrypted page continuation across independent authorized
backend calls. It handles snapshots larger than the single-call cap without
ever marking a partial page walk as full source coverage.

## New storage

`supabase/migrations/20261116021500_lola_gmail_paged_snapshot_staging.sql`
creates three empty, service-role-only RLS tables:

- `lola_gmail_snapshot_jobs`: encrypted starting Gmail history ID,
  encrypted nextPageToken, keyed scope/anchor hashes, chain digest, counters
  and `collecting` / `snapshot_exhausted` / `aborted` state.
- `lola_gmail_snapshot_pages`: one immutable page digest, chain digest and
  message count per (employee, Gmail mailbox, page index).
- `lola_gmail_snapshot_refs`: per-message keyed HMAC hashes, keyed thread
  and metadata-history hashes, unique across every staged page. No message
  ID, subject, sender, body, OAuth secret, email address or page token plaintext.

The `lola_stage_gmail_snapshot_page` RPC compares current page generation
and keyed next-page cursor, updates the job, receipt and message refs in one
transaction, and fails on duplicate keys or truncated/malformed pages.
Every job INSERT/UPDATE invokes Stage 10's independent employee membership,
both channel consent and verified identity/resource/work-item trigger.

Bounded safeguards: 100 message refs per call, 500 pages and 20,000 total
message refs per snapshot job. Beyond that requires another design review;
there is no automatic partial acceptance.

## Backend

`backend/app/intelligence/lola/gmail_snapshot_staging.py`: `read_snapshot_progress`
decrypts backend-only scoped state; `advance_gmail_snapshot_page` reads one
authorized Gmail page and metadata, checks message/thread identity, persists
only keyed digests and encrypted continuation, then confirms the stored
result and page receipt. It does not call the Gmail history replay writer
or install a router/worker.

## Tests

- `backend/tests/test_lola_gmail_snapshot_staging.py`: 500-message 5-page
  continuation, empty mailbox, inconsistent metadata, duplicates, CAS
  conflict, revoked grant, cross-tenant reads, missing receipts and privacy.
- `scripts/test_lola_stage13_snapshot_security.sql`: actual disposable
  PostgreSQL stage initialization, page CAS, cross-page duplicate rollback,
  final snapshot exhaustion without a provider checkpoint, private grants
  and employee consent revocation checks.
- CI migration job now applies Stage 13 and executes its SQL tests on the
  disposable PostgreSQL 17 service, after Stages 8/10/12.

## NOT complete

**Staging a finished message listing is NOT a Gmail cursor checkpoint.**
The initial history anchor must still be reconciled through an exhausted
Gmail history replay, with durable staging-to-checkpoint finalization.
Provider OAuth, employee enrollment, retries, recovery/rotation and actual
connection read tests remain pending. A staged snapshot never proves
`no_reply`, complete lifetime mailbox coverage, or cross-channel mapping.

**No automatic Lola reminder, message or outbound provider write is enabled.**