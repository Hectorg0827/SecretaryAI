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
