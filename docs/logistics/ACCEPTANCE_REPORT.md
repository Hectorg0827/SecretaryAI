# Logistics acceptance report — Workstream 3

Date: 2026-11-09 · Branch: `claude/lola-logistics-intelligence-92yog8`
Environment: Python 3.13, backend test suite (`cd backend && python -m pytest -q`), PostgreSQL 15 scratch
instance for the migration.

## Result

| Suite | Result |
|---|---|
| Full backend suite (before this work) | 1,086 passed, 4 skipped |
| **Full backend suite (after)** | **1,265 passed, 4 skipped, 0 failed** |
| New logistics tests | 179 (all passing) |

| New test file | Tests | Covers |
|---|---|---|
| `test_journey_ledger.py` | 36 | 14-stage transitions, provenance, ETA provenance, customs holds, duplicates, tenant isolation, failure recovery |
| `test_documents.py` | 36 | identify/extract/validate/link for 13 document types, ISO 6346 container check digit, broker-status negation |
| `test_receiving_acceptance.py` | 18 | **mandatory 1,200-case scenario**, liability, claims, hand-off to PolicyEngine + ApprovalQueue |
| `test_monitoring.py` | 22 | all 10 proactive detections, prioritization, drafts never sent, landed cost |
| `test_visibility_and_assistant.py` | 29 | role scoping (owner / logistics / sales / viewer / other company), all 12 questions |
| `test_logistics_api.py` | 24 | HTTP endpoints, machine-source refusal, 503 on store outage, legacy endpoint regressions |
| `test_engine_regressions.py` | 14 | fixes to existing engines, parcel adapter, Supabase store |

The migration `20261109000011_logistics_journey.sql` was applied to a real PostgreSQL instance and checked:
duplicate idempotency key → rejected; `user_confirmed` without a confirmer → rejected; `UPDATE` → rejected by
trigger; deleting the company → cascades (DELETE 1).

---

## Mandatory acceptance scenario

PO-1001 expects **1,200 cases** of WINE-RED at $24.00/case. The warehouse counts **1,180 good + 20 damaged**.

| Requirement | Outcome (asserted in tests) |
|---|---|
| Reconcile the receipt | expected 1,200 · good 1,180 · damaged 20 · short 0 · over 0 · accounted 1,200 · status `discrepancy` |
| Separate damaged inventory | 1,180 → `AVAILABLE`, 20 → `QUARANTINE-DAMAGED` (`saleable: false`) — **proposed**, `executed: false` |
| Financial implications | PO value $28,800.00 · saleable value $28,320.00 · damaged at PO cost **$480.00** · effective cost per saleable case $24.4068 · landed-cost value `null` unless landed cost supplied (never invented) · status "NOT POSTED — no credit, payable adjustment or journal entry has been made" |
| Identify responsibility | damage noted on delivery receipt + transit handling → **carrier** (high); packaging defect + intact seal → **supplier**; no evidence → **undetermined** with the list of missing evidence |
| Prepare claim | `carrier_cargo_claim` draft vs the delivering carrier for $480.00 (or supplier claim / pending-liability draft), with required vs on-file documents and filing-window guidance; `status: draft`, `requires_approval: true`, narrative ends "not sent" |
| Use existing authorized mechanisms | 3 proposals evaluated by the real `PolicyEngine` (stock postings → `require_approval`) and enqueued in the real `ApprovalQueue`; the only table written is `drafts`. A policy `deny` queues nothing. Even a policy `allow` is queued, not executed |
| Audit trail | `receipt_counted` (by the receiving user) → `liability_assessed` → `proposals_prepared`; ledger records a verified `warehouse_received` milestone under the user's id and a `RECEIPT_DAMAGE` exception |
| Over HTTP | `POST /api/logistics/receiving/reconcile` returns the above; tables touched: `logistics_events`, `drafts` only. Back-office staff get quantities and liability but no dollar amounts |

---

## Required test areas

| Area | Evidence |
|---|---|
| Shipment transitions | verified milestones advance the stage; all 14 stages report status/party/expected/actual/documents/exceptions; domestic → customs & port `not_applicable`; pickup before customs release → `SEQUENCE_ANOMALY`; events after cancellation flagged |
| ETA provenance | carrier ETA slip 10th → 15th = +5 days; an emailed ETA is shown as "reported (unverified)" and never changes the official ETA or creates a delay alert |
| Customs holds | broker-EDI hold → critical `CUSTOMS_HOLD` exception, stage status `exception`; verified release resolves it; emailed hold is evidence only; "NOT released" emails are never read as releases |
| Duplicate events | identical event → `duplicate`, no state change; resent invoice line not double-counted; replaying the full log is harmless; DB unique constraint |
| Damage | 1,200 / 1,180 / 20 scenario above; shortage with intact seal → supplier; broken seal → carrier |
| Claims | carrier / supplier / pending-liability drafts; claim values hidden from non-finance roles |
| Landed-cost reconciliation | `estimated` until ocean freight, duty, broker fees and drayage are all present and verified, then `final` ($34,000 / 1,200 = $28.3333); per-saleable-case after damage ($30,000 / 1,180); unverified invoice lines keep the estimate |
| Cross-company isolation | ledger rejects other-company events; replay ignores other tenants' rows; same container number in two companies never crosses; company B gets 404 / empty list / "couldn't find" over HTTP |
| Failure recovery | store outage → 503 "nothing was changed", state identical, retry succeeds; rebuilt ledger equals live ledger; malformed historic row skipped |

