# Logistics journey — interface documentation (Workstream 3)

Owner: Workstream 3 (logistics, purchasing, warehouse, returns, compliance, operational intelligence).
Code: `backend/app/intelligence/wholesale_distribution/logistics/` and `backend/app/api/logistics.py`.

This layer **extends** the existing 9-stage procurement engines; it does not replace them and is not a second
logistics application. The 9-stage engines calculate (reorder quantities, PO drafts, freight parsing, demurrage
maths). The new 14-stage journey ledger is the system of record for *what has actually happened* to a shipment,
*who said so*, and *whether it is verified*.

---

## 1. Safety contract (applies to every interface below)

| Rule | Where enforced |
|---|---|
| An email, AI interpretation or unreviewed document never becomes an official milestone, ETA, port term, allocation or delivery. It is stored as **evidence** (`verified: false`). | `journey.py` — `MilestoneSpec.authoritative`, `_apply_*` |
| Only a **named person** can promote evidence (`confirm_evidence`). `ai`, `system`, `lola`, blank etc. are refused. | `journey._is_non_human`, DB check `ck_logistics_events_confirmer` |
| People cannot claim machine sources (`carrier_api`, `customs_system`, `broker_edi`, `wms`, `erp`, …) over HTTP — those come only through server-side connectors. | `api/logistics.py` `POST /journey/events` → 403 |
| No freight booking, customs submission, stock change, financial write or external message is executed. Stock changes and claims become `ActionProposal`s queued in the existing `ApprovalQueue` after `PolicyEngine` evaluation — **even when policy says "allow"**. | `receiving.submit_for_approval` |
| Messages to suppliers/brokers/customers are **drafts** (`send: false`, `requires_approval: true`). | `monitoring._draft` |
| Every query is scoped to the JWT's `company_id`; client-supplied tenant ids are ignored. | `ShipmentLedger` (rejects other-company events), `SupabaseEventStore.load`, `LogisticsViewer` |
| Stub/unconfigured tracking results never appear as data (no fake vessel positions, no "credentials_not_configured" deliveries). | `freight.TransitTracker`, `store.parcel_tracking_to_events` |

---

## 2. The 14 stages

`JourneyStage` (`journey.py`). Each stage in `Shipment.stage_view()` reports: `status`
(`not_started | in_progress | reported_unverified | complete | exception | not_applicable`), `responsible_party`,
`expected_date`, `actual_date`, `verified_milestones`, `unverified_reports`, `documents` checklist, `exceptions`.

| # | Stage | Completing milestone | Authoritative sources (plus `user_confirmed` / `document_validated`) |
|---|---|---|---|
| 1 | Demand & replenishment | `replenishment_recommended` | `system_calculated` |
| 2 | PO preparation & approval | `po_approved` | `erp` |
| 3 | Supplier confirmation | `supplier_confirmed` | `supplier_edi` |
| 4 | Production & shipment readiness | `cargo_ready` | `supplier_edi`, `forwarder_api` |
| 5 | Freight booking | `freight_booked` | `forwarder_api`, `carrier_api` |
| 6 | Container & carrier movement | `vessel_arrived` | `carrier_api`, `terminal_api` |
| 7 | Customs documentation & clearance | `customs_released` | `broker_edi`, `customs_system` |
| 8 | Port arrival & demurrage | `gate_out_port` | `terminal_api`, `carrier_api` |
| 9 | Warehouse scheduling & receiving | `arrived_at_warehouse` | `wms`, `carrier_api` |
| 10 | Inventory reconciliation | `inventory_reconciled` | `wms`, `erp` |
| 11 | Customer order allocation | `customer_allocated` | `erp`, `wms` |
| 12 | Final distribution & delivery | `delivered` | `carrier_api` |
| 13 | Proof of delivery | `pod_received` | `carrier_api` |
| 14 | Claims, returns & financial reconciliation | `financially_reconciled` | `erp` |

Full milestone catalogue: `journey.MILESTONES`. Stages 7–8 are `not_applicable` for domestic shipments.
Sequence guards (`PREREQUISITES`): pickup / warehouse receipt before a verified customs release, and POD before
delivery, are accepted but raise a `SEQUENCE_ANOMALY` exception.

---

## 3. Event contract — `LogisticsEvent`

```python
LogisticsEvent(
    company_id: str,          # from JWT / connector context — never from the client body
    shipment_id: str,
    kind: str,                # see table below
    source: SourceType,
    code: str = "",           # milestone code (kind="milestone")
    occurred_at: str = "",    # ISO date/datetime the thing happened
    source_ref: str = "",     # email message id, EDI control #, document id, tracking #
    payload: dict = {},
    confirmed_by: str = "",   # REQUIRED for user_confirmed / document_validated
    idempotency_key: str = "" # auto: sha256 of the content; duplicates are no-ops
)
```

