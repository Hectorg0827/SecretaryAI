# SecretaryAI handoff update — October 17, 2026 (Phase 4–6)

**THIS UPDATE SUPERSEDES the older handoff below where the status conflicts.**
Work exclusively on `claude/blissful-noether-ik7p9l`, PR #74 against
`claude/secretaryai-tech-spec-lqBZj`. No other branch or PR was opened.

## Confirmed baseline at session start

- HEAD was `5225cebd265000e6f2131e8d71bf463bfdcf0a64`.
- PR #74 open/mergeable. CI run 39000256749 **success** and CodeQL run
  39000256734 **success** at that baseline. Individual dependency-audit job
  **failed**, consistent with preexisting npm advisories; whole CI was success.
- GitHub releases: v1.0.5, v1.0.4 and v1.0.1 drafts had installer assets,
  but no published release had any assets. The published v1.0.0 and tag_1
  releases each had zero assets.
- `https://api.secretaryai.com/health` could not be accessed; sandbox DNS
  lookup returned `gaierror`. No externally healthy backend was verified.
- A local checkout at `/home/user/SecretaryAI` was unavailable to this tool
  environment; changes were made directly through the authenticated GitHub
  connector. No local pytest/build exit code was observed. **Fresh CI on the
  modified tip must pass before calling these fixes tested.**

## Work committed to the single existing PR (NOT YET CERTIFIED)

1. **#53**: `backend/app/api/computer_use.py` now uses Redis hashes for
   session records, completion results and pending approvals; Lua atomic
   decision CAS validates tenant, pending status and approval ID, and
   decisions are polled across worker processes. Redis errors return 503.
   Test: `test_phase4_release_security.py`. Still needs actual two-worker
   Redis and frontend/engine validation.
2. **#56**: all `create_invoice`, `create_po`, `update_item` requests
   to the unshipped connector reject with 403 **before insertion**.
   Legacy queued writes are cancelled on task polling. This is an intentionally
   fail-closed mitigation, not an implemented approval workflow; future
   connector writes must use PolicyEngine + durable approval and must be
   tested before enabling.
3. **#55**: generated install secrets are one-time outputs; DB stores
   `sha256:` + SHA-256 digest only. Registration accepts only digests.
   Database migration is in three places for the available deployment paths:
   `database/schema_v4_connector_secret_digest.sql`,
   `backend/supabase/migrations/20261017000000_connector_install_secret_digest.sql`,
   `supabase/migrations/20261017000000_connector_install_secret_digest.sql`.
   These are equivalent, not three sequential migrations: apply **one**
   appropriate to the deployment tool. Manual bootstrap now requires
   schema.sql → schema_v2_additions.sql → schema_v3_integrations.sql →
   **schema_v4_connector_secret_digest.sql**, before deploying new code.
   CI schema validation was updated. Actual production migration NOT run.
4. **#54**: token checks and token rotation reject Redis failures with a
   dedicated exception; user auth and refresh return 503; critical log and
   Sentry exception emission added. Production Redis rate limiters also
   fail closed (503); DEBUG-mode development fallback retained.
   No real on-call paging is wired yet. Mutation evidence and CI pending.

Added `backend/tests/test_phase4_release_security.py`, covering the above
negative cases, wrong-tenant CU and duplicated approvals, digest-only
registration, queued legacy write filtering, Redis-outage token denial, and
production rate limiting. The CI workflow intentionally mutates the
connector write gate and requires **three failures** in the negative suite;
that CI step has not yet been observed completing.

## Final safety checks added after the first handoff commit

- Six security mutations now live in `scripts/mutation_phase4.py`. Backend CI
  runs this after full pytest; it intentionally breaks the CU tenant check,
  connector write gate, connector secret digest storage, JWT Redis fail-closed
  branch, production login rate limiter, and production request middleware.
  Each mutant must cause its targeted negative test to **fail**; the script
  restores the original file in `finally`. **No passing CI run has yet been
  observed** for this new gate.
- The backend CI job was updated to start a **real Redis 7 service** on port
  6379, because production JWT revocation is correctly fail-closed, and tests
  that use real JWTs must no longer rely on silently unavailable Redis.
  New outage-specific negative tests explicitly patch Redis to fail. The
  test service does not prove production Redis persistence.
