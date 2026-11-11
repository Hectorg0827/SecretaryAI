# Lion BI / SecretaryAI: Pass 4 selective staging integration

**Target:** `integrate/pass4-staging-20261111`, an independent review branch. It
is **not** the production branch, the Railway staging auto-deploy branch, or a
substitute for a reviewed merge. Do not merge Claude PR #108 wholesale: its
older tree conflicts extensively with current Gate 6C controls.

## Forward-ported batches

1. Dedicated `ENCRYPTION_KEY`, optional previous-key rollover, and legacy
   `SECRET_KEY` decrypt fallback with regression tests. Not activated until
   the operator configures keys; no existing encrypted data was rewritten.
2. Staging-only owner staff management: change an active staff member's role
   (within existing allowlisted roles) and soft-disable access, using current
   company-scoped owner authorization. No owner reassignment, no cross-company
   changes. Privileged writes fail closed if the pre-action audit fails, and
   use conditional updates to reject concurrent changes. The web Settings
   team section shows Save role and a two-step Disable confirmation.
3. Staging-only password recovery and session security: 30-minute one-time
   opaque reset links in Redis, a generic non-enumerating request response,
   staging mailbox allowlist, current-password changes, conditional account
   writes, and durable account-security audit gates. Every new access/refresh
   JWT carries a keyed fingerprint of the current `users.hashed_password`;
   Gate 6C rejects stale fingerprints on every API request. Refresh rotation
   now atomically claims the presented JTI (`SET NX EX`) to reject concurrent
   replay. Frontend adds public recovery pages and an authenticated account
   password page for all staff roles.

## Local verification (2026-11-11)

- Backend: **2,981 passed / 1 skipped** with in-memory simulated Redis;
  not a live Redis/Supabase/Railway test.
- Frontend: **272 passed** in 48 test files; TypeScript, build, and lint
  succeed (13 pre-existing lint warnings, no errors).
- Role and removal tests include: current owner status, tenant boundaries,
  protected owners, audit outage, concurrent update, and rejection of stale
  role/deactivated sessions through the Gate 6C session verifier.
- Password recovery tests include single-use tokens, stale-session rejection,
  simultaneous refresh replay, audit outage, and mounted FastAPI route behavior.

## Deployment gates still open

- No real QuickBooks/Microsoft/Stripe/Resend account tests and no real DB
  migration or staging deployment in this integration batch.
- Check whether the active Railway staging branch has advanced before
  cherry-picking or opening a PR. Do not blindly overwrite newer Gate 6C.
- The existing Gate 6C boundary rejects old-role tokens from authenticated
  APIs. A demoted user may obtain a new **lower-privilege** token through
  `/auth/refresh`; deactivated users cannot. True all-device forced logout
  on role change is **not** implemented in this batch.
- **Rollout effect:** staging's old access tokens without the new password
  fingerprint are rejected with 401; testers must sign in again after the
  eventual reviewed deployment. There is no silent backward-compatible
  bypass for a pre-reset JWT. No production token behavior has been changed.
- Recovery emails are limited to the configured staging invitation mailbox;
  ordinary customers cannot request reset links until a separately reviewed
  production identity delivery policy is implemented and validated.
- Continue selective review of Claude Pass 4 invitation
  acceptance, Outlook connect, and webhook changes against
  the current staging equivalents; do not duplicate or regress newer logic.
- Use synthetic identities and controlled sandbox providers for staging
  acceptance. Production stays out of scope until separately approved.
