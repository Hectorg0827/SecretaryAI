# Gate 6C — controlled live staging authorization acceptance

**Status: NOT YET SIGNED OFF.** CI, CodeQL and the mounted-FastAPI regression
suite do **not** substitute for authorized live staging testing. The previous
temporary test runner was blocked and was not deployed. Do not recreate it or
invoke this procedure using an unapproved service. All checks below require
an authorized operator working from the existing approved staging test setup.

## Scope and immutable guardrails

- GitHub repository: `Hectorg0827/SecretaryAI`. Staging-only branch:
  `feat/staging-secure-manager-invites-20261108`. Never modify the default
  branch, production services, LYO, or any other Supabase project.
- Railway project `77513c77-44df-485e-a927-d57d6a80b2c1`, environment
  `f9dc2025-cd5b-4df2-95bc-c3c54a3c3e36` (`staging`).
- Staging Supabase project `xerktvdgjvjozvckjyfp` only.
- Backend URL: `https://secretaryai-backend-staging.up.railway.app`.
- Test **only** disposable, clearly marked synthetic companies and staff.
  No real employee invitations, production credentials, vendor writes,
  bulk email, billing checkout, external notifications or customer data.
- Do not turn off security guards to make a test pass. Do not interrupt the
  shared staging database or Redis to simulate outages; their fail-closed
  behavior is covered by isolated CI tests. A destructive failure-injection
  test needs its own approved disposable environment and explicit authority.
- Keep API tokens and passwords in the operator's approved secret manager.
  Never paste them into GitHub Actions variables, PR comments, issue text,
  terminal history, screenshots, chat messages or evidence files. In evidence,
  record only a SHA-256 fingerprint of each token (no token body), a timestamp,
  status code and request ID, if supplied.

## Preconditions

1. Independently verify the Railway deployment commit, `SUCCESS` status,
   health 200, and `SECRETARYAI_DEPLOYMENT_ENV=staging` using authorized
   staging configuration access. A variable's *presence* alone is insufficient
   to establish its value.
2. Confirm that the Supabase project reference matches the staging ref above
   and that email/automation/accounting side effects are disabled.
3. Using the **existing approved staging identity/account setup workflow**,
   provision two isolated synthetic companies A and B; capture internal
   synthetic identifiers in private operator notes. Include Manager, AR, AP,
   Finance Controller and Pricing/Billbacks identities in A. B is a negative
   tenant-isolation control. Do not run ad hoc migrations or history repairs.
4. Ensure the synthetic company has a current, qualified
   `dashboard_summary` snapshot if asserting workspace HTTP 200; otherwise
   a 503 from missing/stale snapshot means the business-data prerequisite is
   unmet, not that the role check passed.
5. Sign in a Manager from A and retain **exactly the same JWT** for the
   pre-demotion and post-demotion negative tests. Store its one-way
   fingerprint for correlating the requests. Do not refresh it between them.

## Mandatory live API assertions

Every request targets the staging backend URL above. The status is the actual
HTTP status, not a frontend notification or locally mocked response.
Capture the UTC timestamp and the response's `X-Request-ID` when present.
All access-control failures must show an **absent sensitive response body**.

| ID | Action / token | Method and path | Required HTTP status / content |
| --- | --- | --- | --- |
| M01 | Manager A JWT before change | `GET /api/settings/integrations` | **200**, response limited to A |
| M02 | Operator changes that **same** employee in staging DB: `manager` → `ar_specialist`; reuse original M01 token | `GET /api/settings/integrations` | **403**, no integration metadata |
| M03 | Reuse original M01 token | `GET /auth/me` | **403**, no profile |
| M04 | Reuse original M01 token | `GET /api/team/members` | **403**, no staff list |
| M05 | Reuse original M01 token | `GET /api/finance/workspace` | **403**, no finance summary |
| A01 | Freshly log in as the demoted AR employee; use **new** JWT | `GET /auth/me` | **200**, role `ar_specialist` |
| A02 | Fresh AR JWT | `GET /api/finance/workspace` | **200**, `authorized_scopes=["ar"]`, sections contain **only** `ar` |
| A03 | Fresh AR JWT | `GET /api/settings/integrations` | **403**, no connection metadata |
| F01 | Active Finance Controller A JWT | `GET /api/finance/workspace` | **200**, scopes exactly `["ap","ar","ledger"]` |
| F02 | Active AP specialist A JWT | `GET /api/finance/workspace` | **200**, scopes exactly `["ap"]` |
| F03 | Active Pricing/Billbacks A JWT | `GET /api/finance/workspace` | **200**, scopes exactly `["pricing"]` |
| F04 | Each finance-role JWT, including AR | `GET /api/settings/integrations` | **403** for each |
| D01 | Sign in another active synthetic employee, then **disable** that employee; reuse the unchanged original JWT | `GET /auth/me` | **403** |
| T01 | Sign in another active synthetic employee in A, then **transfer** its company ID to B in the DB; reuse its original A JWT | `GET /auth/me` | **403** |
| R01 | Log out a current synthetic employee session via `POST /auth/logout` (200), then reuse that JWT | `GET /auth/me` | **401** |
| I01 | Malformed token, no authenticating secret | `GET /auth/me` | **401** |
| X01 | AR JWT with `?company_id=<synthetic B ID>&scope=ap` query | `GET /api/finance/workspace` | **200**, still AR-only for A, no B data |

Do not forge or sign JWTs outside the application's authentication flow.
Machine connector-scope and pending-2FA tokens are separately regression-tested
in CI; to test live, use only existing approved connector/2FA fixtures and
never expose their secrets or provision new external connectors.

### Distinguish failures

- A **403** on the stale Manager token after demotion is the critical Gate 6C
  finding; 401 may indicate an expired/revoked token, not successful role
  freshness. If the token expires or is refreshed, restart with a freshly
  authenticated Manager and repeat the same-token before/after test.
- The signed Manager token must produce M01=200 **before** mutation. A blanket
  authorization failure is not passing evidence.
- A 503 on live workspace checks can indicate unavailable or stale source
  snapshots and cannot be counted as a passed finance-scope check.
- If the user is disabled before logging in, or if a fresh token is minted
  after the mutation, that does not prove old-session revocation.
- Any unexpected 2xx on a negative check is a release blocker. Do not
  send employee invitations while any check fails.

## Evidence and cleanup

Operator records a private evidence file (never raw JWTs, passwords, service
role secrets, or sensitive response bodies) containing for each check:
ID, UTC timestamp, method/path, HTTP status, a one-way token fingerprint,
optional request ID, and a clear pass/fail. Record authorized change events
with timestamp and before/after **role or company only**, a synthetic ID,
and the authorized staging change ticket. A reviewer independently checks
that the M01–M05 token fingerprint is identical.

After finishing, delete **only** the synthetic accounts, staff, and snapshots
created for this run using the approved staging cleanup workflow. Independently
query the isolated staging project for zero remaining records with that unique
test marker. No new Railway service or cron should remain. Record cleanup
query time and verified zero counts. Check staging health again and confirm
no unexpected pending deployments, email jobs or vendor writes.

**Gate 6C is accepted only when:** the live results above are all green,
the same-token demotion is demonstrated, cleanup is verified, a second reviewer
signs the evidence, and all CI/CodeQL checks remain green. Until then,
the gate stays `LIVE_ACCEPTANCE_PENDING` and real employee onboarding stays
on hold.

Related evidence: PR #109 (Gate 6C implementation), PR #110 (real FastAPI
route-level regression), `backend/tests/test_gate6c_mounted_app_contract.py`.