- `docs/BACKUP_RESTORE.md` and `docs/DEPLOY_CHECKLIST.md` now demand
  durable Redis persistence/AOF. Otherwise restarting Redis with an empty
  revoked-token blacklist can resurrect previously revoked unexpired JWTs.
  Verify old revocations survive an actual restart or change the storage model
  before production sign-off. **No Redis persistence or restart proof exists.**
- The latest committed workflow and documentation changes were not part of
  the prior handoff commit; consult the **latest PR #74 SHA**, not the old
  baseline, and wait for new exact-SHA CI/CodeQL results.

## Additional work

- Corrected the release workflow body, CI connector comment and README:
  the local Python QB Desktop connector is NOT inside the desktop installer.
  Its tested source and PyInstaller packaging remain in the repository.
  **Existing draft release descriptions still have the false claim**.
  The connected GitHub actions did not expose a release-edit endpoint;
  an owner must use Releases → draft → Edit → remove it. The published
  historical v1.0.0 note also needs correction.
- Added a manual staging workflow `.github/workflows/e2e-contract.yml`,
  scripts `e2e_web.py` and `integration_contracts.py`, plus
  `docs/E2E_ACCEPTANCE.md`. These require a real deployed staging web/API
  host and QuickBooks sandbox, Gmail and SendGrid credentials. **No E2E
  or live vendor contract test has run.** This is not criterion completion.
- Added attorney-review-only `docs/TERMS_OF_SERVICE_DRAFT.md` and
  `docs/PRIVACY_POLICY_DRAFT.md`. They are not linked or published.
- Added `docs/HEALTH_ALERT_RUNBOOK.md` and a truthful unperformed
  `docs/BACKUP_DRILL_EVIDENCE.md`; no page or DB restore occurred.
- `docs/LICENSE_DECISION.md`: owner must choose proprietary or specific
  open-source license and legal entity; **no LICENSE was invented**.
- Updated `docs/PRODUCTION_READINESS.md` with unverified status.

## The 15 GA exit criteria — evidence status after these changes

| # | Status |
|---|---|
| 1 | Owner-blocked: host/payment/DNS, /health not reached |
| 2 | Owner-blocked: published release with .dmg and .exe unavailable |
| 3 | Blocked by #2 and clean Windows VM install proof |
| 4 | Blocked by #2 and clean macOS install proof |
| 5 | Blocked by #1; manual browser E2E added but not run |
| 6 | Owner-blocked: real QuickBooks customer and sync |
| 7 | Owner-blocked: real briefing and QB reconciliation |
| 8 | Code from previous session complete; not observed on live host |
| 9 | Owner-blocked: live Stripe payment and bank settlement |
| 10 | Drafts only; counsel review, publication and live UI links missing |
| 11 | Baseline CI green on PR #74 only; fresh tip and default-branch CI still unverified |
| 12 | Owner-blocked: Apple Developer ID and Windows signing certs/test devices |
| 13 | Code mitigations committed; CI, integration/migration and approval workflow validation pending |
| 14 | Owner-blocked: actual funded Postgres backup/restore drill |
| 15 | Owner-blocked: production monitor + human escalation and observed page |

## Immediate next session steps

1. **Stop committing and inspect the final-tip CI/CodeQL jobs** until done.
   Only report exact completed-job conclusions and counts. Do not confuse
   cancelled intermediate runs with failure, and do not call queued jobs green.
2. Fix any actual CI/test failures **on this same branch/PR**; never skip tests.
   Ensure the mutation-testing step observes the expected security-test failure.
3. Validate Redis session state with two real uvicorn workers and a Redis
   outage/restart; exercise an approval on the opposite worker.
4. Apply the digest migration to staging before staging rollout, verify old
   plaintext converted exactly once and digest registration works.
5. Run the manual E2E workflow only after the owner supplies the real hosting
   and sandbox credentials; neither deployment nor URLs may be fabricated.
6. Keep criterion 13 open until the PolicyEngine write path is correctly
   approval-gated, or explicitly ship this connector with write tasks disabled.
7. Get counsel decisions and owner license decision; perform backup drill
   and actual on-call page. Keep all unresolved 15-criterion states accurate.

