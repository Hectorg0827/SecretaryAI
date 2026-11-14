# Lion BI / Lola — Phase 1: price book and fully loaded pricing

Status: **staging branch implementation, not production-ready**. The migration
and new UI are not applied/deployed merely by merging this code. No customer
price, QuickBooks item, accounting transaction or approved price is changed.

## Current implementation

- `Insights > Price waterfall` has separate inbound and warehouse inputs.
- `Insights > Price book` reads a tenant-scoped proposed/versioned registry
  and fails closed when the backing table is not installed or readable.
- A new additive schema stores versioned price-book rows by
  (company, SKU, package, market, channel, customer, effective date, revision),
  links a cost snapshot, and records one source-backed cost line per component.
- Price publication, supplier billbacks, historical customer-contract sync,
  warehouse invoice ingestion and approval/rollback are **not implemented**.

## Cost boundaries and source-of-truth mapping

**Inbound landed cost (arrival at warehouse):**
supplier merchandise/FOB, ocean/international freight, cargo insurance,
customs duties, broker charges, port fees, domestic port-to-warehouse drayage,
excise tax and other applicable import taxes.

**Warehousing / fulfillment (after arrival):**
receiving/unloading/palletizing, storage for expected holding time, picking/
handling, warehouse-to-customer delivery, shrink/breakage and inventory
financing/carrying costs.

These are separate accounting views. Gross margin uses inbound landed cost.
Contribution margin additionally subtracts warehouse operating costs,
commissions and marketing support. Do not label both amounts "landed."

**Existing sources in Lion BI:**

| Existing module/table | Usable facts | Still missing / unreliable |
| --- | --- | --- |
| Logistics freight engine | ocean, drayage, broker, port, demurrage component models | verified billed shipment-to-SKU allocation and durable invoice links |
| `purchasing_cost_assessments` | SKU/PO provisional cost assessments | independent supplier invoice/FX/landed-cost reconciliation |
| `warehouse_receipt_documents` | independently approved physical receipt / accepted qty | warehouse storage, pallet-day invoices, fulfillment tariffs |
| QuickBooks item + invoice history | list price/purchase cost and observed selling price | approved customer contracts, accurate landed cost, promotional allowances |
| Price-book schema | data contract for price and cost evidence | operational ingestion, approval, publication and staff access |

## Allocation policy (future automated ingestion)

- Preserve original invoice total, currency, FX source and invoice revision.
- For each shared shipment charge, choose **one** allocation basis explicitly:
  received saleable cases (per-case fees), shipment volume/cubic meters,
  gross weight, or pallet-days for time-based warehouse storage.
- For warehouse storage estimate use:
  `pallets allocated to SKU × monthly storage rate × expected days / 30`.
  Divide by expected saleable cases and retain the holding-period assumption.
- For handling use actual expected picks/cases; for last-mile delivery use the
  quoted route/order cost over cases on that route. Inbound domestic freight
  *ends at the warehouse*; warehouse outbound delivery *begins there*.
- When multiple SKUs share a container, allocate each invoice amount once and
  verify the sum of SKU allocations equals the invoice amount before rounding.
  Unallocated differences must be explicit, never silently dropped.
- Treat losses/damage using actual or supported shrink assumptions, not as
  automatic supplier credits; do not count the same write-off twice.
- Never infer a missing warehouse/storage cost is $0. A real zero requires
  a documented no-charge agreement or other verified cost evidence.
- Any missing cost, currency mismatch, stale rate, unapproved price or
  unreconciled supplier invoice blocks a **verified** price recommendation.

## Formulae per case

- `net_revenue = list_price - discount - returns_allowance`
- `inbound_landed = sum(inbound cost categories)`
- `warehouse_cost_to_serve = sum(warehouse cost categories)`
- `fully_loaded_product_cost = inbound_landed + warehouse_cost_to_serve`
- `gross_margin = (net_revenue - inbound_landed) / net_revenue`
- `contribution = net_revenue - fully_loaded_product_cost - commission - marketing`
- `contribution_margin = contribution / net_revenue`
- `minimum_list_at_target_contribution =
   discounts + returns_allowance +
   (fully_loaded_product_cost + commission + marketing)/(1-target_margin)`

The same entered target percentage currently drives legacy gross margin
warnings and the new *target contribution margin* price threshold. A separate
approved gross/contribution target policy per market is still required before
operational use.

Illustrative synthetic case: inbound cost $22.00, warehouse expenses $4.00,
commissions/marketing $2.00, discounts $2.00, and 30% **contribution** target:
minimum list price = $42.00/case. This is **not** an actual Lion BI SKU price.

## Required acceptance gates

1. Price-book migration staged and RLS/grants verified against staging only;
   unauthenticated direct DB readers cannot list price/cost data.
2. Test version history, price-effective-date overlap, and independent approval;
   no same-person self-approval.
3. Reconcile a real PO, freight invoice, accepted receipt, storage invoice,
   customer-route delivery and QuickBooks bill/invoice to the **same SKU**.
4. Reconcile source invoice totals and each allocation including weight/volume
   and pallet-day scenarios. Independent finance confirmation.
5. Demonstrate a mixed-SKU container where a warehouse charge is not also
   included as inbound drayage or product cost.
6. Verify role-specific views for Pricing & Billbacks staff and deny access to
   unrelated tenants and expired/revoked roles.
7. Confirm Lola cites source documents, refuses missing/mixed-currency prices
   and never executes price updates without audited human approval.
8. Browser and backend tests on the exact deploy commit, followed by a
   staging-only pilot. No production rollout without explicit authorization.
