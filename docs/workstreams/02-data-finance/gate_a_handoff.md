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

## Follow-up: proactive-insight source checks (Milestone 3)

`backend/tasks/proactive_insights.py` (Chat 1-owned) sends `Customer` and
`Invoice` dataclasses plus `get_inventory_merged()` records to
`app.intelligence.opportunity_detector.detect_opportunities()` (Chat 2-owned).
Deterministic tests reproduced crashes when an invoice amount was a valid zero
and when mixed string/date values were compared. Some missing/invalid balances
were silently interpreted as no AR exposure. These owned detector paths now
normalize dates and record access, validate overdue balances, and fail closed
on malformed identity/date/money input rather than fabricate verified zero.

For potential low-stock purchase-order drafts, the detector now evaluates the
**authoritative** `total_qty` exactly once (never `qb_qty + warehouse_qty`),
and requires all of:

- Known `quantity_source` (`quickbooks` or verified `warehouse`) whose matching
  observed quantity equals `total_qty` and is a valid whole unit.
- `weekly_sell_rate_observed=True`, status `observed`, and finite positive rate.
- `open_po_qty_observed=True`, status `observed`, and confirmed `open_po_qty=0`.
- Nonempty source item ID and product identity.

**Chat 1 / Chat 3 dependency:** `get_inventory_merged()` does *not* currently
populate open-PO quantity or a verified source status for each SKU. Therefore
these safety checks intentionally suppress speculative purchase-order drafts
until the order/PO reconciliation owner supplies a read-only open-PO observation
with SKU/UOM identity, freshness and source provenance. Do not remove this
gate or infer `open_po_qty=0` from an absent field. The existing action label
remains draft-only, but human approval and role/tenant enforcement belong in
the shared actions API. No feed table schema or scheduler code was changed.

## Follow-up: provider page and invoice integrity (Milestone 4)

Read-only synthetic tests reproduced five categories of unsafe connector
behavior before fixes: missing QBO `QueryResponse` interpreted as a clean
empty result; duplicated full QBO pages; missing Conductor `data` interpreted
as no invoices; Conductor `hasMore=True` with a missing/repeated `nextCursor`
looping indefinitely; and absent Desktop invoice financial fields silently
replaced by today's date or zero. The owned QBO/Desktop connector paths now
require explicit response shapes, validate entity IDs and page deduplication,
detect non-progressing cursors, and reject missing/non-finite/negative Desktop
invoice totals or balances while preserving a genuine zero. Valid empty pages
remain supported when explicitly returned by the provider contract.
Both inventory-item normalizers also require a finite observed quantity: an
absent `QtyOnHand`/`quantityOnHand` may no longer imply verified zero stock.
The Desktop paginator rejects empty `hasMore=True` pages instead of waiting
indefinitely for evidence that never progresses.

**Chat 1 contract request:** a source-batch result must distinguish
`completed_empty` from `failed_or_incomplete`; no dashboard, scheduler or
cashflow projection may convert either a malformed provider response or a
stalled cursor to verified zero activity. No shared source-result schema was
modified in Chat 2.

**Provider-backed validation still required:** obtain consented QBO sandbox
multi-page query, zero-result and OAuth rotation evidence; obtain consented
Conductor pagination fields (`data`, `hasMore`, `nextCursor`) and Desktop
invoice field semantics from real vendor responses. These tests prove
deterministic contracts only, not live provider compatibility. Neither
QuickBooks adapter implements read-only payment history/allocation retrieval,
so a verified customer AR roll-forward (`opening + invoices - credits -
payments = closing`) is blocked. No payment estimate or 13-week cashflow
figure should be produced as observed until source history is available.

## Follow-up: mailbox pagination and refresh durability (Milestone 5)

Gmail `users.messages.list` supplies `nextPageToken`, while Microsoft Graph
lists return `@odata.nextLink` when another page exists. The old connectors
used only the first page, dropped malformed list bodies as verified empty, and
did not detect provider message IDs repeated across pages. Both read paths now
honor requested limits across pages, require explicit valid result shapes,
reject duplicate IDs and stalled/repeated cursors, and propagate partial
failures. Graph next links are validated against the expected HTTPS Microsoft
Graph host and `/v1.0/me/messages` path *before* attaching the OAuth bearer
header; arbitrary external URLs are rejected. No provider messages were read
in the synthetic tests.

Outlook refresh was also wired to a company-scoped encrypted token persistence
callback in the unified adapter. Refresh requires this callback and a refresh
credential. Only a Graph `401` triggers refresh: network outages and other
HTTP failures now propagate without rotating tokens. Persistence failure aborts
the refresh; token values and raw message identifiers are not logged. The
database writes exercised in this workstream were mocked tests only—no live
token or database resource was touched.

**Chat 1 integration/policy requests:**

- Provide a canonical source envelope and persisted checkpoint/cursor for
  mailbox ingestion; page-level deduplication does not solve cross-run thread
  deduplication or ambiguous tenant/account matching.
- Review the older OAuth consent flow in `app/auth/oauth.py`: it currently
  requests Gmail modify, Sheets write and Drive file scopes; `GMAIL_SCOPES`
  also lists send/compose. Implement read-only-first consent with explicit
  separate approvals for additional write scopes in the owner workstream.
- Provide encrypted Google refresh persistence and validated Microsoft scope
  consent. Chat 2 has not claimed provider-backed token rotation, user consent,
  historical mailbox completeness or customer/account matching acceptance.

The existing outbound email and financial-action policy is unchanged. Drafting
and sending are **not** invoked by these connector tests; all outbound actions
must remain disabled pending the shared approval gate.

## Follow-up: invoiced-sales identity and exception checks (Milestone 6)

`app.intelligence.sales_analytics` now rejects duplicate source invoice IDs
instead of silently doubling the billing total; excludes explicitly voided,
cancelled, deleted or draft records; keeps accounts and items with distinct
verified IDs separate even if their names match; and validates line quantities,
line amounts and account-velocity amounts before ranking/trend calculations.
Valid zero amounts remain valid. All changes remain inside Chat 2 analytics
and deterministic owned tests; no shared financial schema was changed.

**Chat 1 owner defect:** `backend/app/api/chat.py:221–229` converts invoice
objects using truthiness fallbacks and `inv.get(...)`. A valid dataclass zero
`total`, empty `line_items`, or empty customer name can invoke `.get` on a
dataclass and crash; the conversion also drops source `id`/`qb_id`,
`customer_id` and `status`. This prevents the analytics engine from detecting
duplicate/void invoices and distinguishing same-named accounts, even after
Chat 2's calculation fix. Please normalize via a mapping/dataclass-safe field
reader, preserve source IDs and status, and retain `Decimal` through the
financial calculation instead of coercing input amounts to float.

**Chat 1 / Chat 5 contract request:** the legacy `total_revenue` field is the
sum of provided invoice totals, **not** recognized or net revenue. `order_count`
is actually invoice count until canonical Order/SalesLine identity is approved.
The current model does not prove currency consistency, discount/tax treatment,
returns, credit memos, payment allocations or reporting completeness. Do not
label these outputs audited revenue, cash receipts or an authoritative sales
ledger. Present incomplete/unknown evidence separately from a verified zero.
