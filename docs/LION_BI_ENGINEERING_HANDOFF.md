# Lion BI (SecretaryAI) — Engineering Baseline & Live Handoff

**Last updated:** 2026-11-27 (Cycle 2 — Unified Release-Candidate Foundation)  
**Active branch:** `rc/lionbi-unified-foundation-20261127` (stacked on verified Draft PR `#233` `c589429d`)  
**Release Status:** **`NO-GO`** (Mandatory hosted disposable replay, live QBO sandbox reconciliation, and independent security signoff gates remain open)

---

## 1. Current Architecture & Deployed Provenance

### Application & Service Topology
- **Web Frontend (`frontend/`):** React + TypeScript + Vite 8 + Tailwind CSS v4 + React Router v7 (`VERSION=1.0.6`). Includes Executive Dashboard, Lola Home (approved orb + voice/text workspace), Finance Workspace, Accounts/CRM, Inventory, Warehouse, Logistics Control Tower, Pricing/Profitability, Compliance, Inbox, Connectors, and Settings/Team/Billing.
- **Desktop Client (`desktop/`):** Tauri 2 (Rust + React + Vite 8) for macOS (ARM/Intel) and Windows (`VERSION=1.0.6`). Stores session credentials in OS Keychain / Windows Credential Manager (`secretary-auth/token`) and encrypts cached SQLite `data_json` payloads with AES-256-GCM.
- **Mobile Client (`mobile/`):** Expo / React Native internal-distribution client (`VERSION=1.0.6`).
- **Cloud Backend (`backend/`):** Python 3.11 FastAPI (`fastapi==0.143.3`, `starlette==1.8.0`), `PyJWT==2.15.1` (HS256, fail-closed Redis JTI revocation and single-use refresh rotation), `PolicyEngine` + durable approval queue, deterministic financial/inventory services, and Anthropic Claude orchestration for Lola.
- **Background Workers:** Celery + Redis 7 (`celery_app` worker + single-instance `beat` scheduler / Railway read-only snapshot cron) protected by `@locked_task` distributed locks.
- **Database & Storage:** Supabase PostgreSQL 17 with Row-Level Security (`ENABLE ROW LEVEL SECURITY` across all `78` canonical public tables), append-only stock/purchasing/logistics/execution ledgers, and private quarantine storage + ClamAV Unix-socket (`zINSTREAM`) malware attestation.
- **Accounting & External Integrations:** QuickBooks Online (Intuit OAuth 2.0 REST), QuickBooks Desktop (via Conductor API), Gmail, Microsoft Outlook (delegated OAuth), WhatsApp Business (consent/ownership schema gated on Meta clearance), SendGrid, and Stripe (`BILLING_ENABLED` fail-closed startup guard).

### Live Environment Provenance (Verified Read-Only Evidence)
- **Canonical Manifest Version:** `1.0.6` across all 6 manifests (`scripts/check_versions.sh` PASS).
- **Default Git Branch (`origin/HEAD`):** `claude/secretaryai-tech-spec-lqBZj` (`79b05284`).
- **Historical Pass 4 Branch:** `integrate/pass4-staging-20261111` (`a6acd300`) — superseded by the November 14 staging integration candidate.
- **Deployed Railway Staging (`SecretaryAI-Staging`, `77513c77-44df-485e-a927-d57d6a80b2c1`):**
  - Commit: `65751b10ead773aacf3b1accac4c93b0638d97d8` (`integrate/lionbi-pricing-phase1-staging-candidate-20261114`) across Web, Backend, Redis, Dashboard Worker, and Snapshot Cron.
  - Note: Pre-existing uncommitted Railway staged environment patch (`patch:cf6ac083-7e09-4eff-a5a0-1a4d15a0096a`) must **not** be applied implicitly.
- **Shared Supabase Staging Parent (`xerktvdgjvjozvckjyfp`):**
  - `45` recorded rows in `supabase_migrations.schema_migrations` (`5` historical baseline rows `20240101000000`..`20261017010000` have `NULL`/empty `statements`), `78` canonical public tables (all RLS-enabled).
- **Isolated Finance UAT Branch (`toujmmvlgktprabjbccq`, `lionbi-finance-ar-isolated-uat-20261118`):**
  - `52` migration rows (includes 7 ad-hoc UAT recovery migrations; **not** a clean from-zero replay). Paired Railway project `LionBI-FinanceAR-Isolated-UAT` (`87c349cc-ba7a-4e94-a56e-bd2f4b4a00c6`) remains offline pending isolated secret provisioning.
- **Disposable Acceptance Preview (`ontqkqgofkghsdgtqwsm`):**
  - Reported `MIGRATIONS_FAILED` / `NotFound` on 2026-11-26 (`#120`). Replacement disposable branch requires explicit operator `$0.01344/hour` cost confirmation and immediate post-test deletion.

