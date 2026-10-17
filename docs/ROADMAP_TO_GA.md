# SecretaryAI — Road to GA

Two things live here: a **definition of done** that is actually measurable, and a
**prompt** to hand an agent so it works the right problems in the right order.

Ground truth below was verified on **2026-10-17** against `e8973aa`. Re-verify
before trusting it — this file goes stale.

---

## What "100% ready for market" means

GA is not "the code is good." It is this sentence being true:

> A non-technical importer, on their own Windows or Mac machine, can download
> the app, install it, sign in, connect their real QuickBooks, receive a correct
> morning briefing, and be charged for it — without anyone from the team
> touching their machine.

Every criterion below is binary and externally checkable. Partial does not count.

| # | Exit criterion | Verified by |
|---|---|---|
| 1 | Backend reachable at a stable HTTPS host; `/health` returns `status: ok` | `curl https://<host>/health` from a machine outside the deploy |
| 2 | `/releases/latest` serves a `.dmg` and an `.exe` to a logged-out visitor | open the URL in a private browser window |
| 3 | A clean Windows VM installs and launches with no admin rights | screen recording of the install |
| 4 | A clean macOS machine installs and launches | same |
| 5 | Sign-up → sign-in works against the deployed backend | do it on a machine that never saw the repo |
| 6 | QuickBooks connects and syncs real data for one real company | the customer's own file, not a sandbox |
| 7 | A morning briefing arrives by email with numbers that match QuickBooks | the customer confirms the numbers |
| 8 | A paid plan actually gates access; an unpaid account is refused | create both and compare |
| 9 | A real card is charged and the money arrives | Stripe dashboard |
| 10 | Terms of Service and Privacy Policy are published and linked from the app | the live URLs |
| 11 | CI is green on the default branch | Actions tab |
| 12 | Installers are signed; no SmartScreen or Gatekeeper warning | install on a clean machine |
| 13 | Open P0/P1 defects in `PRODUCTION_READINESS.md` are closed | the register |
| 14 | A database restore has been performed from backup, start to finish | the drill log |
| 15 | An on-call path exists: someone is paged when `/health` fails | trigger it on purpose |

Criteria 6, 7, 9, 12 and 14 **cannot be satisfied by an agent.** They need a
customer, a bank, a certificate authority, and a human watching a restore.

---

## Verified state (2026-10-17, `e8973aa`)

Green, with evidence:
- Backend suite: 1086 passed, 4 skipped. Backend CI job green.
- Frontend and desktop both build clean; lint, typecheck, version-consistency,
  DB-schema validation all green.
- gitleaks clean. Every `sk-ant-` string in history is a placeholder — checked
  by hand, no key was ever committed.
- Row-level security is real: 35 tables with RLS enabled, 33 policies.

Red, with evidence:
- **No backend deployed.** `api.secretaryai.com`, `app.secretaryai.com` and
  `secretaryai.com` all fail DNS resolution. The desktop build bakes that URL in.
- **No installer published.** Releases holding assets (`v1.0.5`, `v1.0.4`,
  `v1.0.1`) are drafts; the only published release, `v1.0.0`, has zero assets.
- **QB Desktop connector is not shipped.** It is Python in `connector/`. No
  workflow builds it; `tauri.conf.json` declares no `resources`/`externalBin`.
- **Paywall is inert.** `require_active_subscription` is applied to zero routes.
- **No Terms, Privacy Policy, or LICENSE.**
- **CI red** on `Desktop native (fmt / clippy / test)`. Clippy passes locally
  with 0 errors on Rust 1.99.1 (same floating `stable` CI uses) with the
  documented GTK deps, so the cause is runner-specific and **undiagnosed** —
  CI log download is blocked from the agent sandbox.
- **Zero end-to-end tests.** 22 of 48 backend test files are mock-based, 0 are
  integration-marked, no browser/UI tests exist. No code here has ever made a
  real call to QuickBooks, Gmail or SendGrid.
- Open defects #53–#56 confirmed still open (see `PRODUCTION_READINESS.md`).
- Installers unsigned. Mobile has no `eas.json`, so it cannot be distributed.

---

## The prompt

Paste this into a fresh session.

