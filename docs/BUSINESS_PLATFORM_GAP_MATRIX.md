# Latest handoff — FastAPI/Starlette patched and paywall preserved

Verified candidate `35b42cdd`: backend **1,264 passed, 5 skipped** and
Phase 4 security mutation checks passed in GitHub CI 39091819323.
Python `pip-audit` reports **NO KNOWN VULNERABILITIES** on the resolved
requirements (0 in backend; previously 14 Starlette records). This includes
`fastapi==0.143.3` and `starlette==1.8.0` on Python 3.11.

## Why previous upgrade broke tests
FastAPI 0.137+ preserved included routers as a *tree*, rather than
flattening `app.routes`. SecretaryAI's subscription-test harness and
test-only automatic active-subscription fixture incorrectly walked
`app.routes` as if the router were flat. Effective subscription gates were
executing but the old harness failed to see/override them, leading to
incorrect "free router" assertions and unrelated 402 responses.

## Corrected and tested
- Subscription-test fixture and route classification now use the
  FastAPI-supported `iter_route_contexts(app.routes)` and effective
  dependency graph, without modifying actual app billing/authorization.
- All 13 paid groups remain structurally gated; the connector group
  remains mixed: user-facing connector calls are paywalled, machine-facing
  connector endpoints authenticate separately.
- Added explicit *real HTTP* tests: unpaid authenticated tenants get
  `402` on account/action endpoints; an active but underprivileged
  viewer still gets `403` on account details. Paid test fixtures are
  temporarily disabled during those test cases, then restored.
- The existing role checks, company isolation, approval idempotency,
  policy denial, session scopes, JTI revocation and security mutation
  tests remain enabled and passing.

## Release gates
- The **backend dependency security gate is clean**, but the overall
  Dependency audit still fails: full frontend **11 (6 high/5 moderate)**,
  desktop **20 (11 high/8 moderate/1 low)**. Frontend runtime-only
  high-severity audit is clean; do not translate that into a full clear.
- Real staging FastAPI/Starlette HTTP traffic, malware/file storage, QB
  integration, payments, rollback and independent audit remain unverified.
- Do not merge/deploy or apply migrations on customer data.
- Verify latest exact-tip GitHub CI and CodeQL separately before any
  release decision. PR #74 stays open and on its existing base branch.

---

## Security dependency remediation checkpoint — 2026-10-17/18

Existing SecretaryAI PR #74, no merge or deployment. The front-end
`frontend/package-lock.json` was updated by one branch-restricted
GitHub Actions remediation after a clean `npm ci`, lint, typecheck,
Vitest and production build. Only semver-compatible `npm audit fix`
changes were committed; `--force` was deliberately **not** used.

- Frontend full-graph advisory count reduced **21 -> 11**:
  **11 high/9 moderate/1 low -> 6 high/5 moderate** (from
  remediation run `39087191570`). The dependency audit is NOT CLEAN.
- Remaining findings involve dev-tool transitive `braces` /
  `chokidar` /`tailwindcss`, `esbuild`/`vite`, and
  `postcss-selector-parser`; a production `react-router-dom`
  chain also has moderate findings. The `braces` high-severity
  advisory `GHSA-vfj7-8cjw-p6xm` explicitly lists **no patched
  release** as of Oct 17, 2026. No speculative override or silent
  suppress/allowlist is authorized.
- CI runs **separate** runtime-only frontend, full frontend,
  desktop and backend audits, and reports a red audit job if any
  is not clean. Ordinary PR CI still permits coding progress;
  `release-desktop.yml` and `docker-publish.yml` now enforce
  **hard, fail-closed** package security gates before building or
  publishing distributable artifacts.
- Release remains blocked until each remaining issue is fixed by a
  compatible, tested upgrade or has a *specific, owner-approved,
  time-limited risk decision* with verified package/runtime exposure.
  This commit does NOT grant an exception or production-release signoff.
- Need final exact-tip CI/CodeQL and production-closure audit proof
  after all changes. The prior release candidates' green badges do
  not certify the latest dependency lockfile.


---

## V9 local antivirus + independent review gate (2026-10-17)

