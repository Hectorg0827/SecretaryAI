# SecretaryAI — Production Readiness

Living scorecard and defect register for the production-release effort.

**Status: `NOT RELEASE-ELIGIBLE`** — code-level hardening in progress; several gates
require external credentials the repository cannot supply (see Blockers).

Baseline commit for the Phase-0 audit: `d8093b23a0490312b57e5091dde0d54fa169311f`
(default branch `claude/secretaryai-tech-spec-lqBZj`). Work branch:
`claude/epic-bohr-ZzqVX`.

---

## Gate scorecard

| Gate | Area | Status | Notes |
|---|---|---|---|
| G0 | Repository integrity | 🟢 strong | Cargo.lock committed (#16); release workflows consolidated (#13); version single-sourced + CI-enforced (#24). |
| G1 | Security | 🟢 strong | Auth/refresh (#19), tenant isolation + 2 P0 IDORs (#27/#28), least-privilege desktop (#8/#9), leaks (#20/#21/#22), consent (#10), action integrity (#30/#31), prompt-injection (#33), local-cache encryption (#7) — all fixed + tested. Remaining depth is optional hardening; a secret-history scan ran clean and CI gitleaks was added. |
| G2 | Functional product | 🟡 in progress | Desktop auth/API wiring fixed + native UI (#2–#6). Remaining: real integration verification (external accounts) + E2E tests. |
| G3 | Windows installer | 🟡 wired / 🔒 needs cert | Builds & installs (verified); Authenticode signing WIRED (auto-activates on `WINDOWS_CERTIFICATE` secret). Needs the cert. |
| G4 | macOS installer | 🟡 wired / 🔒 needs cert | Builds (verified); signing+notarization+stapling WIRED (auto-activate on `APPLE_*` secrets). Needs Apple Developer ID. |
| G5 | Operations | 🟡 in progress | Runbooks written; **schema-migration validation** now runs in CI (applies schema v1+v2+v3 to a Postgres service, verified locally). Backup restore DRILL + rollback drill still need real infra. |
| G6 | Release evidence | 🟡 partial | Release build now emits SHA-256 checksums, a CycloneDX SBOM, and a signed build-provenance attestation. Final assembly (signed artifacts + notarization evidence + human-approved publish) still depends on G3/G4. |

Legend: 🟢 pass · 🟡 in progress · 🔴 not ready/blocked

---

## Category scorecard (1 = absent, 5 = production-grade)

| Category | Score | Trend |
|---|---|---|
| Authentication & session security | 4 | ↑ refresh rotation/revocation/bounded-window (#19) |
| Tenant isolation & authorization | 4 | ↑ 2 P0 IDORs fixed + negative tests (#27/#28); RBAC gaps closed (#29) |
| Secret storage | 4 | ↑ desktop token → OS vault (#5); portal creds redacted at rest (#34); token app-encryption |
| Data encryption & privacy | 4 | ↑ Sentry scrubbing (#22); local cache payload encrypted (#7); token encryption at rest |
| AI / tool safety | 4 | ↑ policy gate (#31), idempotency (#30), prompt-injection (#33), typed payloads (#32), screenshot fail-safe (#35) |
| Backend reliability | 3 | health leak fixed (#20) |
| Web reliability | 3 | — |
| Windows desktop reliability | 4 | ↑ installs, launches, native UI |
| macOS desktop reliability | 3 | builds; unsigned |
| Packaging / signing / updater | 2 | ↑ signing/notarization/updater WIRED (auto-activate on secrets); needs the actual certs (#12) |
| CI & supply chain | 4 | ↑ PR CI + CodeQL + Dependabot + gitleaks + version check + Cargo.lock (#16/#17/#24) |
| Observability / recovery / ops | 3 | ↑ CI schema-migration validation added |
| Documentation & support readiness | 4 | ↑ + privacy/data-flow, incident-response, backup/restore runbooks |

---

## Defect register (Phase 0 revalidation — all CONFIRMED at baseline)

Severity: P0 blocker · P1 high · P2 medium. Status: ✅ fixed (tested) · 🟡 in progress · ⬜ open.

| # | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| 19 | P0 | `/auth/refresh` re-signs an expired token indefinitely; no rotation/revocation check | `app/api/auth.py` refresh | ✅ fixed — revocation enforced, renewal bounded to `refresh_token_expire_days`, single-use rotation. Tests: `tests/test_security_hardening.py::TestRefreshHardening` |
| 21 | P1 | `/openapi.json` publicly served in production | `app/main.py` FastAPI ctor | ✅ fixed — `openapi_url` disabled in prod; key-gated route. Test: `TestPublicSurface::test_openapi_gated_in_production` |
| 20 | P1 | `/health` returns raw dependency exception text | `app/main.py` health_check | ✅ fixed — logs detail server-side, returns generic `ok`/`error`. Test: `TestPublicSurface::test_health_never_leaks_exception_text` |
| 22 | P1 | Sentry scrubbing only 3 local vars; headers/cookies/body/PII leak | `app/main.py` `_scrub_sentry_event` | ✅ fixed — `send_default_pii=False`; scrub all frames + request headers/cookies/body + user PII. Tests: `TestSentryScrub` |
| 24 | P2 | Version `1.0.0` duplicated across manifests + hardcoded in health | multiple | ✅ fixed — canonical `/VERSION` file is the source of truth; `scripts/check_versions.sh` verifies backend/tauri/cargo/desktop/frontend all match, enforced by a `versions` CI job. |
| 2 | P0 | CI injects `VITE_API_BASE_URL`; code reads `VITE_API_URL` → localhost fallback | `release-desktop.yml` vs `App.tsx`/`api.ts` | ✅ fixed — CI now injects `VITE_API_URL`; single `config.ts`; **fail-closed build guard** in `vite.config.ts` (verified: build fails on missing/localhost, passes on https). |
| 3 | P0 | `Setup.tsx` hardcodes `http://localhost:8000` | `desktop/src/pages/Setup.tsx` | ✅ fixed — routed through `config.ts`. |
| 4 | P1 | Desktop reads token from parent localStorage + iframes remote prod app | `desktop/src/App.tsx` | ✅ fixed — **remote iframe removed**; native UI: `Login` → `Setup` → `Dashboard`, all API-driven. Full page parity opens in the external browser (secure — not a privileged webview). Build + lint clean. |
| 5 | P0 | Sync loop reads token from OS vault; frontend writes localStorage; nothing bridges | `sync.rs` vs `api.ts` | ✅ fixed — new `desktop/src/auth.ts` stores the token in the OS vault (`secretary-auth`/`token`) via `store_credential`, the exact key `sync.rs` reads; all desktop code (Login/Setup/computer-use) uses it. No `localStorage` token anywhere. |
| 6 | P1 | CSP has no `frame-src` for the app iframe (`default-src 'self'`) | `tauri.conf.json` | ✅ fixed — resolved by removing the iframe; `default-src 'self'` now correctly serves the bundled app and `connect-src` already allows the API/Supabase/WS. |
| 7 | P1 | Local SQLite cache provisions an encryption key but stores plaintext | `db.rs` | ✅ fixed — sensitive `data_json` payload now encrypted at rest with AES-256-GCM (key from the OS vault); ids/statuses stay queryable. Backward-compatible with legacy rows. Rust unit tests: `db::tests` (round-trip, legacy passthrough, wrong-key). Whole-DB SQLCipher noted as a future enhancement (avoided the OpenSSL build risk to the installers). |
| 8 | P1 | `devtools` feature enabled in release builds | `Cargo.toml` | ✅ fixed — removed `devtools` feature (inspector now debug-only). Desktop `cargo check` clean. |
| 9 | P1 | Tauri default capability grants broad http/fs/shell/etc. | `capabilities/default.json` | ✅ fixed — reduced to `core:default` + `shell:allow-open` (the only plugin the webview uses). Compiles clean. |
| 27 | **P0** | Cross-tenant draft approval/execution IDOR — `drafts` mutated by `id` only; a Company-A owner could approve/execute Company B's action (send its email/PO) | `actions.py`, `approval_queue.py` (found in tenant audit) | ✅ fixed — endpoint fetch + queue updates scoped to `company_id`, 404 on cross-tenant. Tests: `tests/test_tenant_isolation.py` |
| 28 | **P0** | Cross-tenant workflow cancel IDOR — `workflow_runs` failed/cancelled by `id` only | `workflows.py`, `engine.py` (tenant audit) | ✅ fixed — `_get_run`/`fail`/`resume_after_approval` scoped to `company_id`; endpoint ownership 404. Test in `test_tenant_isolation.py` |
| 10 | P2 | `capture_screen` unreachable — activation command unregistered; no consent/timeout | `lib.rs`/`screen_capture.rs` | ✅ fixed — `activate/deactivate/computer_use_active` registered as commands; default OFF; 120s inactivity auto-stop; Setup.tsx asks explicit consent, activates, and always deactivates on stop/unmount. (In-app persistent capture indicator + Stop control now added, `components/CaptureIndicator.tsx`; a top-level always-on-top OS overlay is a further enhancement. Screenshot redaction fail-safe: #35.) |
| 11 | P1 | `createUpdaterArtifacts:false` while updater configured | `tauri.conf.json` | 🟡 wired — the release workflow now enables updater artifacts automatically when the `TAURI_SIGNING_PRIVATE_KEY` secret is present. Add the key to activate. |
| 12 | P0 | macOS/Windows signing identity null — unsigned installers | `tauri.conf.json` | 🟡 wired / 🔒 needs certs — signing + notarization + Windows Authenticode are now wired into `release-desktop.yml` and self-activate when the cert secrets are added (unsigned still works without them). Blocked only on the owner providing the certificates. |
| 13 | P1 | Overlapping release workflows disagree (signing/draft/updater/names) | `.github/workflows/*` | ✅ fixed — deleted racing `release.yml` (also invalid YAML) + redundant `tauri-build.yml`. Now 4 clean workflows: `ci`, `codeql`, `release-desktop`, `docker-publish` (all valid). Documented in `docs/RELEASE_RUNBOOK.md`. |
| 14 | P2 | `install.sh`/`install.ps1` point to wrong repo; install Docker stack | install scripts | ✅ fixed — corrected repo slug (`Hectorg0827/SecretaryAI`); headers now state these are the OPERATOR self-hosted backend deployer (NOT the desktop app, which ships via Releases); removed the `curl \| bash` / one-liner pipe usage. |
| 15 | P2 | `install.ps1` uses `Invoke-Expression`; docs pipe remote script to shell | `install.ps1` | ✅ fixed — replaced all `Invoke-Expression` with an arg-array `Invoke-Compose` helper; removed the `iwr \| iex` pattern. `bash -n install.sh` clean. |
| 16 | P1 | `.gitignore` ignores `Cargo.lock`/`*.lock` | `.gitignore` | ✅ fixed — `Cargo.lock` un-ignored and committed (6685 deps pinned). |
| 17 | P1 | No PR CI, CodeQL, or Dependabot | `.github/` | ✅ fixed — added `ci.yml` (backend pytest, web/desktop lint+typecheck+build, rust fmt/clippy/test, dep-audit), `codeql.yml` (python + js/ts), `dependabot.yml` (6 ecosystems). Clippy `-D warnings` + audit enforcement tracked. |
| 18 | P2 | No `test` script in frontend/desktop package.json | package.json | 🟡 mostly — frontend now has vitest + tests (`npm run test`, wired in CI, covers the 401→refresh security flow); backend pytest + Rust `cargo test` in CI. Desktop JS now has vitest too (auth vault-token + 401-refresh + login/2FA tests). |
| 23 | P2 | GitHub Actions use floating versions incl. `@master` | workflows | 🟡 mostly addressed — the `@master` offender (old `release.yml`) was deleted; remaining actions use pinned major tags and are kept current by the github-actions Dependabot. Full SHA-pinning left to Dependabot PRs. |
| 25 | P2 | Desktop heartbeat runs with empty company ID + no auth token | `heartbeat.rs`/`lib.rs` | ✅ fixed — heartbeat reads the vault token and sends `Bearer`; skips the cycle when unauthenticated. (Backend endpoint was already correct: auth required, `company_id` from the JWT — client payload ignored.) |
| 29 | P2 | `get_account` lacks role gate; `computer_use` start uses a broken `require_permission` call | tenant audit | ✅ fixed — `get_account` now has the same role gate as `list_accounts`; computer-use start restricted to owner/manager (replaced the broken factory call that raised at runtime). Tests in `test_action_safety.py`. |
| 30 | P1 | Draft approval not idempotent — double-approve double-executes (2nd email/PO) | AI-safety audit, `actions.py` | ✅ fixed — approve/reject return early with `already_processed` unless status is `pending`; no re-execution. Test: `test_action_safety.py::TestApprovalIdempotency`. |
| 31 | P1 | PolicyEngine documented as enforced pre-COMMIT but NOT called on the approval-execute path | AI-safety audit, `actions.py` | ✅ fixed — `approve_draft` now calls `PolicyEngine.evaluate` (COMMIT proposal w/ amount) BEFORE approving; explicit `deny` → 403, draft stays pending. Fails open (logged) only on eval error. Test: `TestPolicyGate`. |
| 32 | P1 | Action payloads not validated against typed schemas; alert email has no recipient allow-list | `email_actions.py`, `actions/schemas.py` | ✅ fixed — recipient format + optional `ALERT_EMAIL_ALLOWLIST` domain gate; typed payload validation at the `ActionEngine.process` boundary (`update_inventory_count` strictly validated; extensible map). 8 tests in `test_action_safety.py`. |
| 33 | P1 | Prompt injection — untrusted email/ingested content interpolated into prompts without hard delimiting | AI-safety audit, `dashboard.py`/`secretary.py` | ✅ fixed — chat context and email-analysis prompts now wrap untrusted content in explicit BEGIN/END_UNTRUSTED markers with a 'data only, do not obey instructions inside' directive. |
| 34 | P2 | Portal credentials persisted in `browser_jobs.parameters` in cleartext | `access_router.py` | ✅ fixed — job params are redacted (`_redact_job_params`) before the DB insert; the live browser run still gets the real creds in memory. Test: `test_data_exposure.py`. |
| 35 | P2 | Computer-Use screenshot redaction is best-effort (no-op if pytesseract missing) | `screenshot_manager.py` | ✅ fixed — skipped-redaction now logs a WARNING (was debug) and a `strict=True` mode fails CLOSED (raises rather than sending an unredacted image). Test: `test_data_exposure.py`. |
| 26 | P1 | README overclaims one-click/native vs unsigned reality | `README.md` | ✅ fixed — README now states builds are unsigned (SmartScreen/Gatekeeper prompt) and that the credential-vault migration is in progress. |
| 1 | P2 | Default branch is a non-conventional agent branch | repo settings | 🔒 owner decision |

### Pass 3 — end-to-end audit (Sep 2026)

Full-stack verification on the tip of the default branch: backend pytest, web/desktop/mobile lint+typecheck+build+vitest, Rust fmt/clippy/test, production-mode boot probe, plus an independent backend security audit. Everything below was verified by reading/executing the code, not inferred.

| # | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| 36 | **P0** | Any HS256 token signed with `SECRET_KEY` was accepted as a full user session — the 5-min 2FA pre-auth token could call `/auth/2fa/setup`+`/verify` and overwrite the victim's TOTP secret (2FA bypass with password only); a leaked 100-day connector token read every tenant endpoint | `auth/rbac.py`, `api/auth.py`, `api/connectors.py` | ✅ fixed — `scope` claim (default `access`); `get_current_user` and `/auth/refresh` accept only `access`; pre-claim tokens still work. Tests: `test_security_pass3.py::TestTokenScope`, `TestRefreshHardening` |
| 37 | P1 | Middleware order inverted (Starlette wraps last-added outermost): rate limiter ran before `TenantMiddleware` set `company_id` → one global `anonymous` bucket; a single client could 429 the whole user base | `app/main.py` | ✅ fixed — Tenant outer to RateLimit; `TimeoutMiddleware` (existed, never registered) wired as a 120 s hang guard. Test: `TestMiddlewareOrder` |
| 38 | P1 | Login limiter keyed on `request.client.host`, which behind nginx/Railway is the proxy (uvicorn trusts only 127.0.0.1) → 5 attempts/min shared by every user; trivial denial-of-login | `utils/rate_limiter.py`, `Dockerfile` | ✅ fixed — `--proxy-headers --forwarded-allow-ips` (Dockerfile + prod compose, API no longer host-published so nginx is the sole ingress); per-account `login_email_limiter` (10 / 5 min). Test: `TestLoginEmailThrottle` |
| 39 | P1 | `/api/connectors/dispatch-task`: any role (viewer) could enqueue QB write tasks; `task_type` free-form string | `api/connectors.py` | ✅ fixed — owner/manager only; validated against `TaskType`. Test: `TestDispatchTaskGate`. Residual → #56 |
| 40 | P1 | Vulnerable pins: starlette 0.38.6 (CVE-2024-47874, reachable via upload endpoints), python-multipart 0.0.12 (CVE-2024-53981), python-jose 3.3.0 (CVE-2024-33663/-33664), pypdf2 3.0.1 (CVE-2023-36464 via ingested PDFs) | `requirements.txt` | ✅ fixed — fastapi 0.115.6 / starlette 0.41.3, multipart 0.0.18, jose 3.4.0, pypdf 5.1.0. Suite green on new pins |
| 41 | P1 | `stripe` absent from requirements → billing silently in MOCK mode in prod (checkout returns `mock-stripe.example.com`, webhooks ignored) | `api/billing.py` | 🟡 partial — `stripe` pinned; ERROR logged if key set but package missing. `require_active_subscription` still has zero call sites (product decision) |
| 42 | P2 | QBO webhooks could never validate: verifier read from a Settings field that didn't exist; HMAC compared as hex while Intuit sends base64 | `api/webhooks.py`, `config.py` | ✅ fixed — field added; base64 compare, fail-closed. Tests: `TestQboWebhookSignature` |
| 43 | P2 | Following the code's own advice (add `INTUIT_WEBHOOK_VERIFIER_TOKEN` to `.env`) crashed startup — pydantic `extra=forbid` | `config.py` | ✅ fixed — `extra = "ignore"`. Test: `TestSettingsRobustness` |
| 44 | P2 | Chat history loaded by client-supplied `conversation_id` only → guessed UUID pulled another tenant's history into this user's LLM context (and `_save_turn` appended to it) | `api/chat.py` | ✅ fixed — `company_id` predicate. Test: `TestChatHistoryScope` |
| 45 | P2 | `/api/connectors/task-result` updated `connector_tasks` by id only (cross-tenant overwrite; body `company_id` check was against attacker input) | `api/connectors.py` | ✅ fixed — scoped update, 404 on miss. Test: `TestTaskResultTenantScope` |
| 46 | P2 | `/api/docs/openapi.json` + ReDoc UI unauthenticated, bypassing the X-Docs-Key gate; key compared non-constant-time | `api/docs.py`, `main.py` | ✅ fixed — same gate; `hmac.compare_digest`. Tests: `TestDocsGating`, `TestDocsKeyCompare` |
| 47 | P2 | `/auth/refresh` re-signed role/company from the OLD token → demoted user keeps privileges up to 7 days | `api/auth.py` | ✅ fixed — reloaded from `users`. Test: `TestRefreshHardening` |
| 48 | P1 | Celery idempotency lock released BEFORE the work in **13** scheduled tasks (`with task_lock() as acquired: if not acquired: return` — body outside the block) → concurrent workers double-process files / CU jobs / token refreshes | `tasks/*.py` | ✅ fixed — `@locked_task` decorator holds the lock for the whole run; guard test scans every task module for the old pattern. Tests: `TestLockedTask` |
| 49 | P2 | `TenantMiddleware` decoded with PyJWT (undeclared transitive dep) behind a bare `except` that would also hide its absence | `middleware/tenant.py` | ✅ fixed — app's python-jose decoder. Test: `TestTenantMiddleware` |
| 50 | P2 | Docker: Playwright Chromium installed to `/root/.cache` before `USER appuser` (unreadable → headless workflows fail; `\|\| true` hid the error); Redis published on `0.0.0.0:6379` (dev); Flower unauthenticated (dev+prod); prod API published on :8000 beside nginx; no `HEALTHCHECK` | `Dockerfile`, `docker-compose*.yml` | ✅ fixed — `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` + chown + visible warning; Redis loopback-only; `FLOWER_BASIC_AUTH` required; prod API `expose` only; HEALTHCHECK |
| 51 | P1 | Web-frontend CI gate red on every push: no ESLint config file, `eslint` undeclared (transitive v10), `--ext` invalid under flat config; desktop lint script had the same flag | `frontend/`, `desktop/package.json` | ✅ fixed — `eslint.config.js` (mirrors desktop), `eslint` + `eslint-plugin-react-hooks` declared, scripts fixed. Frontend 0 errors / desktop 0 errors |
| 52 | P1 | Release workflow's `publish-update-manifest` job broken three ways (macOS updater pointed at `.dmg` — Tauri v2 needs `.app.tar.gz`; wrong `.sig` names; read a DRAFT release by tag → 404 → red on every tag) and overwrote tauri-action's correct `latest.json` | `release-desktop.yml`, `tauri.conf.json` | ✅ fixed — job removed; `includeUpdaterJson`/`updaterJsonPreferNsis`; dead v1 `updater.dialog` key dropped; honest release notes (no phantom CHANGELOG, no "connector bundled" claim) |

**Open after Pass 3** (verified, not fixed here):

| # | Sev | Finding | Evidence | Next step |
|---|---|---|---|---|
| 53 | P1 | Computer-Use sessions live in a per-process dict while the image runs `uvicorn --workers 2` → poll/approve/reject land on a different worker and 404; same class in `auth.py` OAuth-state fallback | `api/computer_use.py`, `Dockerfile` | Move session state + approval signal to Redis (or pin CU to one worker) |
| 54 | P2 | Token revocation, refresh single-use rotation and rate limiting all fail **open** on a Redis outage (logged-out tokens keep working; refresh tokens replayable) | `auth/jwt.py`, `middleware/rate_limit.py` | Policy decision: fail closed (503) on auth-critical paths, or alert loudly |
| 55 | P2 | Connector install secret stored in plaintext although the docstring says "hashed" | `api/connectors.py` | Store `sha256(secret)`, compare digests (storage-format migration) |
| 56 | P2 | Write task types (`create_invoice`/`create_po`/`update_item`) dispatched via `/dispatch-task` bypass `PolicyEngine` / the approval queue that `connector_protocol.py` says they require | `api/connectors.py` | Route write types through the approval queue |
| 57 | P3 | A few handlers echo raw exception text in error details (`agent.py`, `chat.py`, `dashboard.py`, `admin.py` health payload; `ValueError` handler returns `str(exc)`) | various | Generic client messages; log detail server-side |
| 58 | **P0 (deployment, not code)** | `api.secretaryai.com` / `app.secretaryai.com` resolve to domain-parking addresses — no backend appears to be deployed there, yet the desktop build bakes that URL in by default. A perfect installer still cannot sign in | DNS + `release-desktop.yml` defaults | Deploy the backend (`RAILWAY_DEPLOYMENT.md`), set the `API_URL` repo variable, then build |

Backend suite after Pass 3: **1086 passed / 4 skipped** (1045 + 41 new). Frontend lint 0 errors, typecheck/build/vitest green; desktop build/typecheck/vitest green; mobile typecheck green; Rust fmt/clippy/test green; version-consistency script green.

### Pre-existing test failures (not caused by this work)
- `tests/test_wholesale_distribution/test_logistics/test_vendor_tracker.py::test_extracts_ship_date_natural_language` — date-sensitive: the fixture's absolute ship date (`April 20, 2026`) is now in the past and is filtered out. P2; fix by freezing time in the test or accepting past dates. Confirmed failing on the baseline without any of this pass's changes. *(Resolved since: de-flaked with a relative date in `57716d1`.)*

---

## External blockers (owner action required — will NOT be faked)

| Blocker | Gate blocked | Owner action |
|---|---|---|
| Windows Authenticode code-signing certificate (trusted CA) | G3 | Purchase/provision; add as CI secrets |
| Apple Developer ID cert + notarization credentials | G4 | Enroll in Apple Developer Program; add secrets |
| Tauri updater signing private key | Updater | Generate; store as protected CI secret |
| Clean Windows 11 + macOS (ARM & Intel) test machines/VMs | G3/G4 | Provide access for install smoke tests |
| Production/sandbox vendor accounts (QuickBooks Online, Conductor/QB Desktop, Gmail, SendGrid) + prod secrets | G2/G5 | Provide sandbox credentials |
| **A deployed backend** — `api.secretaryai.com` currently resolves to domain parking (#58) | G2 (everything user-facing) | Deploy per `RAILWAY_DEPLOYMENT.md`; set the `API_URL` repository variable to its `https://` URL before running the desktop release |
| GitHub Actions enabled on the repo (the "Release Desktop App" workflow was not appearing in the Actions tab) | G3/G4 | Settings → Actions → General → allow actions; then run the workflow manually and publish the draft it creates |

---

## Change log
- **Pass 1 (security, backend):** fixed #19, #20, #21, #22; single-sourced backend version (#24 partial). Added `tests/test_security_hardening.py` (8 tests, all passing). Full backend suite: 1013 passed / 4 skipped / 1 pre-existing date-flake.
- **Pass 3 (end-to-end audit, Sep 2026):** fixed #36–#52 (one P0 auth bypass, six P1s, ten P2s) across backend, CI, Docker/compose and the release workflow; #41 partial. Added `tests/test_security_pass3.py` (41 tests). Opened #53–#58; #58 is the deployment blocker that makes the installer unusable regardless of code. Backend suite: 1086 passed / 4 skipped.