| kind | payload | notes |
|---|---|---|
| `shipment_created` / `shipment_updated` | `po_numbers, supplier_id, supplier_name, carrier, forwarder, broker, warehouse, container_numbers, bol_number, booking_ref, origin, destination, mode, supply_chain_type, po_sent_at, requested_ship_date, lines[{sku, qty_cases, unit_cost, lot, description}], commercial{payment_terms, incoterm, freight_quote_usd…}, parties{role: name}, expected_dates{code: date}, cancelled` | unverified updates that conflict with BOL/booking → `IDENTIFIER_CONFLICT`, not applied |
| `milestone` | `reason` (holds), `order_id`, `doc_id`, `notes`, `correction: true` | earliest verified occurrence wins unless `correction` |
| `eta` | `eta` (ISO date), `location` | verified ETA = carrier/terminal/forwarder/WMS or human |
| `plan` | `expected_dates{code: date}` | expected vs actual per stage |
| `document` | `doc_id, doc_type, status, fields, failed_checks, suggests_milestone, milestone_date` | produced by `DocumentIntelligence.to_events` |
| `cost` | `component, amount_usd, basis` | same `source_ref`+component replaces (no double count) |
| `allocation` | `order_id, customer_id, customer_name, sales_rep_id, sku, qty_cases, promised_date` | |
| `allocation_update` | `order_id, status, tracking_number, pod_doc_id` | parcel/WMS delivery status |
| `appointment` | `start, end, dock, warehouse, reference` | |
| `port_terms` | `last_free_day, demurrage_rate_per_day, detention_free_days, detention_rate_per_day, terminal` | |
| `exception` / `exception_resolved` | `code, severity, message, stage, data` / `exception_id` | |
| `evidence_confirmed` | `note` | emitted by `ShipmentLedger.confirm_evidence` |
| `receipt_reconciled` | `summary{…}, exceptions[…]` | emitted by `ReceivingReconciler.to_ledger_event` |

### Persistence
Append-only table `logistics_events` — migration `backend/supabase/migrations/20261109000011_logistics_journey.sql`
(unique `(company_id, idempotency_key)`, source whitelist, confirmer check, UPDATE blocked by trigger, RLS
backend-only: browser roles have no grants — see the migration header). State is rebuilt by replay: `ShipmentLedger.load(company_id, SupabaseEventStore(db))`.

Failure behaviour: the event is applied to a copy, persisted, and only then committed. If the store write fails the
API returns **503 "nothing was changed"** and the same event can be retried safely.

> Scaling note: the API currently replays a company's event log per request. That is fine for SMB volumes
> (thousands of events). A snapshot table can be added later without changing any interface.

---

## 4. Python interfaces

| Module | Entry point | Purpose |
|---|---|---|
| `journey.py` | `ShipmentLedger(company_id, store)`, `.ingest(event)`, `.confirm_evidence(id, confirmed_by)`, `.find(identifier)`, `.rebuild(...)`, `.load(...)` | system of record |
| `documents.py` | `DocumentIntelligence().process(text, ledger, reviewed_by="")` → `(ExtractedDocument, [LogisticsEvent])` | identify / extract / validate / link; ISO 6346 container check digit; `broker_status(text)` |
| `receiving.py` | `ReceivingReconciler(company_id).reconcile(shipment, [ReceivedLine], received_by, ReceivingEvidence)`; `submit_for_approval(proposals, PolicyEngine, ApprovalQueue)` | receipt reconciliation, damage quarantine (proposed), liability, claim drafts |
| `landed_cost.py` | `landed_cost(shipment)` | per case and per saleable case, `final` vs `estimated` |
| `monitoring.py` | `LogisticsMonitor(ledger).scan(as_of)` → `[LogisticsAlert]` | prioritized alerts + drafts |
| `visibility.py` | `LogisticsViewer.from_user(jwt_payload)`, `.project(shipment)`, `.filter_alerts(...)` | role scoping |
| `assistant.py` | `LogisticsAssistant(ledger, viewer).ask(question, as_of)` → `AssistantAnswer` | Lola's logistics Q&A |
| `store.py` | `SupabaseEventStore(db)`, `parcel_tracking_to_events(company_id, shipment_id, order_id, tracking)` | persistence, ShipTrack adapter |
| `api/logistics.py` | `ingest_integration_events(db, company_id, events)` | **server-side** entry point for connectors (machine sources) |

`AssistantAnswer` = `{intent, answer, data, sources, links[{shipment_id, containers, po_numbers, orders, skus, communications}], restricted, suggested_actions}` — `links` connect each answer to customer orders, inventory (SKUs), purchasing (POs), finance (landed cost / claims via data) and communications (email message ids).

Supported intents: `where_is`, `delayed`, `arriving_this_week`, `demurrage`, `missing_customs_docs`,
`broker_released`, `overdue_pos`, `customer_not_received`, `in_transit_inventory`, `proof_of_delivery`,
`landed_cost`, `supplier_responsibility`.

