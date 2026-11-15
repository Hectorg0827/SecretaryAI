# Lola Stage 10 — atomic SQL RPC contract and ephemeral Postgres acceptance

**Staging development only.** This branch stacks on Stage 9 and does not
apply migrations, deploy Railway, contact a provider or send any message.

## Implemented

- Supabase CLI v2.122.0 generated the migration filename in isolated CI.
  The unapplied file is `supabase/migrations/20261115050831_lola_delta_atomic_checkpoint_stage10.sql`.
  Its review source remains at
  `docs/lola/sql/STAGE10_ATOMIC_CHECKPOINT_REVIEW_ONLY.sql`.
- It defines the missing Stage 5 employee-consent/channel identity and
  verified-case link tables, an employee-scoped provider checkpoint
  table, and immutable opaque event-reference table.
- An SQL helper independently checks active employee membership, actual
  employee mail integration ownership, current email and WhatsApp consent,
  verified Meta phone ownership, and independently verified contact/work
  mapping. The computed mapping fingerprint matches the Stage 5 resolver.
- The helper obtains row-level SHARE locks during evaluation so concurrent
  consent changes serialize with an in-flight checkpoint transaction.
- `lola_delta_checkpoint_read` and `lola_delta_checkpoint_commit` are
  `SECURITY INVOKER` functions with explicit service_role-only EXECUTE;
  no anon/authenticated/public policies or function access are granted.
- `commit` locks the exact existing checkpoint row, verifies prior
  version+cursor digest, inserts deduplicated opaque event references and
  advances the encrypted terminal cursor inside a single PostgreSQL
  statement transaction. A duplicate insert aborts the full transaction.
- The checkpoint identity includes tenant, employee, provider, connection,
  mailbox, normalized folder key and verified mapping revision.
- Existing Stage 9 Python store now derives stable dedupe keys from Gmail
  history ID or Graph's preceding delta cursor, independent of page order
  and final round fingerprint. It rejects duplicate event references
  **before** the database RPC.
- New Stage 10 unit tests check duplicate event refusal and deterministic
  retry identity even when the source page-walk fingerprint changes.

## Real SQL validation (ephemeral, not hosted)

- `.github/workflows/lola-stage10-atomic-rpc.yml` starts disposable
  PostgreSQL 16 in a GitHub Actions service container.
- `backend/tests/sql/lola_stage10_ephemeral_fixture.sql` supplies only
  synthetic private tables, roles and pgcrypto; no business records.
- It compares the reviewed DDL against the committed CLI-generated migration,
  executes the **committed migration file** on disposable PostgreSQL, then
  runs `backend/tests/sql/lola_stage10_ephemeral_acceptance.sql`.
- Acceptance asserts RLS, browser RPC denial, atomically stored cursor
  and events, stale writer CAS rejection, unique-event rollback,
  tenant isolation, and revocation of employee consent.
- These tests validate SQL syntax and basic transactional behavior in
  disposable Postgres. They do not prove a hosted Supabase deployment
  or a working Gmail/WhatsApp provider synchronization.

## Explicit deployment blockers

1. The local container could not install the Supabase CLI. An isolated
   GitHub Actions runner DID run `supabase migration new` with pinned CLI
   v2.122.0 and produced exactly the committed timestamped migration path.
   No hosted database was connected or changed.
2. The new migration must pass final ephemeral PostgreSQL validation,
   full CI, CodeQL, staging DB/security review, and migration-ordering
   checks against all other pending staging migrations.
3. Validate the actual target Supabase role privileges, policies and
   encryption-key settings. The SQL authorizations depend on verified
   registry population, which is not implemented by this branch.
4. A provider-ingestion integration must create only separately consented
   and independently verified authorization rows. This branch inserts
   no real grants, identities, or verified case links.
5. Full authenticated staging acceptance is still needed for provider
   history recovery, Meta app echoes and cross-channel response matching.
6. A separate explicit approval is required before applying any migration
   on SecretaryAI-Staging. **Never** apply it to the production project.

## Security boundaries

- No raw provider message IDs, address/phone, body, access tokens or
  OAuth secrets are stored in the delta checkpoint/event tables.
- SQL rejects incorrect company/employee scope, missing verified consent,
  stale optimistic versions, unsupported provider/folder and bad metadata.
- A persisted provider round is **not** an independently verified
  no-reply signal. Lola must not schedule or send follow-up messages yet.

See the SQL and tests for the exact contract and failure cases.