---

## Conversational answers (actual output, test fleet, as of 2026-11-09, owner role)

```
Q: Where is my container MSCU1234566?
A: MSCU1234566 is at stage 'Container & carrier movement'; last verified event: Departed origin on
   2026-09-21 (carrier_api); verified ETA 2026-11-15 (from carrier_api); +5 day(s) vs original.

Q: What shipments are delayed?
A: 1 delay(s): container MSCU1234566 delayed 5 day(s).

Q: Which shipments arrive this week?
A: 2 shipment(s) have a verified ETA between 2026-11-09 and 2026-11-15: SHP-7 on 2026-11-12,
   MSCU1234566 on 2026-11-15

Q: Which containers are at risk of demurrage?
A: 1 container(s) need attention: container TCKU7654323: demurrage accruing — Last free day was
   2026-11-07 (2 day(s) ago). (exposure ~$350)

Q: What customs documents are missing?
A: 3 shipment(s) have customs document gaps: MSCU1234566: missing bill_of_lading, commercial_invoice,
   packing_list; TCKU7654323: missing commercial_invoice, packing_list; MAEU5550007: missing ...

Q: Has the broker released the cargo for TCKU7654323?
A: TCKU7654323: NO — on customs hold (Customs hold: Missing commercial invoice).
   (An email in the fleet claims it was released; it is held as unverified evidence.)

Q: Which purchase orders are overdue?
A: 2 PO issue(s): PO PO-1003 overdue 15 day(s) ...; supplier confirmation late — ... An unverified
   confirmation exists (email_parsed) — review it.

Q: Why hasn't Tienda Tres received their order?
A: Order SO-601 (300 cs WINE-RED) is not delivered because the inbound container is on a customs hold;
   the goods have not yet been received at the warehouse.

Q: What inventory is currently in transit?
A: In transit: WINE-RED 2400 cs on the water, 1200 cs at port.

Q: Which deliveries have proof of delivery?
A: 1 delivered order(s) have proof of delivery (SO-401); 0 delivered order(s) are missing POD.

Q: What is the landed cost per case for PO-0990?
A: PO PO-0990: $27.00/case — FINAL.

Q: Which suppliers are responsible for shortages or damage?   (after a 20-damaged receipt)
A: PO PO-1002 from Vinos del Sur SA: 20 damaged, 0 short — likely responsible: carrier.
```

Sales rep `rep-2` (owns only SO-502):

```
Q: What is the landed cost per case?            A: Landed cost is restricted to users with financial access.
Q: Why hasn't ... received order SO-501?        A: I couldn't find that customer order among the shipments you can see.
Q: Why hasn't ... received order SO-502?        A: Order SO-502 (600 cs WINE-RED) is not delivered because the inbound
                                                   shipment has not arrived (verified ETA 2026-11-15); that shipment is
                                                   running 5 day(s) late.
```

---

## Defects found and fixed in existing code

| # | Location | Defect | Impact |
|---|---|---|---|
| 1 | `customs.parse_broker_email` | checked "released" before "hold"; "NOT released — on hold" was logged as **released** | unverified email could clear cargo |
| 2 | `freight.FreightCostParser` | misplaced `break` → only the first invoice line was read | freight costs understated |
| 3 | `cost_reconciliation.build_landed_cost` | repeated components overwrote each other | landed cost understated |
| 4 | `logistics_module.pipeline_status` | read 4 attributes that don't exist | AttributeError on every call |
| 5 | `freight.TransitTracker` | stub tracker's fake vessel position returned as data | fabricated location |
| 6 | `api /reorder/evaluate` | wrong input keys (every SKU skipped), missing `supplier_name`, nonexistent `.sku` | 500 / empty queue |
| 7 | `api /vendor/parse-response` | `PurchaseOrder` missing 5 required args; wrong result attribute names | 500 |
| 8 | `api /freight/parse-invoice`, `/receipt/process` | `FreightBooking(booking_id=…)` — no such field | 500 |
| 9 | `api /learning/seasonal-indices` | passed tuples where floats expected | 500 |

Found in the new code during development (by the new tests or by test-fleet runs) and fixed before commit:
AI/system actors could confirm evidence (case-sensitivity bug); supplier names leaked to sales reps through
"responsible party" labels; customer allocation was reported as the container's location; four false-positive
alert types (freight "−100%" with no invoice, "PO overdue" on delivered shipments, no on-time grace, stale ETA for
arrived containers). Each now has a covering test.

---

## Known limits / not done

* **No authoritative carrier, terminal, customs/broker, forwarder, supplier-EDI or WMS feed exists in the repo.**
  Until Workstream 2 connects them, verified facts come from ERP data, parcel tracking (FedEx/UPS/DHL via
  `ShipTrackConnector`) and named staff confirmations. See INTERFACES.md §7.
* Approved logistics drafts are not executed yet — `api/actions._execute_approved_draft` has no handler for
  `logistics.*` action types (Workstream 1 / inventory owner).
* Lola's chat orchestrator does not yet call `/api/logistics/ask`; shared orchestration files were not modified.
* Claim filing windows are general guidance and are labelled "confirm against the contract".
* The API replays a company's event log per request; add snapshots if volumes grow large.
* No frontend changes were made.