---

## 2. Active Branch Stacks & Release Candidate Topology

Four unmerged engineering stacks currently exist and **must be reconciled onto a single verified release-candidate integration branch** after independent review:

1. **Stack 1 — Finance AR Release Candidate & Canonical Migration Recovery (Active Checkout):**
   - Base: `integrate/lionbi-pricing-phase1-staging-candidate-20261114` (`65751b10`)
   - PR `#208` (`release-candidate/lionbi-finance-ar-staging-20261116`, `9d3bf47e`): Consolidated read-only Finance AR QBO cash-allocation preview, bounded Redis DNS/socket executors, realm binding, and exact-cent math (plus child PRs `#216`, `#219`, `#221`).
   - PR `#217` (`recovery/finance-canonical-46-local-replay-20261119`, `c2bcc4b9`): Reconstructs 46 source-verified canonical migrations matching shared-main's 7 MD5 catalog fingerprints; replays twice in ephemeral Supabase CLI v3 Docker CI.
   - PR `#233` (`recovery/disposable-hosted-replay-20261126`): Disposable-only atomic hosted replay generator, read-only TLS preflight, post-replay least-privilege & GoTrue Auth hook generator, and real PostgreSQL atomicity/rollback CI verification.
2. **Stack 2 — Dependency Zero-Vulnerability Remediation & Authenticated Tenant Acceptance:**
   - Base: `claude/secretaryai-tech-spec-lqBZj` (`79b05284` — **divergence warning:** branched off default branch rather than `65751b10`!)
   - PRs `#228` → `#229` → `#230` → `#231` (`1695ddf0`, mandatory `continue-on-error: false` dependency audit with **0 vulnerabilities** across backend, web, and desktop) → `#232` (`security/tenant-controls-20261126`, `46696ed2`, hardened `scripts/rls_authenticated_acceptance.mjs`, `rls_identity.mjs`, `rls_fixture_cleanup.mjs`).
3. **Stack 3 — Runtime Privilege Separation, Compliance Price-Posting (7 Migrations), & Erasure Review:**
   - Base: `integrate/lionbi-pricing-phase1-staging-candidate-20261114` (`65751b10`)
   - PRs `#197–#205` → `#209` → `#210` (`fix/lionbi-runtime-db-identity-proof-20261116`, `d5c0e915`): Atomic owner registration (`secretary_registrar`), `logistics_events` delete revocation, `secretary_runtime` capability audit (`backend/app/auth/runtime_role_audit.py`), 7 Compliance price-posting migrations (`20261115000000`..`20261115060000`), and non-executing company-erasure evidence validators.
4. **Stack 4 — Fast Migration Preflight & Default ACL Expansion Gate:**
   - Base: `65751b10` → PR `#218` (`51379d97`) → PRs `#220`, `#222–#226` (`aeea1bc4`) → PR `#227` (`23793ef7`, `pg_default_acl` privilege-expansion comparator).

---

## 3. Module Readiness Summary

### Completed in Source (Verified by Automated Unit / Local Postgres / Container CI)
- Core JWT HS256 auth, TOTP 2FA scope isolation, Redis fail-closed JTI revocation, and single-use refresh rotation.
- Cross-tenant IDOR protections on accounts, drafts, workflows, chat history, connectors, and computer-use sessions (`Phase 4` mutation suite).
- V5–V16 PostgreSQL schemas: immutable stock ledger (`available`/`damaged`/`quarantine`), four-eye PO & warehouse receiving (`1,200` ordered → `1,180` accepted / `20` damaged), quarantined receipt evidence SHA-256 + ClamAV protocol checks, durable agent execution claims (`secretary_executor`), and immutable approved email payloads.
- Pricing Phase 1 loaded-cost & price-book snapshots, Logistics Control Tower, Lola core/private employee memory schemas, and Stripe idempotent webhook/checkout safety RPCs.
- Canonical 46-migration reconstruction (`PR #217`) + atomic single-transaction disposable replay & least-privilege bootstrap (`PR #233`).

