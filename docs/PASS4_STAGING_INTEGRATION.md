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

## Local verification (2026-11-11)

- Backend: **2,970 passed / 1 skipped** with in-memory simulated Redis;
  not a live Redis/Supabase/Railway test.
- Frontend: **269 passed** in 47 test files; TypeScript, build, and lint
  succeed (13 pre-existing lint warnings, no errors).
- Role and removal tests include: current owner status, tenant boundaries,
  protected owners, audit outage, concurrent update, and rejection of stale
  role/deactivated sessions through the Gate 6C session verifier.

## Deployment gates still open

- No real QuickBooks/Microsoft/Stripe/Resend account tests and no real DB
  migration or staging deployment in this integration batch.
- Check whether the active Railway staging branch has advanced before
  cherry-picking or opening a PR. Do not blindly overwrite newer Gate 6C.
- The existing Gate 6C boundary rejects old-role tokens from authenticated
  APIs. A demoted user may obtain a new **lower-privilege** token through
  `/auth/refresh`; deactivated users cannot. True all-device forced logout
  on role change is **not** implemented in this batch.
- Continue selective review of Claude Pass 4 password recovery, invitation
  acceptance, one-time refresh, Outlook connect, and webhook changes against
  the current staging equivalents; do not duplicate or regress newer logic.
- Use synthetic identities and controlled sandbox providers for staging
  acceptance. Production stays out of scope until separately approved.
