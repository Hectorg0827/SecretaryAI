# Supply-chain workstream 03 — initial integration gate

Baseline: `d79284a40db6e069aa3b39cb00376c95060c7983`.
Scope: source-level audit and synthetic HTTP contract regression, **not** a
live-provider or database-backed acceptance test. No side effects authorized.

## Nine stages at baseline

| Stage | Real entry points | Integration result / evidence required |
| --- | --- | --- |
| 1. Demand/reorder | `app/api/logistics.py:evaluate_reorder_queue` -> `LogisticsModule.evaluate_reorder_queue` -> `ReorderEngine.build_reorder_queue` | Broken API-to-engine field contract and missing `supplier_name`; fixed in workstream 03 with an HTTP regression. API still accepts caller-supplied stock/history, not a source-verified company snapshot. |
| 2. Draft PO | `app/api/logistics.py:generate_purchase_orders`; separate durable `/orders` RPC | Baseline route crashed for flat `SimpleNamespace` packages, could mint in-memory PO numbers, and falsely upsert stage 2 for caller-supplied `approved=true`. The legacy endpoint is now explicitly blocked with HTTP 409 until a server-verified approval contract exists. Independently approved durable `/orders` remains available but is not connected to reorder proposals. |
| 3. Supplier response | `parse_vendor_response` -> `VendorResponseParser.parse` | Baseline route constructed an incomplete `PurchaseOrder`, read nonexistent response fields and best-effort advanced the pipeline. Now repaired as a pure unverified analysis preview: no DB write, supplier alert or status promotion. Authenticated inbound message association, authoritative PO lines and durable response event are still missing. |
| 4. Freight/cost capture | `parse_freight_invoice` -> `FreightCostCapture` | Stage 3.4 fixes multi-line extraction, overlapping cost-label double count, repeated component allocation and non-USD input rejection. The route returns explicitly unverified candidate USD amounts, not independently matched invoices or durable shipment costs. |
| 5. Transit | `LogisticsModule.check_shipment_status` -> `TransitTracker` | Engine exists, no HTTP workflow in `app/api/logistics.py` to persist a provider-verified carrier milestone and source timestamp. The default adapter previously fabricated coordinates, vessel name and `in_transit` status; it now reports unavailable/unknown instead. Live provider acceptance absent. |
| 6. Port/customs | `log_customs_hold`, `check_demurrage_risk`, `parse_broker_email` | Stage 3.4 validates port-day/date inputs, rejects future arrival and invalid dates, guards negated release and pending duty statements, and labels all API outcomes as candidate/estimate only. No verified booking/arrival, duty evidence, real alerts or durable status transition. |
| 7. Receiving/stock | Legacy `process_warehouse_receipt`, separate `/orders` + `/receipts` routes, and new `/warehouse/*` work-order previews | Legacy endpoint explicitly returns `analysis_only`. Durable V6 SQL supports distinct PO submit/approve, receipt stage/independent approval and atomic ledger posting (`supabase/migrations/20261017020000_purchasing_receiving.sql`, `20261017010000_stock_ledger.sql`). Stage 3.5 adds route-mounted, role-gated **preview-only** picking, cycle-count, capacity/slotting and supplier-claim drafts; no verified linkage to a posted receipt, tenant-owned bin or event ledger yet. SQL contract tests are not proof of deployed provider acceptance. |
| 8. Landed-cost variances | `reconcile_costs` -> `CostReconciliationEngine` | Baseline accepted incomplete/mismatched cost payloads and falsely upserted stage 8 from client text. The route now validates source identity, same SKU, finite nonnegative components and positive cases; result is `analysis_only` with candidate alerts, never a verified invoice or stage transition. Independent invoice/currency/receipt match is still missing. |
| 9. Learning/report | `build_seasonal_indices`, `monthly_logistics_report`, `LearningEngine` | Seasonal arithmetic exists; `LogisticsModule` is created fresh per request with empty `_lead_time_records`, so supplier performance/lead-time memory is not persisted or recovered from verified receipts. |

Independent engines (`stochastic_inv.py`, `scor_toc.py`, `returns_mgmt.py`,
`esg_tco.py`) have calculation and unit-test coverage but no verified durable
event feed across these nine stages. Calculations are not executed transactions.

## First owned regression

`tests/test_wholesale_distribution/test_logistics/test_reorder_api_contract.py`
dispatches through FastAPI with synthetic tenant-scoped company configuration.
It covers valid reorder data, safe no-reorder stock, missing demand observations,
seasonal-week conversion and unsupported supply type. It first reproduced the
missing required `SKUProfile.supplier_name`. The API also passed keys the
engine ignored and serialized a nonexistent package `.sku` attribute.

This does not prove QuickBooks/warehouse source coverage, actual vendor lead
times, or approval to issue a purchase order.

## Second owned regression — supplier-response safety preview

`tests/test_wholesale_distribution/test_logistics/test_vendor_response_api_contract.py`
first reproduced the HTTP `PurchaseOrder` construction failure with caller-
provided text. The repaired endpoint uses the real parser shape and returns
`analysis_only` candidate classification with permanent `message_verified=false`,
`po_link_verified=false`, `requires_human_review=true` and
`supplier_action_permitted=false` until a verified message/PO contract exists.
It performs no company lookup, cycle upsert, supplier alert, email send or
inventory action. A parsed response is **not** evidence that the named supplier
confirmed or rejected a durable purchase order.

## Third owned regression — no phantom procurement states

