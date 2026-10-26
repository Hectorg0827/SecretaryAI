# Supply-chain workstream 03 — initial integration gate

Baseline: `d79284a40db6e069aa3b39cb00376c95060c7983`.
Scope: source-level audit and synthetic HTTP contract regression, **not** a
live-provider or database-backed acceptance test. No side effects authorized.

## Nine stages at baseline

| Stage | Real entry points | Integration result / evidence required |
| --- | --- | --- |
| 1. Demand/reorder | `app/api/logistics.py:evaluate_reorder_queue` -> `LogisticsModule.evaluate_reorder_queue` -> `ReorderEngine.build_reorder_queue` | Broken API-to-engine field contract and missing `supplier_name`; fixed in workstream 03 with an HTTP regression. API still accepts caller-supplied stock/history, not a source-verified company snapshot. |
| 2. Draft PO | `app/api/logistics.py:generate_purchase_orders` -> `POGenerator.generate_pos` | Returns a synthetic draft/email body and best-effort `procurement_cycles` status. `SimpleNamespace(**dict)` is not a `ReorderDecisionPackage` with `sku_profile`; this route is not the independently approved durable PO workflow. In-memory PO counter is not a durable identity. |
| 3. Supplier response | `parse_vendor_response` -> `VendorResponseParser.parse` | Current route constructs an incomplete `PurchaseOrder` and reads properties not in `ParsedVendorResponse`. No authenticated inbound message association or immutable response event. Stage status update is best-effort and can overstate progress. |
| 4. Freight/cost capture | `parse_freight_invoice` -> `FreightCostCapture` | Parses caller-supplied invoice text on a new booking object; does not retrieve verified invoice bytes or durably attach line items to a shipment. |
| 5. Transit | `LogisticsModule.check_shipment_status` -> `TransitTracker` | Engine exists, no HTTP workflow in `app/api/logistics.py` to persist carrier/vessel milestone and source timestamp. Live provider acceptance absent. |
| 6. Port/customs | `log_customs_hold`, `check_demurrage_risk`, `parse_broker_email` | Computes events/alerts on a fresh per-request module; not linked to a durable booking, port clock or signed customs evidence. |
| 7. Receiving/stock | Legacy `process_warehouse_receipt` and separate `/orders` + `/receipts` routes | Legacy endpoint explicitly returns `analysis_only`. Durable V6 SQL supports distinct PO submit/approve, receipt stage/independent approval and atomic ledger posting (`supabase/migrations/20261017020000_purchasing_receiving.sql`, `20261017010000_stock_ledger.sql`). No verified integration between generated PO, freight, and durable receipts. SQL contract tests are not proof of deployed provider acceptance. |
| 8. Landed-cost variances | `reconcile_costs` -> `CostReconciliationEngine` | Compares client-submitted component totals; does not independently match approved invoice documents, freight, duties, currency rates, or received quantities. Best-effort pipeline upsert must not mean financially reconciled. |
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
