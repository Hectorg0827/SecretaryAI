# Workstream 04: audit and Gate A handoff

Baseline: `d79284a40db6e069aa3b39cb00376c95060c7983`. This public
technical document contains no tenant data, real prices, supplier strategy or secrets.

## Dependency order

1. Compliance (#7): present registries and checklist, absent verified rule revision/approval chain.
2. Pricing and cost (#13): requires Chat 3 audited landed costs and invoices.
3. Dynamic pricing (#23): requires #13, Chat 2 observed sales and regulatory pricing constraints.
4. Portfolio (#12): depends on sales, inventory, contribution and SKU dependencies.
5. Brand (#10): depends on account/brand trends and supplier fulfillment.
6. New brand (#11): depends on portfolio baseline, financial assumptions and launch cohorts.
7. People/HR (#15): can proceed independently after Gate A identity/RBAC and private storage contracts.

## Concrete code findings

- `backend/app/intelligence/compliance/state_matrix.py`: 51 static jurisdiction rows, approximate fees and default `last_reviewed` metadata; not per-rule, revision-specific legal evidence.
- `backend/app/intelligence/compliance/rules_engine.py`: checklist formerly treated any registered non-expired document as valid even when revoked or pending, and accepted negative quantities. Narrow safety checks are added here. `approved=True` still means *only* a checklist pass, not authorization.
- `backend/app/intelligence/compliance/compliance_module.py`: in-memory registry hydrated on each request; `get_overall_status()` may return `ok` for an empty registry.
- `backend/app/api/compliance.py`: entity loader suppresses some DB failures; audit-log insert can fail without blocking an `approved` response. API uses the ambiguous checklist `approved` field.
- `backend/app/api/deps.py`: pooled service-role Supabase client requires explicit tenant predicates and authorization in every new path. Do not rely on RLS alone for service-role queries.
- `frontend/src/components/compliance/ShipmentChecker.tsx`: labels a checklist pass as an approved compliant shipment. **Chat 5 owns the UX change; no cross-lane edits.**
- `frontend/src/lib/api.ts`: brand-registration client expects an array, while its endpoint returns an object envelope. **Chat 5 contract repair.**
- `backend/app/intelligence/wholesale_distribution/dynamic_pricing.py`: instantiated by `module.py`, with no proven observed-sales or audited-landed-cost integration. This change adds safe input validation, lower-confidence holds and actual-cost pass-through scenario math.
- `backend/app/auth/rbac.py`: HR-specific access scopes do not exist; **Chat 1 policy owner**.

## Gate A contract requests: no shared-code edits

All data snapshots must carry `company_id`, source system and record identifier,
immutable revision, observed/fetched timestamps, verification status and
correlation ID. Errors and missing evidence must not appear as empty success.

1. **RegulatoryRuleRevision**: jurisdiction, product type, topic, source URL, source revision, effective/superseded dates, authenticated reviewer ID/time and audit reference. Draft until qualified review.
2. **ComplianceClearanceDecision**: distinct from `rules_check_passed`; tenant, SKU, destination, shipment scope, exact rule revision set, authenticated approver, expiry, policy tier and durable audit ID. Default pending; caller-supplied reviewer text is not proof.
3. **AuditedLandedCostSnapshot** (Chat 3): tenant, SKU, lot, case/unit basis, currency, component allocations, document references, cost revision and verification status.
4. **ObservedSalesAndPriceSeries** (Chat 2): tenant, account/segment, SKU, comparable dates/units, actual net realized price, quantity, promotions, returns, stock-outs and source revision. No synthetic acceptance data.
5. **PricingActionDraft**: cost/sales/regulatory evidence versions, proposed effective date, price, margin waterfall, exception flags, confidence, human approval state, idempotency key and audit reference. No price writes in draft.
6. **HRAccessScope** (Chat 1): distinct read/manage permissions by tenant and authorized staff scope, privacy-safe aggregate views and redaction; no autonomous employment decisions.

Chat 1 owns shared schemas, policy and API route registration. Chat 2 owns
observed commercial sources; Chat 3 owns cost evidence; Chat 5 owns UI. This
workstream will integrate after accepted contract handoffs, not guess fields.

## Seven capability acceptance gates

| Capability | Deterministic tests | Operational proof required |
|---|---|---|
| Compliance | Negative quantity/revoked status blocks; missing, stale and wrong-tenant sources never clear | Verified regulatory evidence, human approval and fail-closed audit |
| Brand | Sales and distribution KPIs, supplier fill/on-time metrics with missing-data flags | Actual order, account and supplier data |
| New brand | Scenario returns, cannibalization and launch cohort calculations | Approved assumptions and actual launch observations |
| Portfolio | Contribution/velocity/rationalization constraints; dependent SKU guard | Sales, inventory, stock aging and supplier obligations |
| Pricing/cost | Reconciled landed-cost waterfall, price exception and promotion ROI | Audited cost, invoices and regulatory posting controls |
| People/HR | Role denial, onboarding due dates, PII redaction | Tenant policy, private store and human review |
| Dynamic pricing | Nonfinite/zero inputs reject; insufficient sample holds; real cost margin | Observed elasticity, legal constraints and audited price approval |

## Test command and limits

From `backend/` with pytest installed:

`python -m pytest --noconftest -p no:cacheprovider tests/test_compliance_evidence_gate.py tests/test_growth_dynamic_pricing_safety.py tests/test_wholesale_distribution/test_dynamic_pricing.py tests/test_compliance_rules.py tests/test_compliance_module.py tests/test_compliance_alerts.py tests/test_compliance_models.py -q`

This is **pure-domain unit evidence** only. It does not prove API/RBAC/RLS,
database persistence, live third-party integrations or staging E2E. Normal CI
must run with its full dependency set before integration.

Local result (2026-10-26): **154 passed**, with two pytest configuration
warnings because this minimal runner intentionally omitted the unrelated
pytest-asyncio plugin and application-wide fixtures.

## Role UX handoff

- Compliance officer: dated legal references, renewal signals, gaps and `Request review`; never checklist-as-clearance.
- Finance/pricing manager: per-unit cost, price waterfall, scenario provenance, exceptions and `Request approval`.
- Brand and portfolio owner: distribution, velocity, margin, aged stock, supplier scorecards and draft actions.
- Product manager: explicit forecast assumptions, cannibalization and launch tracking, no automatic launch.
- HR administrator: private onboarding/training/compliance tasks; sensitive details invisible to unapproved roles.
- No automated messages, price updates, HR decisions or supplier changes from these engines.