- New `CLAMAV_UNIX_SOCKET` backend setting defaults empty (**fail closed**).
  Configure a trusted, locally mounted Unix-domain `clamd` socket;
  the backend sends binary `zINSTREAM` chunks and recognizes only exact
  `stream: OK` or `stream: <signature> FOUND` verdicts. Timeouts, malformed
  replies, absent scanner, connection failure or stale stored bytes deny
  scan attestation. Never rely on remote plaintext ClamAV TCP.
- New `POST /api/logistics/evidence/{id}/scan` re-downloads a private
  quarantine object, re-hashes it, verifies its recorded length and MIME,
  and obtains a real scanner verdict (clean/infected). It records the
  signed-in actor and immutable scan metadata through V9 SQL.
- New `POST /api/logistics/evidence/{id}/approve-review` requires a
  separate active owner/manager, **different from the document submitter
  and scanner operator**, and a previously clean scan.
- New read-only `GET /api/logistics/evidence/{id}/malware-status` shows
  scan and approval status. Even an independently approved clean scan
  **does not create a public URL** or authorize viewing untrusted bytes;
  `safe_to_view=false`, `accounting_reconciled=false` and
  `landed_cost_verified=false` are maintained.
- `database/schema_v9_malware_review.sql` is the manual migration
  equivalent of two Supabase migration paths. Apply **one** schema path
  after V8 (never all copies); no live migration or deployment took place.
- `scripts/test_evidence_malware_review.sql` tests scanner verdict and
  approval RLS/separation/immutability in real PostgreSQL;
  `backend/tests/test_evidence_malware_review.py` tests actual ClamAV
  socket protocol, scanner errors, stored-byte re-verification and
  API-identity controls. Verify **latest exact-tip** CI and CodeQL.
- **Staging blockers:** actual ClamAV service socket and signatures updates,
  real Supabase private bucket, streaming preflight/object length limit,
  multiworker contention and timeout/performance tests, malformed PDF and
  image processing, quarantine lifecycle and malicious-file incident
  response, separate safe rendering/CDR, and external security approval.
- Frontend dependency audit remains red. No QuickBooks writes, supplier
  payment or receipt stock changes can occur through scanner routes.


---

## V8 quarantined file upload / integrity-only check (2026-10-17)

- A new private-storage path `POST /api/logistics/evidence/{id}/upload`
  streams a maximum 8 MiB, validates PDF/PNG/JPEG signatures against the
  claimed extension, compares actual SHA-256 bytes to the existing immutable
  claim, requires `purchasing-evidence-quarantine` to exist with
  `public=false`, and writes with `upsert=false`.
- The service stores a distinct append-only `purchasing_evidence_integrity_checks`
  record through `attest_purchasing_evidence_integrity`, with actor,
  observed length, MIME and digest. Its `malware_scan_status=not_scanned`,
  `safe_to_view=false`, `financial_review_status=not_reviewed` and
  `accounting_reconciled=false` are *non-negotiable* truthful statuses.
- Read-only `GET /api/logistics/evidence/{id}/integrity` returns the current
  metadata without a download link or permission to open quarantine files.
- `database/schema_v8_evidence_integrity.sql` and the two alternative
  Supabase migrations contain the same schema. Run **one** migration path,
  after V7, not all copies. Staging migration/dry-run, bucket creation,
  bucket privacy, object lifecycle/retention, proxy upload size limits and
  antivirus scanning are **not performed here**.
- If object upload succeeds but SQL attestation fails, an orphaned private
  object can remain. Do not overwrite on retry. A restricted recovery and
  forensic deletion process is required before production.
- Neither checksum matching nor file signature is a malware scan or proof
  that freight values/invoices are authentic. Files are never approved for
  viewing, payment, landed costs, or QuickBooks posting.
- New real Postgres test `scripts/test_evidence_integrity.sql` and
  backend tests `backend/tests/test_evidence_upload_integrity.py`.
  The latest exact-commit CI and CodeQL **must pass**, then real private
  bucket integration and security/performance testing remain necessary.


---

# SecretaryAI Business Operations Platform — gap-to-implementation matrix

**Audit date:** 2026-10-17  
**Branch:** `claude/blissful-noether-ik7p9l` / existing PR #74  
**Evidence standard:** Source inspection and commit history are not staging or customer acceptance. No enterprise expansion is certified by this document.

