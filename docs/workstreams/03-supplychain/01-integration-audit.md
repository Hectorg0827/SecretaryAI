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
| 4. Freight/cost capture | `parse_freight_invoice` -> `FreightCostCapture` | Parses caller-supplied invoice text on a new booking object; does not retrieve verified invoice bytes or durably attach line items to a shipment. |
| 5. Transit | `LogisticsModule.check_shipment_status` -> `TransitTracker` | Engine exists, no HTTP workflow in `app/api/logistics.py` to persist carrier/vessel milestone and source timestamp. Live provider acceptance absent. |
| 6. Port/customs | `log_customs_hold`, `check_demurrage_risk`, `parse_broker_email` | Computes events/alerts on a fresh per-request module; not linked to a durable booking, port clock or signed customs evidence. |
| 7. Receiving/stock | Legacy `process_warehouse_receipt` and separate `/orders` + `/receipts` routes | Legacy endpoint explicitly returns `analysis_only`. Durable V6 SQL supports distinct PO submit/approve, receipt stage/independent approval and atomic ledger posting (`supabase/migrations/20261017020000_purchasing_receiving.sql`, `20261017010000_stock_ledger.sql`). No verified integration between generated PO, freight, and durable receipts. SQL contract tests are not proof of deployed provider acceptance. |
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

## Interface requests — owner to forward manually

**Chat 1 — architecture / approval gate:** Define tenant-scoped
`reorder_proposal_id`, immutable input snapshot/version, quantity unit,
approval status/actor/time and durable idempotency key. Specify a transition
from approved proposal to the existing V6 `purchasing_orders` and linked
shipments, invoice claims and reviewed cost variances. Synthetic pipeline
upserts must not advance verified state. Gate sending POs, posting stock,
adjustments, returns and financial writes independently. Supply migration/RLS
ownership for needed event tables; Chat 3 will not edit shared migrations.

**Chat 2 — data/connectors:** Supply a read-only authenticated snapshot of
mapped QB IDs, warehouse stock, allocations, in-transit purchase lines, open
orders, sales history, supplier lead times and source freshness (`observed_at`
and `synced_at` UTC). Specify units, warehouse/QB overlap, pagination,
completeness and provider-failure reason codes. Verify inbound supplier email
thread, shipment/carrier milestones and customs evidence separately from
parsed free text. Missing provider data cannot silently mean zero quantities.

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