**Owner decisions remain final:** do NOT bundle the local QB Desktop connector,
keep the paid/free boundary as implemented, and fail closed with alerting on
Redis revocation outage. Do not re-ask these.
---

# Continue here — handoff for a fresh session

Written 2026-10-17 against commit `f9cc58e` on branch
`claude/blissful-noether-ik7p9l`, open as PR #74 against the default branch
`claude/secretaryai-tech-spec-lqBZj`.

**Read this instead of the "verified state" section of `ROADMAP_TO_GA.md`,
which is now stale in several places.** The 15 exit criteria in that file are
still the definition of done.

---

## Decisions the owner has already made — do not re-ask

1. **QuickBooks Desktop connector: do NOT ship it.** Keep the repaired code,
   its tests and the PyInstaller packaging in the repo, but do not bundle it
   into the installer and do not add a first-run flow. Reason below.
2. **Paid/free boundary:** everything is paid except sign-up/sign-in, billing,
   setup, settings, webhooks, admin and the API docs. Already implemented.
3. **Defect #54 (token revocation during a Redis outage): fail closed, plus
   alerting.** Not yet implemented.
4. **One pull request.** This repo previously accumulated 55 open Dependabot
   PRs and it buried real review. Keep working on the existing branch.

---

## What this session changed

### Exit criterion 11 (CI green) — root-caused and fixed

`ROADMAP_TO_GA.md` records the `Desktop native (fmt / clippy / test)` job as
"runner-specific and **undiagnosed**", passing locally. That is wrong. The
runner log says:

```
error: proc macro panicked --> src/lib.rs:305:16
  .build(tauri::generate_context!());
  = help: The `frontendDist` configuration is set to "../dist" but this
          path doesn't exist
```

`tauri::generate_context!()` resolves `frontendDist` **at compile time**, so
the crate cannot build without `desktop/dist/` — a gitignored build output
that the job never produced. It passed locally only on machines where someone
had run `npm run build` at some point. The job now builds the UI first, in
development mode (the production guard in `desktop/vite.config.ts` correctly
refuses a production build without a real `VITE_API_URL`, and this job ships
nothing).

**The `CI` workflow now concludes `success`.** Verified on `f13ac8a`,
`2ef1f6b` and `735ca8e`.

Also note: the roadmap says one red job. There were **two**. `Dependency
audit` is red as well — 21 pre-existing npm advisories in `frontend/`. It is
`continue-on-error: true` and equally red on the default branch, so it does
not gate; see the standing-down comment on PR #74. Clearing it is a deliberate
triage pass of its own, not this PR's job.

### The CSP would have blocked every API call (worst bug found)

`desktop/src-tauri/tauri.conf.json` pinned
`connect-src ... https://api.secretaryai.com`. The desktop UI calls the backend
with the **webview's own `fetch()`** (`desktop/src/auth.ts` — it does not use
`tauri-plugin-http`, which would bypass the webview CSP), so that policy is
enforced on every request. Release builds have the inspector disabled, so
there is no console in which the violation appears.

A correct deployment with `API_URL` set would still have produced an installer
where sign-in silently did nothing. `scripts/set_desktop_api.py` now rewrites
the directive from the URL being baked in, and `release-desktop.yml` runs it.

### Release workflow no longer invents a backend

It defaulted to `vars.API_URL || 'https://api.secretaryai.com'`, a host with no
DNS. Because the URL is baked into the installer permanently, that silently
produced installers that fail on first sign-in. It now refuses to build when
`API_URL` is unset and names the exact GitHub settings page.

### Celery beat was missing from the deployment guide

`RAILWAY_DEPLOYMENT.md` described four services and never mentioned Celery
**beat**. The worker only runs work something enqueues; beat is what enqueues
it. Without beat, nothing in `celery_app.py`'s `beat_schedule` fires — no 7am
briefing, no overnight scan, no sync check — while the deployment looks
perfectly healthy. Exit criterion 7 was unreachable on its own. Fixed, and
`docs/DEPLOY_CHECKLIST.md` makes it a numbered step.

### Dockerfile honours `$PORT`

It hardcoded `--port 8000`. Railway, Render, Heroku and Cloud Run all assign a
port and route only to that port, so the service would have been unreachable
with no obvious cause. Now binds `${PORT:-8000}`.

