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
| G0 | Repository integrity | 🟡 in progress | Cargo.lock ignored (#16); overlapping release workflows (#13); no single version source (#24, partially fixed backend). |
| G1 | Security | 🟡 in progress | Backend: #19/#20/#21/#22 **fixed + tested**. Remaining: tenant-isolation negative tests, Tauri least-privilege (#8/#9), local-cache encryption (#7), secret-history scan. |
| G2 | Functional product | 🔴 not ready | Desktop auth/API wiring broken (#2/#3/#4/#5/#6); needs real integrations (external accounts). |
| G3 | Windows installer | 🔴 blocked | Builds & installs (verified), but **unsigned** (#12) — needs Authenticode cert. |
| G4 | macOS installer | 🔴 blocked | Unsigned/un-notarized (#12) — needs Apple Developer ID + notarization. |
| G5 | Operations | 🔴 not ready | Backups/restore drill, runbooks, migration rollback not yet proven. |
| G6 | Release evidence | 🔴 not ready | Depends on G0–G5. |

Legend: 🟢 pass · 🟡 in progress · 🔴 not ready/blocked

---

## Category scorecard (1 = absent, 5 = production-grade)

| Category | Score | Trend |
|---|---|---|
| Authentication & session security | 2 → 3 | ↑ refresh rotation/revocation/bounded-window added (#19) |
| Tenant isolation & authorization | 2 | — negative tests pending |
| Secret storage | 2 | — desktop token in localStorage (#5) |
| Data encryption & privacy | 2 | — local cache plaintext (#7); Sentry scrubbing fixed (#22) |
| AI / tool safety | ? | — not yet audited this pass |
| Backend reliability | 3 | health leak fixed (#20) |
| Web reliability | 3 | — |
| Windows desktop reliability | 3 | installs + launches (crash-on-start fixed earlier) |
| macOS desktop reliability | 3 | builds; unsigned |
| Packaging / signing / updater | 1 | unsigned; updater artifacts off (#11) |
| CI & supply chain | 1 | no PR CI/CodeQL/Dependabot (#17); floating actions (#23) |
| Observability / recovery / ops | 2 | — |
| Documentation & support readiness | 2 | this doc + register started |

---

## Defect register (Phase 0 revalidation — all CONFIRMED at baseline)

Severity: P0 blocker · P1 high · P2 medium. Status: ✅ fixed (tested) · 🟡 in progress · ⬜ open.

| # | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| 19 | P0 | `/auth/refresh` re-signs an expired token indefinitely; no rotation/revocation check | `app/api/auth.py` refresh | ✅ fixed — revocation enforced, renewal bounded to `refresh_token_expire_days`, single-use rotation. Tests: `tests/test_security_hardening.py::TestRefreshHardening` |
| 21 | P1 | `/openapi.json` publicly served in production | `app/main.py` FastAPI ctor | ✅ fixed — `openapi_url` disabled in prod; key-gated route. Test: `TestPublicSurface::test_openapi_gated_in_production` |
| 20 | P1 | `/health` returns raw dependency exception text | `app/main.py` health_check | ✅ fixed — logs detail server-side, returns generic `ok`/`error`. Test: `TestPublicSurface::test_health_never_leaks_exception_text` |
| 22 | P1 | Sentry scrubbing only 3 local vars; headers/cookies/body/PII leak | `app/main.py` `_scrub_sentry_event` | ✅ fixed — `send_default_pii=False`; scrub all frames + request headers/cookies/body + user PII. Tests: `TestSentryScrub` |
| 24 | P2 | Version `1.0.0` duplicated across manifests + hardcoded in health | multiple | 🟡 backend now single-sourced (`app.__version__`); desktop/frontend/tauri still separate |
| 2 | P0 | CI injects `VITE_API_BASE_URL`; code reads `VITE_API_URL` → localhost fallback | `release-desktop.yml` vs `App.tsx`/`api.ts` | ✅ fixed — CI now injects `VITE_API_URL`; single `config.ts`; **fail-closed build guard** in `vite.config.ts` (verified: build fails on missing/localhost, passes on https). |
| 3 | P0 | `Setup.tsx` hardcodes `http://localhost:8000` | `desktop/src/pages/Setup.tsx` | ✅ fixed — routed through `config.ts`. |
| 4 | P1 | Desktop reads token from parent localStorage + iframes remote prod app | `desktop/src/App.tsx` | 🟡 in progress — decision recorded (ADR 0001: **bundle natively**); step 1 done; token-vault bridge + iframe removal tracked. |
| 5 | P0 | Sync loop reads token from OS vault; frontend writes localStorage; nothing bridges | `sync.rs` vs `api.ts` | ⬜ open |
| 6 | P1 | CSP has no `frame-src` for the app iframe (`default-src 'self'`) | `tauri.conf.json` | ⬜ open |
| 7 | P1 | Local SQLite cache provisions an encryption key but stores plaintext | `db.rs` | ⬜ open (SQLCipher or drop cache) |
| 8 | P1 | `devtools` feature enabled in release builds | `Cargo.toml` | ✅ fixed — removed `devtools` feature (inspector now debug-only). Desktop `cargo check` clean. |
| 9 | P1 | Tauri default capability grants broad http/fs/shell/etc. | `capabilities/default.json` | ✅ fixed — reduced to `core:default` + `shell:allow-open` (the only plugin the webview uses). Compiles clean. |
| 27 | **P0** | Cross-tenant draft approval/execution IDOR — `drafts` mutated by `id` only; a Company-A owner could approve/execute Company B's action (send its email/PO) | `actions.py`, `approval_queue.py` (found in tenant audit) | ✅ fixed — endpoint fetch + queue updates scoped to `company_id`, 404 on cross-tenant. Tests: `tests/test_tenant_isolation.py` |
| 28 | **P0** | Cross-tenant workflow cancel IDOR — `workflow_runs` failed/cancelled by `id` only | `workflows.py`, `engine.py` (tenant audit) | ✅ fixed — `_get_run`/`fail`/`resume_after_approval` scoped to `company_id`; endpoint ownership 404. Test in `test_tenant_isolation.py` |
| 29 | P2 | `accounts.py get_account` lacks a role gate; `computer_use` start uses a broken `require_permission` call | tenant audit | ⬜ open (tracked) |
| 10 | P2 | `capture_screen` unreachable — activation command unregistered | `lib.rs`/`screen_capture.rs` | ⬜ open |
| 11 | P1 | `createUpdaterArtifacts:false` while updater configured | `tauri.conf.json` | ⬜ open |
| 12 | P0 | macOS/Windows signing identity null — unsigned installers | `tauri.conf.json` | 🔒 external blocker (certs) |
| 13 | P1 | Overlapping release workflows disagree (signing/draft/updater/names) | `.github/workflows/*` | ⬜ open (consolidate) |
| 14 | P2 | `install.sh`/`install.ps1` point to wrong repo; install Docker stack | install scripts | ⬜ open |
| 15 | P2 | `install.ps1` uses `Invoke-Expression`; docs pipe remote script to shell | `install.ps1` | ⬜ open |
| 16 | P1 | `.gitignore` ignores `Cargo.lock`/`*.lock` | `.gitignore` | ✅ fixed — `Cargo.lock` un-ignored and committed (6685 deps pinned). |
| 17 | P1 | No PR CI, CodeQL, or Dependabot | `.github/` | ✅ fixed — added `ci.yml` (backend pytest, web/desktop lint+typecheck+build, rust fmt/clippy/test, dep-audit), `codeql.yml` (python + js/ts), `dependabot.yml` (6 ecosystems). Clippy `-D warnings` + audit enforcement tracked. |
| 18 | P2 | No `test` script in frontend/desktop package.json | package.json | ⬜ open |
| 23 | P2 | GitHub Actions use floating versions incl. `@master` | workflows | ⬜ open (pin SHAs) |
| 25 | P2 | Desktop heartbeat runs with empty company ID + no auth token | `heartbeat.rs`/`lib.rs` | ⬜ open |
| 26 | P1 | README overclaims one-click/native vs unsigned reality | `README.md` | ⬜ open |
| 1 | P2 | Default branch is a non-conventional agent branch | repo settings | 🔒 owner decision |

### Pre-existing test failures (not caused by this work)
- `tests/test_wholesale_distribution/test_logistics/test_vendor_tracker.py::test_extracts_ship_date_natural_language` — date-sensitive: the fixture's absolute ship date (`April 20, 2026`) is now in the past and is filtered out. P2; fix by freezing time in the test or accepting past dates. Confirmed failing on the baseline without any of this pass's changes.

---

## External blockers (owner action required — will NOT be faked)

| Blocker | Gate blocked | Owner action |
|---|---|---|
| Windows Authenticode code-signing certificate (trusted CA) | G3 | Purchase/provision; add as CI secrets |
| Apple Developer ID cert + notarization credentials | G4 | Enroll in Apple Developer Program; add secrets |
| Tauri updater signing private key | Updater | Generate; store as protected CI secret |
| Clean Windows 11 + macOS (ARM & Intel) test machines/VMs | G3/G4 | Provide access for install smoke tests |
| Production/sandbox vendor accounts (QuickBooks Online, Conductor/QB Desktop, Gmail, SendGrid) + prod secrets | G2/G5 | Provide sandbox credentials |

---

## Change log
- **Pass 1 (security, backend):** fixed #19, #20, #21, #22; single-sourced backend version (#24 partial). Added `tests/test_security_hardening.py` (8 tests, all passing). Full backend suite: 1013 passed / 4 skipped / 1 pre-existing date-flake.