### Partially Implemented / Pending Integration or Live Provider Acceptance
- **Finance AR & QuickBooks Online (`PR #208`, `#219`, `#221`):** Read-only QBO cash-allocation preview implemented and unit-tested; live Intuit sandbox count/cent reconciliation and full operational AR collections workflow (promises-to-pay, disputes, dunning performance) remain pending.
- **Dependency Remediation (`PR #230–#231`):** Clean (`0` vulnerabilities) on `security/backend-audit-candidate-20261126`, but must be ported onto the `65751b10` / `PR #208` staging integration line where `ci.yml` still marks `dependency-audit` `continue-on-error: true`.
- **Database Privilege Separation (`PR #199–#210`):** Implemented in draft stack `#210` (`secretary_registrar`, `secretary_runtime`), not yet integrated or wired into live Railway startup (`DATABASE_URL` / `service_role` on shared staging still holds `DELETE` on `companies` and `logistics_events`).
- **Compliance Price Posting (`PR #197–#210`):** Seven `20261115*` migrations and UI/API panels exist on Stack 3, absent from shared staging `xerktvdgjvjozvckjyfp`.
- **Lola AI COO & Voice (`#117`, `#118`):** Grounded tool routing, orb UI, and bilingual scaffolding exist; systematic deterministic AI evaluation suite, proactive deduplicated operational alerts, and live audio/microphone E2E verification remain open.

### Missing / Externally Blocked
- Clean hosted disposable Supabase branch execution of `hosted_replay.py` + `hosted_post_replay_least_privilege.py` + `PR #232` `rls_authenticated_acceptance.mjs` (blocked on operator cost confirmation for a fresh disposable branch).
- Separately hosted, dual-control lawful company erasure executor with live legal-hold/retention verification and external immutable audit storage (`#206`; destructive execution remains intentionally disabled).
- Production WhatsApp Business Meta clearance (`#135`), Apple Developer ID & Windows Authenticode code-signing certificates (`G3`/`G4` desktop), published attorney-reviewed Terms/Privacy/License, live backup-restore drill, and production domain/hosting cutover.

---

## 4. Confirmed Technical & Security Findings Log

| ID | Sev | Component | Finding & Root Cause | Status |
|---|---|---|---|---|
| **DB-233-1** | **P0** | `hosted_replay.py` | **Fixed (2026-11-27):** Three canonical migrations (`20261102092256`, `20261102232409`, `20261103022426`) contain top-level `BEGIN;`/`COMMIT;`. Concatenating them verbatim inside `hosted_replay.py`'s outer `BEGIN; ... COMMIT;` prematurely committed the outer transaction at migration #34/46, dropping `SET LOCAL lock_timeout` and `LOCK TABLE`, running #37–#46 in autocommit mode, and preventing rollback on failure. Fixed via lexer-aware `prepare_transactional_sql()` + runtime `pg_current_xact_id()` continuity guard while preserving exact unmodified canonical SQL in `schema_migrations.statements`. | **FIXED in PR #233** |
| **DB-233-2** | **P1** | `hosted_replay.py` | **Fixed (2026-11-27):** Unconditional `ON CONFLICT(version) DO UPDATE` across all 46 rows could mask unexpected ledger collisions. Replaced with guarded `ROW_COUNT = 1` `UPDATE` for the 5 empty baseline rows and strict `INSERT` (no `ON CONFLICT`) for the remaining 41 versions + byte-length provenance verification. | **FIXED in PR #233** |
| **DB-233-3** | **P1** | `hosted_preflight.py` | **Fixed (2026-11-27):** Hardcoded defunct ref `ontqkqgofkghsdgtqwsm`, failed to find `psql` outside `/bin:/usr/bin` (`os.defpath`), and ran a passive `SELECT` that exited `0` even on non-empty databases. Added `EXPECTED_DISPOSABLE_PROJECT_REF` validation with hardcoded `FORBIDDEN_PROJECT_REFS` denylist, `shutil.which("psql")` resolution prior to `PG*` env isolation, and fail-closed PL/pgSQL assertions. | **FIXED in PR #233** |
| **SEC-215-1** | **P0** | Clean Branch Grants & Auth Hook (`#215`, `#227`, `#232`) | **Mitigated for Disposable Acceptance (2026-11-27):** Clean canonical replay lacked explicit table grants on V1–V2 tables, inherited 30 client-role `TRUNCATE` grants from default ACLs, and lacked a GoTrue Custom Access Token Hook to populate top-level `auth.jwt() ->> 'company_id'` without client `user_metadata` spoofing. Added `hosted_post_replay_least_privilege.py` (`finance-uat` and `rls-acceptance` modes) + `secretary_internal.custom_access_token_hook(jsonb)` while preserving all 7 `public` catalog fingerprints. Hosted disposable execution pending branch provisioning. | **READY FOR HOSTED REPLAY** |
| **INT-001** | **P0** | Branch Stack Divergence | **Unified on RC Candidate (2026-11-27):** Integrated Stack 1 (`#208/#217/#233`), `#219` + `#221` Finance AR preflight/count guards, Stack 2 RLS harness (`#228–#232` `scripts/rls_*` + fail-closed `ci.yml` `dependency-audit`), Stack 3 (`#199–#210` `secretary_registrar`, `secretary_runtime`, `logistics_events` delete revoke, 7 `20261115*` Compliance migrations, erasure validators), and Stack 4 (`#218–#227` fast hygiene + default ACL gate) onto `rc/lionbi-unified-foundation-20261127` with 54-migration CLI v3 parity replay. | **UNIFIED ON RC BRANCH** |
| **SEC-206-1** | **P0** | Shared Staging `service_role` | Shared staging `xerktvdgjvjozvckjyfp` still grants `DELETE` on `public.companies` (`ON DELETE CASCADE`) and `public.logistics_events` to `service_role`, and Railway backend still uses shared privileged credentials. | **OPEN (`#206` / `#210`)** |

