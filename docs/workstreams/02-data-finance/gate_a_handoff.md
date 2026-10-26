# Workstream 02 — Gate A contract and compatibility handoff

Audited baseline: `d79284a40db6e069aa3b39cb00376c95060c7983`.
Code findings only; no live provider or customer data was accessed.

## Chat 1: scheduler compatibility correction (not edited here)

| `backend/app/scheduler/overnight_scan.py` | Missing method | Available alternative | Data type |
| --- | --- | --- | --- |
| line 25 | `adapter.get_customers()` | `adapter.get_all_customers()` | `list[Customer]` |
| line 28 | `adapter.get_invoices(from, to)` | `adapter.get_orders_last_n_days(365)` | `list[Invoice]`; relative date range, not arbitrary dates |
| line 63 | `adapter.get_inventory()` | `adapter.get_inventory_qb()` | `list[InventoryItem]` |

Do **not** substitute `get_inventory_merged()` at line 63: it returns dicts,
not `InventoryItem` objects. The scan uses `customer.id`, `customer.qb_id`,
`invoice.customer_id` and `invoice.line_items[].item_id`. These identifiers
are provider-scoped, not globally tenant-unique. The velocity helper at
lines 94–107 also depends on invoice `item_id` and quantity; QBO normalization
is improved on Chat 2's branch, while QB Desktop lines require vendor fixtures.

All other scan/dashboard/task consumers found by call-graph search use the
actual public adapter names: `get_all_customers()`, `get_orders_last_n_days()`,
`get_inventory_merged()` or `get_inventory_qb()`. Existing scheduler tests
primarily use mocks and miss the real-class method mismatch. Please implement
and test the correction on Chat 1's branch, not this one.

## Proposed normalized contracts (Chat 1 owns shared schemas/migrations)

| Record | Minimum normalized fields | Deduplication identity |
| --- | --- | --- |
| Customer | names, contact channels, status, addresses | tenant/source/external customer ID |
| Account | customer link, territory, verified assigned rep, effective dates | tenant/canonical account ID |
| Invoice | account, invoice number, issue/due dates, currency, gross/discount/tax/net, balance, status, credit/void link | tenant/source/external invoice ID/version |
| Order | customer, placed/shipped date, fulfillment status, currency | tenant/source/external order ID |
| SalesLine | order/invoice link, source item, SKU, UOM, signed quantity, price/tax/discount/line amount | tenant/source/parent/line ID |
| Payment | payer, date, amount, currency, status, payment-to-invoice allocations | tenant/source/external payment ID/allocation ID |
| ARBucket | as-of date, not-due/1–30/31–60/61–90/90+ overdue balances, unapplied credits/exceptions | tenant/snapshot version/as-of |
| CommunicationEvent | mailbox/provider message ID, thread ID, direction, timestamp, sender/recipients, account match/evidence | tenant/mailbox/provider message ID |

Common envelope for each source record or batch: `tenant_id`,
`source_connection_id`, source external ID, `observed_at`, `ingested_at`,
source version/cursor, `consent_scope`, provenance/evidence pointer,
`quality_status` (fresh/stale/partial/missing/error/unverified), plus warnings.
Distinguish **verified zero**, **unknown null**, and **source not observed**.
Use decimal money plus original currency; any FX rate needs its source and
timestamp. Gross invoiced billings must not automatically count as recognized
revenue. Credit memos, returns, voids, payments and taxes must reconcile.

Requested API: `SourceSnapshot[T]` with `tenant_id`, `records`, `source`,
`fetched_at`, `completeness`, `cursor`, `warnings`, `errors`, `provenance`.
No connector/network error may be represented as a successful empty snapshot.
Ownership must be verified server-side before a sales rep sees an account.
Prove negative tenant and role cases under service-role DB access and RLS.
Require durable source-event deduplication, atomic checkpoints, and retry-safe
idempotency/auditing. Any outbound message or financial write needs separate
server-side approval and audit-before-dispatch.

A 13-week cash forecast requires verified starting cash, AR expected receipts,
AP obligations, scheduled payroll/tax outflows and historically calibrated
assumptions. Without sufficient sources, return unavailable with reasons;
never fill missing series with invented numbers or present bounds as facts.

## Other priority observations and ownership

