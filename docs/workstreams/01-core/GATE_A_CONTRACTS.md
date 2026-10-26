# Gate A — SecretaryAI shared architecture contracts (v1.0)

**Scope:** contract freeze for five workstreams. No provider calls, migrations, side effects, merges or deployments from this document. PR #74 and LYO remain unchanged.

## Canonical ownership and compatibility

- **Industry module interface:** `app.industry_modules.base.IndustryModule` (all ten methods) and `app.industry_modules.loader.get_module_for_company` are the canonical **configuration/industry knowledge** contract.
- `app.intelligence.industry_registry` and `app.intelligence.wholesale_distribution.module.WholesaleDistributionModule` are **legacy operational intelligence façades** for currently tested callers. Do not create new imports of the legacy registry. Do not delete or replace it until Chat 2/3/4 migrate consumers through a documented compatibility layer. They are not a second canonical industry configuration API.
- **Business entities and action policy:** `app.domain.contracts` dataclasses, including `ActionProposal`, `ActionDecision`, `ActionClass`, `DataPath`; **one server-side policy authority**: `app.domain.policy.PolicyEngine`. Additional per-module scoring/guardrails may reduce permissions, never authorize effects forbidden by the tenant policy.
- **Provider read adapter:** `UnifiedDataAdapter.get_all_customers()`, `get_orders_last_n_days(days)`, `get_inventory_qb()`, `get_inventory_merged()`, `get_emails(...)`. Source-native `QuickBooksAdapter.get_customers/get_invoices/get_inventory` are NOT methods of `UnifiedDataAdapter`. Chat 2 owns any adapter changes; callers must conform to the unified interface.
- **Briefing:** `app.domain.briefing_contract` defines the **new** typed `EvidenceRef`, `DigestEntry`, `RoleBriefing` contract, version `1.0`. It is read-only and does not bypass API-side tenant and permission checks. Call `RoleBriefing.project()` only after authenticating the user, resolving effective role AND record-level permissions, and reducing input entries to resources the viewer may access. `RoleBriefing.to_payload()` returns the stable JSON representation for web/mobile/desktop. The contract itself enforces tenant and coarse role scope, not customer/territory permissions.

## Ten industry-module methods — required (canonical)

`get_profile`, `get_entity_schema`, `get_kpi_definitions`, `get_scoring_config`, `get_causal_trees`, `get_workflow_definitions`, `get_pricing_config`, `get_compliance_calendar`, `get_terminology`, `get_benchmarks`.

Chat 2/3/4 implement domain-specific computations behind this interface; Chat 1 maintains loader and shared shape. New industries may not silently fall back to wholesale logic if configuration was explicitly set incorrectly.

## Data snapshot contract v1 (cross-team)

Represent individual source reads through existing `app.domain.data_result.DataResult` (source, fetched_at, confidence, is_ok/error, capability, correlation_id). Never treat `is_ok=False`, stale records, or missing permissions as numeric zero. Before persisting or cross-role sharing, attach company_id from verified authentication, provider/source record ID, UTC observed and retrieved timestamps, source synchronization status, normalization version, and source-quality state (verified/partial/unavailable). Chat 2 implements persisted normalization and lineage; Chat 1 owns schema migration approvals.

Missing source is a **visible data-quality incident**, not an empty-success report. Missing data must not produce a claim of operational health, an automatic PO, or an email.

**Implemented safety check on Chat 1 branch:** `compute_dashboard_payload` now raises `SnapshotSourceUnavailable` if any required account, inventory, or sales read fails rather than fabricating healthy zero metrics. No incomplete snapshot is written by the morning worker. Successful reads are labeled `source_read_status=completed` and `source_quality=partial` until Chat 2 proves provider-level lineage, freshness and completeness; a returned adapter payload alone is NOT a verified source. The initial on-demand dashboard request returns HTTP 503 when required data cannot be read. Chat 5 must display a dedicated unavailable-data state for HTTP 503 rather than rendering zero-valued default tiles. Cached snapshots must always show their `generated_at` age; handling of stale existing snapshots remains a Gate B acceptance item.

## Event v1 and deduplication

A scheduled insight/work item must have `company_id`, stable `event_type`, `entity_type`, `entity_id`, `detected_at` (UTC), `source_ids`, `correlation_id`, rule/model version, and a stable dedupe key over tenant + event type + entity + effective data/version window. Events are tenant-scoped and retry-safe. Changes from Chat 2/3/4 must be visible to Chat 1 before schema additions.

## Six-section digest v1