### The connector never worked at all

Worse than "unpackaged". `connector/executor.py` did
`from backend.app.connectors.qb_desktop import ...`, but that module does
`from app.connectors.base import ...` internally, so it needs `backend/` — not
the repo root — on `sys.path`. Every `fetch_*` task failed with
`No module named 'app'` regardless of credentials. Only `ping` worked, and
nothing caught it because the connector had no tests.

Fixed via `connector/qb_adapter.py`, plus: state moved from the working
directory to per-user appdata (0600, atomic write), `ConfigError` instead of a
bare `KeyError`, version drift 1.0.0 → 1.0.6 now covered by
`scripts/check_versions.sh`, rotating file logging, and a `--selfcheck` flag
that loads the adapter from inside the frozen bundle. 15 new tests.

Proven, not assumed: the packaged binary run against a local fake cloud
registered, persisted its token, heartbeated, polled a task and executed
`fetch_customers`, failing with `403 Forbidden` from the sandbox egress proxy
rather than `No module named 'app'`.

### Why it is still not shipped

Two facts found while reading the Conductor integration end to end:

1. **QuickBooks Desktop already works without it.**
   `backend/app/api/setup.py` creates a Conductor EndUser and returns
   Conductor's `authFlowUrl`; the customer installs the `.QWC` into
   QuickBooks Web Connector, and the backend then reads QuickBooks through
   `QBDesktopAdapter` from the cloud (`unified_adapter.py`,
   `access_router.py`, `agent.py`). `desktop/src/pages/Setup.tsx` already
   drives this. The local connector is a **second path to the same Conductor
   API** — it runs on the customer's PC and then calls `api.conductor.is` over
   the internet, with no local QuickBooks access the backend lacks.
2. **Shipping it means putting the master Conductor key on customer machines.**
   `settings.conductor_api_key` is one global server-side secret; only
   `conductor_end_user_id` is per-company (`database/schema.sql:23`). The
   connector reads `CONDUCTOR_API_KEY` from its environment and there is no
   per-customer key to give it.

Hence the owner's decision not to ship it.

### Phase 3 (billing) is complete

`require_active_subscription` was applied to **zero** routes, so every signup
got the whole product free. Now applied per router in `app/main.py`, with each
free router's reason recorded beside it. `connectors` is gated per endpoint
instead, because `/heartbeat`, `/tasks`, `/task-result`, `/sync-status` and
`/error` authenticate with a connector token and a user-subscription check
would 401 the agent.

`BILLING_ENABLED` (default true) ties the paywall to Stripe: with billing on
and `DEBUG=false`, a missing `STRIPE_SECRET_KEY` or `STRIPE_WEBHOOK_SECRET`
**fails startup**. Previously billing fell back to mock mode — checkout
returned `https://mock-stripe.example.com/...` and webhooks were ignored,
which is indistinguishable from working software until no money arrives.

`tests/test_paywall.py` has 48 tests and was **mutation-tested**: un-gating a
router, mounting an unclassified router, and making the guard admit everyone
each produce failures. `tests/conftest.py` treats every tenant as paying by
default (so unrelated tests do not need a fake subscription) and sets
`DEBUG=true`, because a test run is a development environment.

Backend suite: **1137 passed, 5 skipped, exit 0** (was 1086 at session start).

---

## Do this next

### Phase 4 — close defects #53–#56 (`docs/PRODUCTION_READINESS.md`)

The owner chose this as the next phase. In severity order:

1. **#53 — Computer-Use is broken in production today.** Sessions live in a
   per-process dict while the server runs 2 uvicorn workers, so polling and
   approval hit the wrong worker and 404. Move the state to Redis. Note the
   Dockerfile `CMD` still sets `--workers 2`; that is correct, the state is
   what is wrong.
2. **#56 — write tasks bypass the approval queue.** `create_invoice`,
   `create_po` and `update_item` dispatched to the connector skip the
   PolicyEngine approval that `connector_protocol.py` says they require.
   Related finding: `connector/executor.py`'s `SUPPORTED_TASKS` does not
   implement those three at all, so they would also fail as
   "Unsupported task type" — `connector/tests/test_connector.py` pins that.
