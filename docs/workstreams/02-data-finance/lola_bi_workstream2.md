# Lion BI / Lola — Workstream 2: unified business intelligence (read-only)

Base: `program/secretaryai-integration-20261026` @ `10af839`. Staging-line work only.
No deployment, merge, migration, provider call, email or accounting write was made.
All tests use invented data (`backend/tests/test_lola_bi/synthetic.py`).

## 1. What was built

| Path | Purpose |
| --- | --- |
| `backend/app/connectors/qb_bi_reads.py` | New **read-only** QuickBooks projections: all open invoices, payments with invoice allocations, open bills, bank book balances, home currency, Profit & Loss report (QBO), product catalog with brand hierarchy. Strict validation; no write calls (a test asserts this). |
| `backend/app/lola_bi/contracts.py` | `SourceSnapshot` (tenant, provider, status, coverage, window, quality, fetched_at, max_age), `Finding` (`verified` vs `hypothesis`), the `lola.bi.v1` result envelope. |
| `backend/app/lola_bi/money.py` | Exact `Decimal` money; per-currency totals; FX conversion only with a named, timestamped rate. |
| `backend/app/lola_bi/scope.py` | `BIAccessScope`, capability matrix, `BIScopeProvider` protocol, default `AssignmentScopeProvider`. |
| `backend/app/lola_bi/gateway.py` | `BusinessDataGateway`: the only data path. Tenant check, per-source capability gate, record scoping, cost redaction, tenant-keyed cache, sanitized error classification. |
| `backend/app/lola_bi/{finance,receivables,payables,pricing,sales,investigate,entities,common}.py` | Department engines (pure, deterministic). |
| `backend/app/lola_bi/tools.py` | 26 tools, strict input schemas, `LolaBIService.tool_definitions()` / `invoke()`. |
| `backend/app/api/lola_bi.py` | `GET /api/lola/bi/tools`, `POST /api/lola/bi/tools/{name}` (paid router). |

Only two existing files changed: `backend/app/main.py` (one additive `include_router`) and
`backend/tests/test_paywall.py` (classify `/api/lola` as paid). No WS1 auth, chat/orchestration,
approval, policy, schema or migration file was modified.

## 2. Capabilities (one Lola interface, departmental functions — not personas)

| Department | Tool | Answers |
| --- | --- | --- |
| Franklin — Finance | `finance_cash_position` | Current cash (QB bank **book** balances, per currency) |
| | `finance_profit_and_loss` | P&L totals, subtotal reconciliation, prior-period change (QBO only) |
| | `finance_cash_flow_projection` | 1–13 week due-date schedule (AR in, AP out). **Not a forecast** |
| | `finance_profitability_by_brand` | Estimated GP/margin by brand (current item cost) |
| | `finance_anomalies` | Duplicate invoice numbers/bills, credit balances, negative bank, outliers |
| Wildora — AR | `ar_receivables_summary` | What customers owe + aging (reuses `intelligence.receivables`) |
| | `ar_overdue_customers`, `ar_upcoming_due_invoices` | Who is overdue; what comes due soon |
| | `ar_collection_priorities` | Ranked, explained collection list (+ last payment, correspondence count) |
| | `ar_payment_reconciliation` | Payment allocation balance; invoice total − payments = balance |
| Accounts Payable | `ap_open_bills`, `ap_duplicate_bills` | Supplier bills, due dates, duplicates |
| | `ap_payment_proposal` | Proposed payment run within cash; returns `ActionProposal`-shaped dicts, `submitted: false` |
| Raul — Pricing | `pricing_product_margins` | Highest/lowest margin products |
| | `pricing_price_discrepancies` | Below-cost sales, off-median and off-list prices |
| | `pricing_billbacks_and_promotions` | Returns **unavailable** + what data is needed |
| Sales & CRM | `sales_period_comparison` | Month-to-date vs same days last year / last month |
| | `sales_declining_accounts`, `sales_underperforming_products` | Declining distributors; weak SKUs (incl. no sales with stock) |
| | `sales_rep_attention`, `sales_territory_drivers` | Reps needing attention; territory decline decomposition (reconciles exactly) |
| | `sales_projection` | Run-rate month projection (labelled uncalibrated) |
| | `crm_customer_profile` | One customer across AR, sales, products, territory, rep, mail metadata |
| Cross-system | `crm_find_entity` | Customer/product/vendor matching; ambiguity is returned, never guessed |
| | `investigate_account_decline` | "Why is this distributor ordering less?" — verified facts vs hypotheses |
| | `bi_data_coverage` | Live / stale / failed / partial / unverified / not-connected per source |

Rules applied everywhere: missing ≠ zero; failed reads → `status: unavailable` with the failing
source named; currencies never mixed without a sourced FX rate; "sales" = gross billed invoices
(not recognized revenue); every figure lists its sources and freshness.

## 3. Data-source audit (state of connectors for these questions)

| Data | Source / path | State |
| --- | --- | --- |
| Customers, balances | QBO `Customer` / Conductor customers | Live (QBO provider-observed; Desktop unverified mapping) |
| Invoices + lines (date window) | QBO `Invoice` / Conductor invoices | Live, window-bounded |
| **All open invoices** | QBO `Balance > '0'`, Conductor `paymentStatus=not_paid` | New. QBO live; Desktop filter name unverified |
| Payments + allocations | QBO `Payment` / Conductor `receive-payments` | New. QBO live; Desktop unverified |
| Open bills (AP) | QBO `Bill` / Conductor `bills` | New. QBO live; Desktop unverified |
| Bank balances | QBO `Account` (Bank) / Conductor `accounts` | New. **Book** balance only, not bank statement |
| Home currency | QBO `Preferences` / tenant settings (Desktop) | New |
| P&L | QBO `reports/ProfitAndLoss` | New. **Not supported on Desktop** |
| Inventory | `get_inventory_merged()` | Partial (existing WS2 lineage caveats) |
| Rep/territory assignments | `accounts.assigned_rep_id`, `accounts.territory` | Tenant-declared; often unpopulated |
| Correspondence | Gmail/Outlook `get_emails` | Metadata sample only (bounded, no bodies) |
| Credit memos, vendor credits, sales orders/estimates, GL detail, payroll/tax, billbacks, promotions, FX rates, bank feeds | — | **Not connected**; reported as such, never estimated |