```
You are working on SecretaryAI at /home/user/SecretaryAI, branch
claude/secretaryai-tech-spec-lqBZj (this is the default branch; there is no
main). It is an AI operations manager for small importers: a FastAPI backend,
a React web app, a Tauri desktop app, and a Python QuickBooks Desktop connector.

GOAL: move the product toward GA as defined in docs/ROADMAP_TO_GA.md. Read that
file first. It lists 15 exit criteria and the verified state as of 2026-10-17.

START BY RE-VERIFYING, NOT BY TRUSTING THAT FILE. Check: current branch tip, CI
status on the default branch, whether any release has published assets, and
whether the production API host resolves and answers /health. State what has
changed before doing anything else.

WORK IN THIS ORDER. Do not start a phase until the one before it is done,
because each is worthless without its predecessor.

PHASE 1 — Make the product reachable (blocks everything else)
  The app points at a backend that does not exist. Until one does, every other
  improvement is invisible to customers.
  - Deploy the backend. docs/RAILWAY_DEPLOYMENT.md is the intended path.
    Prepare everything you can: Dockerfile correctness, env var inventory,
    migration order, a smoke script that proves /health is green.
    The account creation, payment and DNS are the owner's to do — produce an
    exact, numbered checklist for them and stop there. Do not invent a URL.
  - Once a real host exists, set the API_URL repository variable, run the
    "Release Desktop App" workflow, and publish the resulting draft release.
    NOTE: the agent sandbox cannot dispatch workflows (token lacks actions:write)
    and cannot push tags. Hand those two clicks to the owner explicitly.

PHASE 2 — Make the core promise real
  QuickBooks Desktop is the differentiator and it is currently undeliverable:
  the connector is an unpackaged Python script customers have no way to run.
  Decide, with the owner, one of:
    (a) package it — PyInstaller or similar, built in CI, bundled via
        tauri.conf.json resources/externalBin, with a first-run flow that
        launches it; or
    (b) cut QuickBooks Desktop from v1 and ship QuickBooks Online only,
        removing every claim about Desktop from README, release notes and UI.
  Do not leave it in the current state, where the marketing says one thing and
  the installer delivers another. Recommend (b) for a first release unless the
  owner's actual customers are on Desktop.

PHASE 3 — Make it billable
  - Apply require_active_subscription to the routes that should be paid. It is
    currently applied to zero routes, so every signup gets everything free.
    Decide the free/paid boundary with the owner before wiring it.
  - Stripe runs in mock mode unless STRIPE_SECRET_KEY is set. Make the failure
    loud: refuse to start in production if billing is enabled but unconfigured.
  - Add tests proving an unpaid account is refused and a paid one is not.

PHASE 4 — Close the known defects
  Defects #53–#56 in docs/PRODUCTION_READINESS.md, all re-confirmed open:
  - #53 Computer-Use sessions live in a per-process dict while the server runs
    2 uvicorn workers, so polling and approval hit the wrong worker and 404.
    Move the state to Redis. This feature is broken in production today.
  - #56 Write task types (create_invoice, create_po, update_item) dispatched to
    the connector bypass the PolicyEngine approval queue that
    connector_protocol.py says they require.
  - #54 Token revocation fails open during a Redis outage: logged-out tokens
    keep working. Decide fail-closed vs loud alerting with the owner.
  - #55 Connector install secret is stored in plaintext despite a docstring
    claiming it is hashed. Store a digest; this is a storage migration.

PHASE 5 — Earn trust in the build
  - Fix the red CI job (Desktop native / clippy). It passes locally on the same
    toolchain, so read the actual runner log — you may be able to when the
    sandbox proxy allows it; otherwise ask the owner to paste it.
  - Add end-to-end tests. There are none. At minimum: one browser test that
    signs up, signs in and loads the dashboard against a running backend; one
    contract test per external integration (QuickBooks, Gmail, SendGrid) run
    against sandbox credentials. 1086 passing mock-based unit tests have never
    proven the product works.
  - Code signing (Apple Developer ID, Windows Authenticode) is already wired in
    release-desktop.yml and activates when the secrets exist. Buying the
    certificates is the owner's task; tell them precisely what to buy.

PHASE 6 — Legal and operational
  - Draft Terms of Service and a Privacy Policy consistent with
    docs/PRIVACY_AND_DATA_FLOW.md. Mark them clearly as drafts requiring a
    lawyer's review — this product touches accounting records and email, so do
    not present your drafts as sufficient. Add a LICENSE reflecting the owner's
    intent.
  - Run a real backup-and-restore drill per docs/BACKUP_RESTORE.md and record
    the result. Set up alerting so a human is paged when /health fails.

RULES
  - Work on the existing branch. Do not open a pile of pull requests: this repo
    previously accumulated 55 open Dependabot PRs and it buried real review.
  - Verify every claim before making it. Run the tests, run the build, check
    the exit code. "Should work" is not a result.
  - When something is blocked on a purchase, a credential, a legal review or a
    real customer, say so plainly, name exactly what is needed from whom, and
    move to work that is not blocked. Never simulate a deployment, fabricate a
    URL, or mark an exit criterion met that you did not observe.
  - Report progress against the 15 exit criteria by number, and say which are
    still owner-blocked.
  - The owner is not a developer. Write instructions for them as numbered
    clicks, not shell commands, wherever a UI exists.
```

---

## What is owner-blocked no matter who does the work

| Blocker | Who | Rough lead time |
|---|---|---|
| Hosting account + payment method, DNS for the API domain | Owner | hours |
| Running the release workflow and publishing the draft | Owner (two clicks) | minutes |
| Apple Developer ID ($99/yr) + Windows Authenticode certificate | Owner, purchase | days to weeks |
| Lawyer review of Terms and Privacy Policy | Owner + counsel | days to weeks |
| QuickBooks / Gmail / SendGrid production credentials | Owner | hours to days |
| One real importer willing to connect their books | Owner, sales | unknowable |

An agent can get you to roughly criteria 1–5, 8, 11 and 13, plus drafts for 10.
The rest need a person, a card, or a customer.