---

## 5. HTTP endpoints (`/api/logistics`)

New:

| Method & path | Who | Notes |
|---|---|---|
| `POST /journey/events` | operators (`view_all` or `back_office`) | sources: `user_confirmed` (recorded under caller's id), `email_parsed`, `ai_inferred`, `manual_note`. Machine sources → 403 |
| `GET /journey/shipments` | any logistics tier | role-projected list |
| `GET /journey/shipments/{id}` | any logistics tier | 14-stage view; 404 if not visible |
| `GET /journey/shipments/{id}/landed-cost` | `view_financials` | |
| `POST /journey/evidence/{evidence_id}/confirm` | operators | human promotion |
| `POST /documents/ingest` | operators | `{text, doc_id, hint, reviewed, received_at}` |
| `POST /receiving/reconcile` | operators | records verified receipt + reconciliation; queues proposals; money hidden without `view_financials` |
| `GET /monitor?as_of=` | any logistics tier | role-filtered alerts |
| `POST /ask` | any logistics tier | `{question, as_of}` → `AssistantAnswer` |

Changed (bug fixes, backwards compatible): `/reorder/evaluate`, `/vendor/parse-response` (+ optional
`shipment_id`, `message_id`), `/freight/parse-invoice`, `/receipt/process`, `/learning/seasonal-indices`,
`/customs/parse-broker-email` (+ optional `shipment_id`, `message_id`; adds `verification` field).

---

## 6. Visibility rules (built on `app.auth.rbac.ROLE_PERMISSIONS`)

| Tier | Roles | Sees | Does not see |
|---|---|---|---|
| `full_operations` | owner, manager | everything; commercial data via `view_financials` | — |
| `logistics` | back_office | all company shipments' operational data: milestones, ETAs, documents, exceptions, quantities | unit costs, landed cost, freight $, supplier terms, demurrage $, supplier scorecards, **customer identities** on allocations (redacted unless shipment in `assigned_shipment_ids` claim) |
| `sales` | sales_rep | only shipments carrying an allocation where `sales_rep_id == sub` or `customer_id ∈ customer_ids` claim; their own orders, ETA, delivery & POD | other customers, supplier identity/terms, POs, costs, commercial documents, unverified evidence |
| `none` | viewer | nothing (403) | |

Sales scoping **fails closed**: an allocation without a rep is not shown to any rep.

---

## 7. Coordination requests

### For Workstream 1 (shared contracts, orchestration, auth)
1. **Chat orchestrator tool**: register `POST /api/logistics/ask` (or `LogisticsAssistant.ask`) as Lola's logistics
   tool for `logistics`/`inventory` intents. The LLM should phrase `answer`/`data`, never invent facts. No change
   was made to `ai/secretary.py`.
2. **JWT claims** (optional, additive): `customer_ids: [str]` for sales reps and `assigned_shipment_ids: [str]` for
   logistics staff. Without them the rules above fail closed.
3. **Policy rules**: action types `logistics.post_inventory_receipt`, `logistics.post_inventory_quarantine`
   (`commit`, capability `inventory`) and `logistics.file_claim` (`draft`, capability `claims`). Consider adding
   `("claims", …)` to `router/policy.CAPABILITY_PERMISSIONS`.
4. **Executor**: after approval, the existing approval flow must execute the stock postings (it currently has no
   handler for these action types, so approved drafts are recorded but not executed — by design of this PR).
5. `require_permission("manage_inventory")` on older endpoints matches no role permission, so only owner/manager pass
   (via `view_all`). Left unchanged — decide whether back_office should get it.

### For Workstream 2 (data sources)
Feed authoritative events through `ingest_integration_events(db, company_id, events)`:

| Missing authoritative feed | Gives | Today |
|---|---|---|
| Ocean carrier / visibility API (e.g. carrier track & trace, aggregators) | departures, arrivals, transshipments, verified ETAs | **none** — `StubVesselTracker` only |
| Terminal / port API | discharge, availability, last free day, gate-out, empty return | **none** |
| Customs (ACE) or broker EDI/portal | ISF, entry, holds, exams, release | **none** — broker emails only (unverified) |
| Freight forwarder API | booking confirmations, cargo-ready, invoices | **none** |
| Supplier EDI 855/856 or portal | PO acknowledgements, ASN | **none** |
| WMS | appointments, receipts, putaway | **none** — counts entered by staff |
| Parcel carriers | out-for-delivery, delivered, ePOD | `ShipTrackConnector` (FedEx/UPS/DHL) — adapter `parcel_tracking_to_events` provided |
| ERP / QuickBooks | POs, allocations, invoices | existing QB connectors — mapping to events still to be wired |

Email ingestion should emit `email_parsed` events (with the message id as `source_ref`); documents should go
through `DocumentIntelligence.process`.