## 4. Permission model

| Role | Record scope | BI capabilities |
| --- | --- | --- |
| owner, manager | company | all (incl. costs/margins, cash, P&L, payment proposals, team views) |
| back_office | company | AR, AP (read), company sales, CRM, inventory, mailbox metadata, coverage. **No** cash, P&L, costs/margins or payment proposals |
| sales_rep | assigned accounts only (`accounts.assigned_rep_id = sub`) | own sales, own AR, CRM, inventory. No costs, mailbox, team or company finance |
| viewer / unknown | — | none (403) |

* Scope is resolved server-side from the verified JWT + `accounts` rows; JWT `bi_capabilities`
  can only **narrow**. Assignment outage → 503, never company-wide.
* Out-of-scope sources are not read at all (`DENIED` snapshot, zero provider calls — tested).
* Customer-bound records are filtered on every return, including cache hits; product cost
  is removed for non-cost roles. Cache entries are tenant-keyed and re-checked.
* The HTTP layer resolves scope **before** building the adapter, so denied callers never
  trigger credential decryption or OAuth refresh (tested).

## 5. Interfaces for Workstream 1

```python
from app.lola_bi import AssignmentScopeProvider, BusinessDataGateway, LolaBIService

scope = await provider.resolve(verified_jwt_payload)      # or WS1's own BIScopeProvider
gateway = BusinessDataGateway(unified_adapter, scope, db=service_db)
svc = LolaBIService(gateway, timezone=user_tz)
tools = svc.tool_definitions()        # Anthropic tool format, pre-filtered for this caller
result = await svc.invoke(name, args) # lola.bi.v1 envelope
```

Envelope keys: `contract_version, tool, department, tenant_id, generated_at, status
(ok|partial|unavailable|denied|invalid_request), answer, findings[{statement, basis, evidence,
sources}], sources[{source, provider, status, quality, coverage, window, fetched_at, age_seconds,
stale, record_count, warnings, error}], data_quality.missing_or_limited, warnings, scope,
read_only: true, actions_executed: false`. Money is serialized as exact decimal strings.

Requests to WS1:

1. **Wire chat to these tools.** `backend/app/api/chat.py` `send_message` is guarded only by
   `view_own_accounts` and `_fetch_data_for_intent` reads company-wide customers/invoices for
   any caller, so a sales rep's chat context includes every account. Replace those reads with
   `LolaBIService` tool calls (or apply `BIAccessScope` filtering) — not changed here (WS1-owned).
2. `backend/app/api/accounts.py` `_owned_by_sales_rep` reads `assigned_rep_id` from QuickBooks
   `Customer` objects, which never carry it, so reps currently see zero accounts (fails closed,
   not a leak). Join against `accounts.assigned_rep_id` like `AssignmentScopeProvider` does.
3. Route `ap_payment_proposal.action_proposals_for_approval` through `PolicyEngine` + durable
   approval if AP payments are ever enabled (COMMIT; this workstream never submits them).
4. Collection emails: drafts must go through the approval gateway; tools return context only.
5. Approve or replace `AssignmentScopeProvider` with the policy-authority-backed resolver
   (override `app.api.lola_bi.get_scope_provider`).
6. Provide an approved FX-rate source if multi-currency combined totals are wanted
   (`LolaBIService(fx_rates=...)` accepts `FxRate` with source + timestamp).

## 6. Blocked by external provider acceptance

* QBO **sandbox** evidence for the new reads (`Balance > '0'` filters, `Payment.LinkedTxn`,
  `Bill`, `Account` bank balances, `Preferences.CurrencyPrefs`, `reports/ProfitAndLoss`
  header/period semantics, multicurrency fields). Parsers are tested against invented fixtures only.
* QuickBooks Desktop / Conductor: field names for `paymentStatus`, `receive-payments`,
  `appliedToTransactions`, `bills.amountDue`, `accounts.currentBalance` are **unverified** and
  reported as `quality: unverified_mapping`; malformed responses fail closed. Conductor offers no
  P&L report → `not_supported`.
* Gmail/Outlook consented read scopes and historical completeness (sample-only today).
* Real rep/territory assignments in `accounts` for each tenant.

## 7. Tests

`backend/tests/test_lola_bi/` — 120 tests: exact reconciliation (aging = Σ balances, cash
schedule roll-forward, payment allocation, invoice total − payments = balance, territory
decomposition, P&L subtotals), missing/malformed data, identical vs conflicting duplicates,
partial connector results, failed OAuth refresh through the real QBO client with a mocked
network, error classification, inclusive date windows and leap-year shifts, future/oversized
windows, multi-currency and FX, cross-tenant connector/cache/row isolation, sales-rep
restriction across every customer-bound tool, no reads for denied tools, HTTP ordering
(no adapter for denied callers), and a real-clock regression.

Full backend suite with a local Redis: **2818 passed, 1 skipped**. Without Redis the 70
pre-existing revocation-store tests fail exactly as noted in PR #99.