## Baseline and scope

Existing FastAPI API routers include authentication, accounts, actions, agent, chat, compliance, computer use, connectors, dashboard, inventory, logistics, workflows and billing. Industry intelligence, workflows and a nine-stage procurement API already exist. This must remain **one** SecretaryAI application using the existing QuickBooks/Conductor route. A separate local QB Desktop connector **must not** be bundled or activated.

At the starting SHA `f67cdc4e`, GitHub CI run `39004104669` reported **12 failed, 1,138 passed, 5 skipped**, while CodeQL run `39004104676` passed. The failing CI was not a sign-off. Subsequent code and test changes require fresh exact-SHA CI.

## Development continuation — immutable stock ledger (2026-10-17)

Prior tip `694d79b4` was confirmed **CI success** (backend/desktop/web/mobile/schema/secret scan) and **CodeQL success**. The pre-existing report-mode dependency-audit job still failed. This is **baseline evidence only**, not proof of later ledger commits.

The **new internal-only** migration `database/schema_v5_stock_ledger.sql` defines a tenant-scoped SKU master, warehouses, balances and an append-only movement ledger. `post_stock_movement` performs a database-atomic, event-idempotent update, rejects unauthorized actors, cross-tenant locations, changed replay payloads and negative stock. Stock is segregated into available, damaged and quarantine buckets. Only the server `service_role` may invoke the procedure; even that role cannot directly insert movement rows or edit balances. New `scripts/test_stock_ledger.sql` is wired into the live Postgres schema validation CI job and checks the 1,180 accepted / 20 damaged movement example, tenant-specific RLS, replay denial, immutable history, and withdrawal limits.

This does **not** establish approved purchase orders, real receipts, stock reservation/transfer atomics, a QuickBooks-ledger reconciliation, shipment receiving UX or staging validation. The older `/api/logistics/receipt/process` endpoint now explicitly returns `inventory_posted=false` and `accounting_reconciled=false`: it remains **analysis only**, not stock posting.

There are **three equivalent locations** for the V5 SQL: manual `database/schema_v5_stock_ledger.sql`, Supabase root migrations and backend Supabase migrations. Use **one** deployment path per database; they are not three sequential migrations. Nothing has been applied to staging or production in this session. Validate CI and a database backup/restore plan before authorizing staging migration.

---
 
## Next milestone — inventory provenance and reconciliation preview (2026-10-17)

- A new **read-only** `GET /api/logistics/reconciliation/stock/{sku}` compares
  explicitly mapped QuickBooks inventory with stock-ledger balance buckets
  for the current tenant, without adding overlapping source quantities.
- Missing or ambiguous QB item mappings, incomplete ledger pagination,
  malformed source quantities, and QuickBooks outages are flagged; **even
  matching numerical values remain `reconciled=false`** because the ledger
  lacks verified opening balances/location coverage and QB's sellable versus
  damaged scope may differ.
- No accounting write, supplier payment, ledger stock adjustment or hidden
  source override occurs. The earlier logistics receipt **analysis** no
  longer updates `procurement_cycles` to a misleading warehouse-received
  stage.
- `backend/tests/test_stock_reconciliation_preview.py` covers equal-value
  scope uncertainty, missing explicit mappings, connector failures, tenant
  scoping, page limits and analysis-only stage behavior.
- The older PR tip `c3139cd1` had **1,168 backend tests passing and
  successful V6 PostgreSQL acceptance**. Check this new **exact tip's**
  CI and CodeQL before promoting readiness; npm high-severity advisories
  remain a release gate.

**Remaining integration gate:** prove warehouse/QuickBooks location and unit
coverage with signed evidence and source timestamps, then reconcile
documented variances and accounting through Conductor. Without that proof,
do not call inventory or accounting fully reconciled.


---

## V7 procurement evidence — metadata-only implementation

Evidence claims for approved purchasing orders now record document type,
tenant-scoped object key, claimed SHA-256, actor, idempotency key, optional
claimed monetary amount and currency. Rows are immutable and readable only
within a tenant. Claims in distinct currencies remain separate and are
explicitly **unverified**. They do not reflect bytes actually uploaded,
document authenticity, posted AP invoices, certified landed cost, or an
accounting tie-out. Claims have no write authority over stock, accounting
or bank/payments. Exact-commit V7 schema and API tests must pass before
marking the implementation verified.