| Owner | Evidence at baseline | Request |
| --- | --- | --- |
| Chat 1 | `backend/tasks/file_ingestion.py:12–17,42–53`: weak content fingerprint, only first 500 rows stored, database exceptions caught; `tasks/ingestion_scheduler.py:95–98` marks complete | Lossless row/provenance contract and propagate failed persistence |
| Chat 1/policy | `backend/app/api/dashboard.py:321–350`: direct email send guarded by a role permission, audit inserted **after** send, audit errors swallowed | Durable per-message approval, audit-before-send; fail closed |
| Chat 1 | `backend/app/api/deps.py:63–100` and `tasks/base.py:159–207`: token construction/refresh without standardized source health | Canonical freshness and token-failure envelope |
| Chat 2 | `backend/app/connectors/qb_online.py:161–172`: `BalanceWithJobs` labeled `total_sales` | Do not equate accounts receivable and sales; await canonical contract |
| Chat 2 | `backend/app/connectors/qb_desktop.py:91–114`: `totalBalance` labeled `total_sales`, raw unvalidated invoice lines | Obtain and validate Conductor fixtures before claiming parity |
| Chat 2 | `backend/app/connectors/unified_adapter.py`: warehouse failures can appear as zero quantities | Agree on data-quality envelope before changing response shape |
| Chat 2 | `backend/app/connectors/outlook.py`: no persistence callback wired from unified adapter; Microsoft OAuth onboarding not established in reviewed code | Consent and durable refresh/persistence tests |
| Chat 2 | `backend/app/auth/oauth.py:21–25`: Google consent scopes include write-level permissions | Read-only-first scopes and explicit feature consent |
| Chat 2 | `backend/app/intelligence/wholesale_distribution/causal_reasoning.py`: fixed recovery probabilities and counterfactuals | Label as heuristic hypotheses pending valid identification |

## Evidence required to greenlight source E2E

1. QBO **sandbox** with consent: prove base URL and realm, token rotation,
   pagination, invoices/credits/partials, and reconciliation; no QB writes.
2. QB Desktop with consent: vendor-backed Conductor local-agent transaction
   and cursor evidence, item identities, duplicate/retry behavior; read-only.
3. Gmail/Outlook: approved scopes, refresh and revocation behavior, immutable
   message/thread keys, partial failure signals, ambiguous account matching.
4. File/warehouse: full SHA-256, row counts, source version, missing-field
   checks, reconciliation without double-counting inventory.
5. Cross-tenant, sales-rep and outage negative tests; default-deny writes;
   human-approved draft actions only.

No migration, deployed SHA verification, production action or vendor-backed
acceptance occurred in Chat 2's first audit.

## Follow-up: source-independence test fixture results

The default `AccessRouter.route("inventory")` API path uses the same
QuickBooks provider as `get_inventory_qb()`
(`backend/app/router/access_router.py:247–275`). It is **not** a second
warehouse source. Otherwise its QB item `sku` can match but its native
`quantity_on_hand` field can be mistaken for a missing `quantity` field and
interpreted as warehouse zero. This was reproduced with deterministic
fixtures and corrected in Chat 2's adapter without changing router ownership.

Interim additive `get_inventory_merged()` entry keys:

- `source_quality=partial` until provider lineage and completeness are proven.
- `warehouse_source_status=observed|missing|stale|unavailable|not_independent`
  and `warehouse_qty_observed` disambiguate a verified zero from absent data.
- `warehouse_source_path` and `warehouse_fetched_at` retain route provenance.
- `weekly_sell_rate_observed` and `weekly_sell_rate_source_status` identify
  unobserved sales velocity. Legacy numeric zeros remain for compatibility
  and **must not** be displayed as verified zero without the observed flag.
- `warehouse_authoritative` fails closed on missing, stale, invalid, or
  non-independent warehouse data. QB-authoritative stock remains QB-only,
  without adding warehouse quantities to duplicate physical inventory.

**Chat 1 / Chat 3 request:** provide an independently scoped warehouse route
and a consumer-visible data-quality envelope before promoting a warehouse
measurement to verified; adopt the observed/status fields in consumers and
ensure any source-unavailable dashboard or stock insight is marked partial.
No new table or central schema is proposed by this interim compatibility fix.