Always produce: `red_alerts`, `yellow_alerts`, `opportunities`, `scoreboard`, `todays_priorities`, `actions_taken`. Each entry must cite evidence, retain source freshness/quality, and have explicit allowed roles and tenant. Read-only role projection must not widen access. Owners and managers may view only company-authorized data; assigned reps get only their permitted accounts. Source-unavailable is explicit and cannot be misreported as resolved. An entry whose evidence is marked unavailable MUST have status `source_unavailable`; callers may never report it as open/resolved or suppress it as a healthy zero. JSON keys are `contract_version`, `company_id`, `audience_role`, `created_at`, `sections`; each section is an array of evidence-backed entries.

`actions_taken` requires execution receipts and independently checked external-provider outcomes. A queue acknowledgement is not confirmation of an external side effect.

## Actions and authority v1

Flow: `ActionProposal` → tenant PolicyEngine → (deny | draft/approval | allow) → immutable approval evidence → durable execution claim → provider effect → provider verification → immutable receipt/audit → outcome reconciliation. Never re-run an ambiguous provider side effect automatically. Executing a previously approved action must re-evaluate current policy, tenancy, amount limits, content integrity and allowed destination.

Autonomy tiers apply **per capability**, user-approved, no self-promotion:
1 = observe/inform; 2 = recommend/draft; 3 = selected routine actions inside configured rules; 4 = exception-led operation with all prohibitions still enforced. Legacy `AutonomyEngine` and `ActionEngine` cannot independently override the server policy; consolidation requires dedicated regression tests and incremental caller migration.

## Background execution topology

Required staging-only services: one FastAPI API, web frontend, Redis, a distinct Celery worker and a distinct Celery beat (scheduler). Dedicated worker must be able to invoke `tasks.overnight`, `tasks.morning_briefing`, `tasks.workflow_runner`, and `tasks.workflow_reaper`; beat owns the schedule. Redis must persist as configured; workers need health probes and runtime execution logs.

**Current read-only staging observation:** API, Web, Redis online, but no worker or beat service was listed. API container entrypoint starts Uvicorn, not Celery. Never claim scheduled briefings run until the two services are deployed and observed. All staging external sends/writes remain disabled; staging rollout requires explicit authorization.

## Dashboard data-integrity and side-effect boundary (Chat 1)