`GET /api/logistics/orders/{po_number}/evidence` and matching `POST`
are API-only; no warehouse operator document-upload UI is certified.
Production-grade blob upload, cryptographic verification, independent
acceptance, transport proof, costs, FX and QuickBooks reconciliation are
separate outstanding implementation tracks.

## V6 durable PO/warehouse receipt implementation (2026-10-17)

The migration and route implementation now cover PO drafts, independently
approved POs, independently approved inspected receipt drafts, case quantities,
atomic inventory posting, separate damaged/quarantined buckets, tenant isolation,
and idempotent replay. Raw `service_role` stock posting was revoked so this
receiving approval path cannot be bypassed through the low-level RPC.
`scripts/test_purchasing_receiving.sql` runs against real PostgreSQL in CI.
`backend/tests/test_purchasing_receiving_contract.py` checks authenticated
actor binding and fail-closed API behavior. Recheck **latest exact-SHA** CI
and CodeQL before considering this test-proven.

This does **not** prove external QuickBooks/WMS data exchange, supplier
approval, transport milestones, signed receiving evidence, user-interface
readiness, production performance, inventory reservations or GA certification.
The SQL records returned `accounting_reconciled=false` intentionally.

## Implementation matrix

Status vocabulary: **Existing / incomplete**, **Partially implemented**, **Not identified in audited endpoints**, **Blocked externally**. None indicates production verification.

| Priority | Domain | Observed implementation | Missing release evidence or architecture | Required validation |
|---|---|---|---|---|
| P0 | Core CI/security | JWT Redis fail-closed, Redis-backed CU approvals, subscription gates, connector write gate, CodeQL and mutation tests | Fresh green CI, real two-worker Redis approval, Redis revocation persistence after restart, production alarm | Exact-SHA CI/CodeQL, two-worker Redis, forced outage/restart and mutation gates |
| P0 | Identity / tenancy | JWT, role/permission checks, row-level-security claims and isolation regression tests | End-to-end policy verification for every new record, job, file and AI tool; support/HR field controls | Company-A/B isolation matrix including denied routes and direct DB access |
| P0 | Inventory quantities | QuickBooks data and optional warehouse source merged for stock alerts | Durable SKU/location/lot movement ledger, reservations, reconciliation, verified source ownership | Concurrent receipt/reservation, overlap/no-double-count, replay and reboot |
| P0 | Procurement/receiving | Nine-stage logistics intelligence API, `procurement_cycles` best-effort stage summaries | Authoritative PO/PO-line records, approval chain, supplier confirmations, shipment/receipt events, immutable inventory postings, discrepancies, accounting sync | 1,200 ordered → 1,180 accepted, 20 damaged/missing with cost and QA reconciliation |
| P0 | Accounting actions | QuickBooks read/write adapters, draft/action policy engine, connector writes disabled | Cross-system idempotency and authorized approval/execution proof for each write surface | Duplicate/replay approval, settlement, and QB sandbox verification |
| P1 | Outbound fulfillment | Accounts/order and workflow API foundations | Quotation, order lines, inventory reservation, picks, packing, BOL, carrier/POD, backorders, returns | Order-to-payment full E2E with partial shipments and failure recovery |
| P1 | Forecasting | Moving-average forecasting, item endpoint and QB invoice source | Historical-data sufficiency, source freshness, configurable supplier lead time and stock commitments | Evidence-based forecast API and missing-data tests; separate true forecast validation |
| P1 | Integration health | Scheduled sync/reconciliation and connector routing | Reconciliation provenance/coverage, stale-source disclosure, alert routing and failure escalation | Real QB/WMS reconciliation, loss-of-source scenarios |
| P1 | HR | General users/RBAC available | Dedicated employee/personnel domain, onboarding/offboarding, scoped HR permissions, payroll-provider integration | HR cross-tenant, self-service denial, token revocation/offboarding E2E |
| P1 | CRM/sales | Accounts, sales analytics and action workflows | Pipeline/opportunity model, territory ownership, quotes, prices, commissions/returns | Lead-to-cash, role and territory isolation, approved commission calculation |
| P1 | AP/AR / executive reporting | QB-backed financial intelligence and dashboard basis | Traceable GL reconciliation, bill approval, cash forecasting, department budgets, role-scoped profitability | Tie out customer sample figures to QuickBooks and permitted source records |
| P2 | Service/campaign/product | Partial general agent, compliance and workflow primitives | Case ledger/SLA, campaign events/consent, product lifecycle and regulated SKU release controls | Department workflow E2E and approval/permission negatives |
| P0 GA | Infrastructure / commercial | Deployment/release workflows, signing scaffolding, policies drafted | Reachable production API, signed/public installers, live payment proof, independent assessment, DR and actual paging | Existing 15 GA gates in `docs/ROADMAP_TO_GA.md` and advertised-module gates |

