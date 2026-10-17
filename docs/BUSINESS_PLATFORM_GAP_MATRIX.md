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