`tests/test_wholesale_distribution/test_logistics/test_pipeline_status_guard.py`
first reproduced four failures: client-forged approval crashed the PO generator;
landed-cost comparison lacked an analysis-only status, accepted mismatched SKU
and allowed missing source identity. The stage 2 route is now a documented
HTTP 409 until a canonical, server-verified human approval exists. Stage 8
calculations remain available as previews with typed finite per-case inputs,
no `procurement_cycles` upsert, no accounting claim, no actual alerts and no
inventory/financial side effect. Suggested alerts require human review. The
legacy best-effort `_upsert_cycle` helper was removed from the API file.

## Fourth owned regression — freight, tracking and customs evidence boundaries

`tests/test_wholesale_distribution/test_logistics/test_freight_customs_api_contract.py`
reproduced 13 initial API/engine failures and additional cost/negation failures.
The original freight parser broke on the first invoice line, omitted valid
later components, double-counted overlapping THC labels and overwrote repeated
component costs in SKU allocation. Now parsed lines are individually labeled
candidate costs, ambiguous multi-amount lines are skipped, unsupported EUR/GBP
inputs are rejected pending a reviewed FX contract, and allocation accumulates
repeated components before rounding. A syntactically parsed USD amount is not
evidence of invoice currency, supplier identity, PO match, or case-count unit.

The fallback vessel adapter once returned invented coordinates and status;
now it returns an unavailable/unknown observation. Customs holds and broker
messages from caller text, and demurrage based on supplied dates, return only
analysis-only estimates and candidate milestones. No authentic operational
alert is returned or sent, and no cycle/stock/accounting state is changed.
Invalid/future port dates are rejected rather than converted to zero cost.
Negated releases and unpaid-duty messages cannot become positive milestones.
These are synthetic HTTP and calculation checks, **not** real vessel, broker,
forwarder or warehouse integration acceptance.

## Fifth owned regression — warehouse work-order draft boundaries

`app/api/warehouse.py` is mounted from the owned `app/api/logistics.py` router
under `/api/logistics/warehouse/`; no shared application loader was edited.
`app/intelligence/wholesale_distribution/warehouse/planning.py` accepts bounded,
timezone-aware caller snapshots (strict **cases** unit) and generates:

- FEFO-first bin/lot pick drafts, excluding expired observations and tracking
  local bin availability across multiple order lines, with shortages flagged.
- Cycle-count variance/recount drafts, with no stock-adjustment pathway.
- Capacity utilization, reservation overcommit and slotting-move candidates;
  fewer than 30 observed pick events yield `insufficient_data` confidence.
- Supplier shortage, over-receipt and damaged-case claim drafts, with no sender,
  claim filing or financial write.

`tests/test_wholesale_distribution/test_warehouse_previews.py` covers the real
logistics-router registration, role restrictions, tenant identity sourced from
the authenticated actor, duplicate/negative/mixed-unit input rejection, stale
and naive timestamps, FEFO, shortage, no repeated bin allocation, safe empty
capacity, slotting sample size and claim exceptions. All responses are
`analysis_only`, with unverified source and required human review.

**Not yet an operational warehouse system:** A caller-supplied bin, order, PO or
receipt identity is not proof that it belongs to the tenant; none is queried or
persisted. Pick lists cannot be issued or marked shipped; stock adjustments and
claims cannot be approved, posted or sent. Shared schema/RLS and durable policy
contracts belong to Chat 1. Authenticated warehouse and QuickBooks snapshots,
including bin+lot allocations and source timestamps, belong to Chat 2.

## Interface requests — owner to forward manually

**Chat 1 — architecture / approval gate:** Define tenant-scoped
`reorder_proposal_id`, immutable input snapshot/version, quantity unit,
approval status/actor/time and durable idempotency key. Specify a transition
from approved proposal to the existing V6 `purchasing_orders` and linked
shipments, invoice claims and reviewed cost variances. Synthetic pipeline
upserts must not advance verified state. Gate sending POs, posting stock,
adjustments, returns and financial writes independently. Supply migration/RLS
ownership for needed event tables; Chat 3 will not edit shared migrations.
For warehouse execution, define tenant-scoped location/bin/lot and count-session
identity, immutable snapshot hashes, approval actors, pick reservations,
movement/recount/claim event idempotency and auditable state transitions.
Approval must be independent of the requesting user and cannot be inferred from
successful calculation or a caller-supplied boolean.

**Chat 2 — data/connectors:** Supply a read-only authenticated snapshot of
mapped QB IDs, warehouse stock, allocations, in-transit purchase lines, open
orders, sales history, supplier lead times and source freshness (`observed_at`
and `synced_at` UTC). Specify units, warehouse/QB overlap, pagination,
completeness and provider-failure reason codes. Verify inbound supplier email
thread, shipment/carrier milestones and customs evidence separately from
parsed free text. Missing provider data cannot silently mean zero quantities.
For stages 4–6, specify verified invoice byte/hash, currency and approved FX
rate source/date; booking/container/PO association; carrier status source,
provider timestamp and unknown/outage semantics; broker message identity;
port arrival/free-time rules, terminal tariff schedule and independent invoice
review. No caller text may be upgraded to a customs release or duty payment.
For warehouse reads, specify verified bin location capacity, SKU/lot/unit and
expiry provenance, allocations, quarantines, open orders, counted balances and
pick-event history with UTC timestamps, complete pagination and tenant mapping.

## Acceptance still outstanding

- Verified sandbox source snapshot with dates and units.
- Proposal -> independent approval -> durable PO; no draft stock booking;
  deterministic replay protection and approved supplier communications.
- Independently sourced supplier confirmation, transit and customs milestones.
- Inspected receipt -> exactly-once stock movement with concurrency,
  bad-unit, replay and partial-failure tests plus full audit history.
- Independent line-item landed-cost validation, documented exceptions and
  durable historical learning events.
- No live migrations, messages, financial writes or deployments absent owner
  authorization and Chat 1's gate.