- \`compute_dashboard_payload\` uses the single \`total_qty\` supplied by Chat 2's merged inventory adapter; QB and warehouse quantities are two representations of the same stock and must not be summed. A missing or invalid authoritative quantity fails closed. Snapshot records distinguish successful top-level reads from unverified upstream source provenance.
- Dashboard API GET \`/summary\` returns \`snapshot_freshness\`, \`snapshot_age_seconds\`, \`snapshot_stale\`, \`source_read_status\`, and \`source_quality\`. Stale snapshots may be shown only with a clear timestamp/warning. Unknown timestamps or implausible future timestamps are not considered fresh.
- POST \`/refresh\` **only enqueues** \`tasks.dashboard_refresh.refresh_dashboard_snapshot\`: read-only company-scoped data fetch + snapshot persistence. It never enqueues \`tasks.morning_briefing.send_morning_briefing_all\`, which may involve an email delivery under separately authorized settings. Broker or persistence failure must return an error, not a false success.
- \`store_snapshot\` now raises \`SnapshotPersistenceUnavailable\` instead of swallowing write errors; owner-facing refresh may not claim completed storage if Supabase failed.
- **Chat 5 UI acceptance:** do not treat \`source_quality=partial\` as source-verified; show explicit stale/unknown warnings; never conflate \`refresh_queued\` with \`refreshed\`. Treat \`pending_actions=null\` and \`unread_emails=null\` as unavailable, not zero.
- **Chat 2 data acceptance:** prove adapter \`total_qty\` semantics, warehouse/QB non-additivity, location/ownership logic, weekly velocity presence/freshness, and fail-closed source unavailability before upgrading source-quality to verified.

## Immediate cross-team compatibility requests

- **Chat 2:** freeze provider adapter method signatures; reject missing source as empty success; add source lineage and amount/units normalization fixtures.
- **Chat 3:** in `backend/app/api/logistics.py`, pass `daily_sales_history` and `lead_time_history` matching `ReorderEngine.build_reorder_queue`; add API-through-engine tests and verify unit normalization. Do not change the shared domain contract.
- **Chat 4:** expose new capabilities using canonical industry configuration; no direct action execution or unilateral DB migration.
- **Chat 5:** consume `RoleBriefing` six-section projection via a versioned read endpoint once Chat 1 wires it; treat unavailable data as degraded (never show "zero issues"). E2E must include RBAC, duplicate retries, stale data and provider failures.

## Gate A acceptance

Contract and compatibility tests must pass on exact feature-branch tip; architecture specification must be reviewed with Chat 2–5, and no shared-interface changes may be merged until the relevant owners adopt v1. This contract freeze does not represent live end-to-end verification, safe autonomous execution, or permission to deploy.


## Security release blockers and mitigations (2026-10-26)

- **Quarantined in Chat 1 PR #75:** legacy POST /api/dashboard/emails/send-reply unconditionally returns HTTP 409 without resolving a provider or emitting email. Browser confirmation cannot bypass tenant policy, durable approval or idempotent execution. It stays disabled pending separately verified gateway.
- **Provider failure truthful:** POST /api/dashboard/emails/{email_id}/mark-read now raises HTTP 503 on provider exceptions. OPEN: Chat 2 must change UnifiedDataAdapter.mark_email_read to reject missing connector rather than silently succeeding.
- **Current and prior sales failures truthful:** GET /api/dashboard/sales raises HTTP 503 for failed source reads instead of fabricating $0. Dataclass/dict dates and amounts use shared validation accessors. OPEN: Chat 2 must supply independent source-completeness/freshness evidence.
- **PO draft authorization:** POST /api/actions/draft-po requires explicit server-side draft_purchase_orders permission: owner, manager, back_office only; sales_rep/viewer denied. Positive optional quantities enforced. OPEN: tenant-owned inventory/vendor validation before draft-to-execution promotion.
- **OPEN:** approve/{draft_id} legacy provider sends and PO dispatches are not certified for unattended external effects. Mandatory future gateway needs durable execution receipt, policy recheck, idempotency and a test-only allowlist.
- **OPEN:** app.api.inbox partial read and mark-read success semantics require per-source health metadata and fail-closed errors. UI cannot infer swallowed backend exceptions.


## Gate A durable approval convergence (Chat 1)

- `POST /api/actions/approve/{draft_id}` no longer has an active fallback from human approval into inline email delivery or PO dispatch. When `SECRETARYAI_DURABLE_APPROVAL_ENABLED` is not `1`, the endpoint denies with HTTP 409 *before mutation*. A healthy durable transport remains a prerequisite for even queueing a reviewed email.
- The enabled branch supports **email draft approval + atomic durable enqueue only** via `PostgresDurableGateway`. Purchase-order dispatch stays blocked until a separately reviewed durable execution claim, vendor/line-item validation and provider receipt exist.
- The private execution RPC must return a valid `execution_id` plus `queued=true`; `queued=false` is accepted only with `already_processed=true`. API responses distinguish `execution_status=queued` from `already_processed` and suppress second approval-audit attempts. Neither outcome proves actual provider delivery.
- `PolicyEngine.evaluate` now fails closed for COMMIT/DESTRUCTIVE when either authoritative `policy_rules` or tenant `company_features` cannot be loaded. READ/DRAFT may continue with conservative defaults. The API converts a policy-source failure to deny rather than allowing a default approval threshold.
- Synthetic tests exercise the disabled path, policy-store outages, queue-only behavior, PO refusal, duplicate approval, ambiguous ledger response and protected transport. No external email or PO was sent.
- **Remaining release blockers:** independent verification of the SQL RPC and immutable receipt schema; comprehensive provider callback/reconciliation tests; no automatic retry of unknown outcomes; approved worker/beat deployment in staging after separate authorization; per-company scopes and app data-source completeness.


## Gate A read-only executive briefing API (initial partial integration)

- New `GET /api/briefing/today` is registered under the paid API guard and requires an authenticated `owner` or `manager` with `view_financials` permission. Other roles receive HTTP 403; company_id is from the trusted JWT, not the request body.
- Source: the existing `report_snapshots` tenant-filtered `dashboard_summary` cache only. The endpoint **never connects to Gmail, QuickBooks or a provider and never creates or executes an action**.
- The response uses `RoleBriefing.to_payload()` v1.0 with six canonical sections and evidence references. `source_coverage=dashboard_snapshot_only`, `complete_digest=false` and `source_quality=partial` disclose the intentionally incomplete scope. Opportunities and actions_taken remain empty until verified evidence, not because the company necessarily had none.
- Missing/invalid/stale snapshots, unverified read status, invalid figures and aggregate inconsistencies return HTTP 503 rather than misrepresenting an operational all-clear. Currently supports company totals and critical/low inventory counts, not record-level account data.
- Chat 5 may consume this endpoint as an additional executive prototype but must prominently display the partial-coverage disclaimer and 503 unavailable state. Do NOT substitute it as the validated full morning briefing until real provider evidence, account-level RBAC, worker execution receipts and proof of source coverage are integrated.
- Before enabling sales-rep or logistics views, Chat 1/2/3 must agree on record-level ownership, territory, permission checks and per-source reliability. Client role hiding is not authorization.