---

## 5. Production Release Gate Matrix (`G0`–`G6`)

| Gate | Scope | Status | Blocking Evidence Required |
|---|---|---|---|
| **G0 — Stability** | Infrastructure, worker/beat, version & SHA parity | **BLOCKED** | Consolidate Stacks 1–4 onto one candidate; verify deployed staging SHA parity and snapshot/beat health. |
| **G1 — Integrations** | QuickBooks Online, Conductor, Outlook, Gmail, WhatsApp | **BLOCKED** | Live QBO sandbox read-only count/cent reconciliation (`#208`); Outlook/Gmail staging OAuth E2E; Meta clearance (`#135`). |
| **G2 — Workflows** | AR/AP, CRM, Inventory, Warehouse, Logistics, Pricing, Compliance | **BLOCKED** | Reconcile 7 Compliance migrations (`20261115*`) onto canonical 46 baseline → 53-migration isolated replay; complete operational AR collections & E2E workflow suite. |
| **G3 — Lola AI** | Grounded COO intelligence, proactive alerts, orb/voice, EN/ES | **BLOCKED** | Deterministic Lola evaluation suite (exact financial agreement, attribution, prompt-injection resistance) + live voice/mic verification. |
| **G4 — Security** | Tenant isolation, least privilege, revocation, 0 dependency vulns | **BLOCKED** | Provision authorized disposable Supabase branch, run `hosted_replay.py` + `hosted_post_replay_least_privilege.py` + `PR #232` real-Auth acceptance, port `#231` zero-vuln lockfiles & `#210` `secretary_registrar`/`secretary_runtime`, obtain independent security review. |
| **G5 — Launch Ops** | Atomic onboarding, Stripe billing, observability, backup/restore | **BLOCKED** | Execute real backup-and-restore drill (`docs/BACKUP_RESTORE.md`), Redis restart revocation-survival proof, and alerting drill. |
| **G6 — Final Release** | Exact-head CI/CodeQL, signed installers, legal docs, GO signoff | **NO-GO** | Apple/Windows signing certs, counsel-approved ToS/Privacy/LICENSE, green exact-head mandatory audits, and owner promotion approval. |

---

## 6. Ordered Next Engineering Actions

1. **Verify Exact-Head CI & Local Postgres Replay on PR `#233`:** Confirm `Finance canonical 46-migration replay (local only)`, `Disposable replay regression`, `CI`, and `CodeQL` pass on the hardened `hosted_replay.py` + `hosted_post_replay_least_privilege.py` commit.
2. **Unify Security Stack 2 (`#228–#232`) with Staging/Finance Candidate (`65751b10` / `#208`):** Port the vetted zero-vulnerability `backend/requirements.txt`, `frontend/package-lock.json`, `desktop/package-lock.json`, mandatory `ci.yml` dependency-audit enforcement (`continue-on-error: false`), and `scripts/rls_authenticated_acceptance.mjs` onto an isolated integration branch stacked cleanly on the canonical staging candidate.
3. **Integrate Runtime Privilege Separation & Compliance Migrations (`#199–#210`):** Validate the 7 additive `20261115*` Compliance migrations and `secretary_registrar` / `secretary_runtime` SQL migrations on top of the 46-migration canonical baseline in local Supabase CLI v3 replay.
4. **Execute Hosted Disposable Replay & Real-Auth Tenant Acceptance (Requires Operator Cost Approval):** Upon explicit operator confirmation for a fresh `with_data=false` Supabase preview branch (`$0.01344/hr`), run `hosted_preflight.py` → `hosted_replay.py` → `hosted_post_replay_least_privilege.py` → `rls_authenticated_acceptance.mjs`, record evidence in `#211`/`#215`/`#120`, and immediately delete the preview branch.
5. **Build Deterministic Lola AI Evaluation & Operational AR Completion:** Implement the repeatable offline Lola financial/cross-department evaluation harness (Section 23) and complete remaining AR collection workflow gaps without external AI spend.
