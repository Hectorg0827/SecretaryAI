# Lola Stage 14B — Consistent private Gmail baseline finalization

**State:** dormant source-only GitHub staging draft, dependent on stacked Stage 8/10/12/13/14A code. No hosted Supabase modification, Railway deployment, LYO change, OAuth bootstrap worker, live provider access, external messages, or production approval.

## New components
- `supabase/migrations/20261116162500_lola_gmail_checkpoint_finalization_staging.sql`: single **new additive** staging migration. Creates a service-role-only, no-browser-RLS finalization receipt, employee/consent/resource/work-item access gate, consistent snapshot bundle reader RPC, and atomic initial Gmail checkpoint finalizer.
- `backend/app/intelligence/lola/gmail_snapshot_finalization.py`: backend-only coordinator. Loads a scoped snapshot, validates the Stage13 chain and unique HMAC references, replays Gmail from the **original** Stage13 history anchor with employee reauthorization on each provider page, reloads the source and refuses source changes, then submits one encrypted/CAS-protected baseline transaction.
- `backend/app/intelligence/lola/gmail_snapshot_reconciliation.py`: adds HMAC-only event rows to Stage 14A private preview; never exposes raw Gmail message identifiers.
- Synthetic Python tests and SQL tests in disposable Postgres; CI schema job applies migration after 8/10/12/13 and runs the stage14B negative cases.

## Privacy and write semantics
- SQL reader returns the scoped job + pages + message HMAC refs via **one single PostgreSQL RPC**, with both collection arrays obtained from one statement-level MVCC snapshot. A job row lock prevents legitimate Stage13 page updates during the reader transaction. No direct client-facing endpoint is added.
- A separate verified backend caller must bind the actor to the exact employee-owned case; the server service_role is privileged and **cannot by itself distinguish a user request from a forged employee_id**. Do not expose these RPCs as public APIs or LLM tools. The caller must resolve an authenticated user from independently verified backend session state.
- The access gate checks active employee membership, BOTH separately consented channels, verified Gmail principal, approved resource, and verified project/contact mapping. Commit also passes Stage10's write-time authorization trigger, under transaction locks.
- Finalizer refuses partial/mismatched page chains, HMAC ref membership changes, differing mapping revisions, reused checkpoints and invalid/duplicate events.
- The initial encrypted checkpoint is **baseline_only=TRUE**, generation=1, recovery_epoch=0. The immutable finalization receipt records source and event batch digests. Exact retries are no-ops and return original generation; changed evidence fails closed.
- On an RPC timeout or contradictory verification, the backend does not claim success; a duplicate-safe retry can be attempted against current authorized source evidence.
- **No customer no-reply claim**. These records do not prove complete historical mailbox coverage, identity correlation across channels, reply absence or outbound authorization. No reminders or sends are enabled.

## Remaining release blockers and review requirements
1. **Provider-level source provenance:** Stage13's persisted original per-page digest cannot be recomputed exclusively from the stored HMAC message references because its original serialized page cursor envelope is not persisted. The Stage14B source-chain and membership checks detect tampering/incomplete storage but do not independently certify that the original page was fetched or every provider event was preserved. Require fresh authorized provider sandbox acceptance and a separately reviewed canonical evidence binding approach if needed before final launch.
2. **Authenticated end-user binding:** the role-only SQL gate is deliberately backend service-role-only; a real server-authenticated tenant/employee case binding must be proven in E2E negative tests before connecting it to routes or tasks.
3. **Concurrency/consent:** run real disposable Postgres checks for revocation races, simultaneous first finalizers, interrupted provider pagination and post-commit retries.
4. **Independent QA:** review current exact PR head in QA issue #114; no author self-signoff. Full CI (Python, frontend, SQL, secret scan) and both CodeQL jobs must be green.
5. **Deployment** requires a separate explicit approval for the named staging project, including the additive migration and rollback strategy; no production changes.

Supabase CLI was unavailable in the editing environment, so the new migration was authored as a new additive file following this repository's migration naming pattern. Existing migrations are untouched.