3. **#55 — install secret stored in plaintext** despite the docstring on
   `generate_install_secret` in `app/api/connectors.py` claiming it is hashed.
   Store a digest; this is a storage migration, so it needs a schema file
   applied in the documented order.
4. **#54 — token revocation fails open during a Redis outage.** Owner decided:
   **fail closed, with alerting.** Note `/health` already returns 503 when
   Redis is down, so fail-closed is consistent with what the platform already
   believes.

### Phase 2 leftover (small)

Per the decision not to ship the connector: remove the claim that the QB
Desktop connector is "included in the installer". It appears in the release
body of `.github/workflows/release-desktop.yml` and in the existing draft
releases' notes. Check `README.md` and the desktop UI for the same claim.

### Phase 5 — earn trust in the build

CI is green, so the remaining work is tests that prove the product works:
one browser test that signs up, signs in and loads the dashboard against a
running backend; one contract test per external integration (QuickBooks,
Gmail, SendGrid) against sandbox credentials. There are currently **zero**
end-to-end tests — 1137 passing unit tests have never proven the product
works. Code signing is already wired in `release-desktop.yml` and activates
when the secrets exist.

### Phase 6 — legal and operational

Terms of Service and Privacy Policy consistent with
`docs/PRIVACY_AND_DATA_FLOW.md`, **clearly marked as drafts requiring a
lawyer** — this product touches accounting records and email. A `LICENSE`
reflecting the owner's intent (not yet stated; ask). A real backup/restore
drill per `docs/BACKUP_RESTORE.md`, which needs a real database. Alerting so a
human is paged when `/health` fails.

---

## Exit criteria status

| # | Criterion | State |
|---|---|---|
| 1 | Backend reachable, `/health` → `ok` | owner-blocked (hosting + payment). Prep complete: `docs/DEPLOY_CHECKLIST.md`, `scripts/smoke_test.sh` |
| 2 | `/releases/latest` serves `.dmg` + `.exe` | owner-blocked — two clicks, after #1 |
| 3 | Clean Windows install | blocked by #2 |
| 4 | Clean macOS install | blocked by #2 |
| 5 | Sign-up → sign-in works | blocked by #1; the CSP blocker is removed |
| 6 | QuickBooks syncs a real company | owner-blocked — a real customer |
| 7 | Briefing matches QuickBooks | owner-blocked; the missing beat service is fixed |
| 8 | Paid plan gates access | **code complete, 48 tests**; observing it needs #1 |
| 9 | Real card charged | owner-blocked — live Stripe keys |
| 10 | ToS + Privacy published | not started — Phase 6 |
| 11 | CI green on default branch | **fixed**; green on PR #74, closes on merge |
| 12 | Installers signed | owner-blocked — Apple Developer ID $99/yr, Authenticode ~$200–400/yr |
| 13 | #53–#56 closed | **not started — do this next** |
| 14 | Restore drill performed | owner-blocked — needs a real database |
| 15 | On-call paging on `/health` failure | not started — Phase 6 |

---

## The two things only the owner can do, in order

1. Work through `docs/DEPLOY_CHECKLIST.md` (Supabase, Railway with **five**
   services, the environment variables, then `scripts/smoke_test.sh` until it
   prints `RESULT: GREEN`).
2. Set the `API_URL` repository variable, run the **Release Desktop App**
   workflow, confirm four installers are attached to the draft, then publish
   it. The agent token can neither dispatch workflows nor push tags.

## Verification commands

```
cd backend && python -m pytest -q                 # expect 1137 passed, 5 skipped
python -m pytest connector/tests -q               # expect 15 passed
bash scripts/check_versions.sh                    # expect all match 1.0.6
python3 scripts/set_desktop_api.py --check        # reports the current CSP
cd desktop && npm ci && npm run build -- --mode development
cd desktop/src-tauri && cargo fmt --all --check && cargo clippy --all-targets && cargo test
```

The desktop Rust checks need the GTK deps the CI job installs:
`libwebkit2gtk-4.1-dev libgtk-3-dev libappindicator3-dev librsvg2-dev patchelf libssl-dev`.
`cargo clippy` fails with the `generate_context!` panic above unless
`desktop/dist/` exists, so run the Vite build first.
