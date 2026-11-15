# Lola Stage 12 — audited Gmail history-404 checkpoint recovery

**GitHub staging-only development. No production or hosted Supabase / Railway
deployment, no live OAuth/WhatsApp access, no sent messages.**

## Why this is needed

After Stage 11, a Gmail history cursor can expire and return HTTP 404. Google
requires a full sync. Simply overwriting the checkpoint would misrepresent
the gap as continuously observed history, discard provenance, and allow
later incremental events to be assigned to the wrong synchronization era.

## Additive, UNAPPLIED migration

`supabase/migrations/20261115233000_lola_gmail_recovery_epochs_staging.sql`:

- Adds `recovery_epoch` to existing replay checkpoints and events, default 0.
- Creates a backend-only RLS-protected `lola_provider_replay_recoveries`
  audit table. Audit records retain prior and replacement cursor **digests**
  (not plaintext), previous/new replay digests, generation, epoch, reason,
  initial message count, event count and server timestamp.
- Defines service-role-only `lola_recover_expired_gmail_replay` RPC with
  `SECURITY INVOKER`. It locks the existing Gmail checkpoint, enforces CAS
  generation/old cursor digest, rejects stale or malformed input, increments
  epoch and generation, sets `baseline_only=TRUE`, inserts new epoch event
  metadata, and appends the recovery audit in one transaction.
- Stage 10's BEFORE UPDATE trigger still checks the *current active employee*,
  BOTH independently verified channel grants, unrevoked consent, exact
  provider identity, authorized mailbox and verified case mapping under locks.
- Original replay events are NEVER deleted or rewritten by a recovery.
- The new event-epoch trigger maps subsequent Stage 8 incremental events
  onto the current checkpoint epoch. This prevents new data from being
  accidentally recorded under epoch zero after a successful recovery.
- Service role has SELECT and INSERT to the audit ledger, but no UPDATE or
  DELETE; anon/authenticated have no access. No browser RPC grant.

## Backend-only recovery pathway

- `replay_recovery.py`: validates a typed provider-history 404 and exhausted
  `GmailBootstrapEvidence`; AES-GCM encrypts the new Gmail cursor, hashes
  employee mailbox and event identities, calls the atomic recovery RPC and
  re-reads the checkpoint, recovery audit and new-epoch events. Fail closed
  if any committed row or digest does not match.
- `gmail_recovery_service.py`: separate, opt-in recovery method. Re-resolves
  employee consent and independently verified case mapping, requires a
  current scoped **backend employee recovery approval** (max 30 min validity),
  asks Gmail to replay the OLD cursor and requires actual GmailHistoryExpired,
  then performs a fresh full metadata/history bootstrap and reauthorizes
  the employee AND the recovery approval before the atomic reset.
- The approval loader is deliberately only a trusted backend interface;
  no route/scheduler or durable approval-enrollment UI is installed, so
  the method is dormant in a live application until that source is built.
- If the old cursor is still valid, no reset occurs. If the new bootstrap
  expires or exceeds Stage 11's 400-message bounded cap, no reset occurs.

## Tests

- `backend/tests/test_lola_replay_recovery.py`: encryption, CAS old generation
  and cursor digest, event retention/epoch tagging, required verified 404,
  tampering, absent audit, failed RPC, no invented response absence.
- `backend/tests/test_lola_gmail_recovery_service.py`: exact order of
  employee approval + confirmed 404 + full sync + reauthorization + reset;
  missing/stale approvals, silent-reset rejection, revoked identity, fresh
  history expiry and failed checkpoint commit.
- `scripts/test_lola_stage12_recovery_security.sql`: actual disposable
  PostgreSQL 17 transaction verifies role restrictions, original CAS
  checkpoint/event creation, reset audit, epoch advancement, preservation
  of old events, post-reset incremental epoch tagging, stale replay refusal
  and rollback on employee consent revocation.
- `.github/workflows/ci.yml` applies Stage 8, 10 and 12 additive migrations
  ONLY to GitHub Actions' disposable PostgreSQL service and runs tests.

## Remaining blockers and limitations

1. Existing Stage 8/10/12 migrations remain unapplied to hosted staging.
2. The backend recovery approval registry/provider credential loader is
   not yet wired to any real employee connection or approved action.
3. Full Google history before the original snapshot is inherently unknown;
   a reset creates a new observation epoch, not proof of historical silence.
4. Gmail full-sync remains bounded to 400 messages. Durable paged sync
   for larger mailboxes requires its own design and authorization tests.
5. Graph folder token recovery still returns a request for resync; no
   audited Graph recovery RPC is implemented here.
6. Official Meta WhatsApp webhook and app-echo ingest, message correlation,
   per-employee privacy controls and no-reply proof are not accepted end-to-end.
7. Real provider sandbox tests, hosted staging permission review,
   concurrent recovery-vs-revocation races, approval revocation and
   ciphertext key rotation still require acceptance before production.

**Every completion response remains `follow_up_eligible=False` and
`reply_absence_proven=False`. No automated sends are possible in this stage.**

Protocol: https://developers.google.com/workspace/gmail/api/guides/sync