## Phase 0 changes from this directive (implementation, not verification)

1. `backend/app/api/computer_use.py`: remove the Redis write signature collision (persisted `session_id` field vs helper argument).
2. `backend/tests/test_gap_implementations.py`: migrate obsolete process-global CU session/approval tests to Redis-backed API tests, including tenant denial.
3. `backend/tests/test_security_pass3.py`: enforce fail-closed connector write restrictions for owners while testing permitted protocol tasks.
4. `backend/tests/test_utils.py`: isolate ephemeral test Redis namespaces, keeping the production cross-process limiter and fail-closed posture.
5. `backend/app/api/inventory.py`: correct incompatible forecasting service calls and derive sales samples from actual QuickBooks invoices, explicitly disclosing missing data.
6. `backend/app/connectors/unified_adapter.py`: stop adding overlapping QB and warehouse stock by default; expose reconciliation discrepancies; require an explicit warehouse-authoritative policy to use that source.
7. `backend/app/scheduler/inventory_alert_check.py`: base reorder classifications on reconciled `total_qty`.
8. `backend/tests/test_inventory_forecast_contract.py`: add factual forecast, availability failure and quantity-ownership regression tests.

**All changes above are implemented in the branch but remain unverified until the latest complete CI run and deeper integration tests.**

## Sequenced implementation dependencies

1. **Stabilize Phase 0:** green backend suite, mutation tests, CodeQL, non-ignored security audits, Redis concurrency/outage/restart tests. No production deploy.
2. **Define stock authority first:** tenant-scoped SKU/warehouse/lot identities, a single ledger and unique external-event idempotency keys. Forbid inferred totals and untracked corrections. Provide consistency/reconciliation migration from existing inventory snapshots.
3. **Durable purchase-to-stock vertical slice:** purchase request → approved PO and immutable lines → supplier confirmation → shipment events → expected receipt → accepted/damaged receipt → exact-once stock ledger → landed cost and QB reconciliation. Add staging-backed acceptance test for 1,200/1,180/20.
4. **Outbound order-to-cash:** reservations and concurrency, invoices/payments sourced from accounting, returns and proof of delivery.
5. **Financial/HR expansion:** use accounting and payroll system-of-record connectors rather than rewriting their ledgers. Restrict HR to dedicated access and data policies.
6. **One AI interface across each domain:** read-only source-backed answers first, typed drafts next, policy-approved, idempotent actions last. Document freshness and provenance for each result.
7. **GA gates:** staging tenant isolation; signed desktop pilots; real vendor contracts, payment and restore drills; legal counsel and independent penetration test; 15 historical GA criteria plus new advertised-feature acceptance tests.

## Non-negotiable release blockers

- All exact-SHA CI jobs and CodeQL must pass; a green GitHub label does not prove runtime behavior.
- No customer data, real payments, production migrations or signed releases until explicit owner approval and prepared rollback.
- Redis revocation must **not** turn fail-open on restart or outage; a durable revocation mechanism or proven persisted Redis is required.
- Repeated receipt, approval and financial events cannot post twice.
- A numeric dashboard must indicate source and freshness, especially when warehouse/QB scope overlaps.
- An assistant must never represent in-memory recommendations or stage summaries as posted POs, booked freight, cleared customs or warehouse stock.
- All external vendor integrations and critical flows need real authorized staging evidence; legal and SOC 2 claims remain unverified.
