# Workstream 04 — isolated domain engines, integration boundaries

Technical contract proposal, not an approved API or release claim. All sample
data and tests use synthetic identifiers. No customer, employee or supplier
records are included in this public repository.

## Executable domain calculations

| Capability | Owned pure engine | Output / decision control |
|---|---|---|
| Compliance #7 | `compliance/evidence_gate.py`, `compliance/rules_engine.py` | Document statuses, dated rule references, blockers; never shipping authorization |
| Brand #10 | `brands/scorecards.py` | Case growth, account reach/retention, margin, stock cover, supplier fill/on-time/damage with configurable score weights; advisory |
| New brand #11 | `products/concepts.py` | Demand whitespace, unit economics, cannibalization, payback and observed launch KPIs; scenarios only |
| Portfolio #12 | `portfolio/rationalization.py` | Contribution, stock coverage, stockout-with-demand supply reviews, capital tied up and dependency-safe rationalization candidates; never deletes |
| Pricing #13 | `pricing/waterfall.py` | Itemized landed cost, net realization, gross/contribution margin, channel price scenario, exception flags and promotion ROI |
| People/HR #15 | `hr/onboarding.py` | Opaque staff onboarding/training/license renewal statuses and scoped briefing; no employment decisions |
| Dynamic pricing #23 | `wholesale_distribution/dynamic_pricing.py` | Observational confidence guard, modeled pass-through using real costs and human price-approval flags |

HR task projections retain an explicit tenant ID and underlying source
record/revision alongside opaque employee refs. The private read projection
rejects mixed-tenant task lists *before* counting or revealing tasks, including
when two tenants reuse the same employee reference. It also rejects invalid
or provenance-inconsistent task statuses. Gate A must authenticate the HR role
and per-employee grant server-side; an `HRTaskAccess` value is never an
authorization credential by itself. No actual HR records or decisions are
stored or published by this workstream.

Supplier score composites accept `SupplierScoreWeights` rather than
hard-coded operational priorities. Chat 1's canonical ten-method industry
module should eventually provide reviewed weight configuration through
`get_scoring_config`. The pure engine validates nonnegative weights
that sum to one, and suppresses the rankable composite if source revision
metadata is missing. Undefined brand penetration/repeat/return percentages
remain null with explicit data-gap codes. Chat 3 must reconcile supplier
ordered/received/damaged units to a consistent PO-receipt cohort before a
real score is considered verified. No supplier change can be executed here.

New-brand launch tracking now requires a caller-established `verified`
source-quality value **and** a source record ID/revision before displaying
realized volume, contribution margin or repeat-account percentages. Partial,
unavailable or missing-revision observations withhold these percentages and
report source gaps; no active accounts produces an explicit denominator gap.
The quality value is only a trusted adapter's claim and still requires Chat 1
and Chat 2 source-lineage/freshness validation before the metrics may appear
in a user-facing briefing. Portfolio inventory-age inputs reject Boolean
values that Python otherwise accepts as integers.

The legacy compliance expiration scanner previously removed recorded overdue
permits, COLAs, licenses, registrations and distributor agreements once their
date passed. It now retains those records as critical daily review alerts and
keeps states without a currently active license visible as critical in the
digest. This is a **recorded-date signal**, not a regulator-verified legal
finding. No renewal submission, shipment approval or communication occurs.

Dynamic-price suggestions fail closed on inadequate samples, nonnegative
observational elasticity (possible seasonality/promotion/stock-out confounding),
and invalid or overflowing price/volume projections. These checks are
mathematical safeguards, **not** proof of causal price elasticity. Chat 2 must
provide independent account/SKU transaction lineage, comparable pre/post
observation windows and event revisions before any real recommendation can be
considered operationally grounded. Pricing changes always require review.
When raw price events include a tenant, segment or product-category key, the
pure engine excludes mismatches before fitting elasticity. It supports paired
`period_before_days` / `period_after_days` to compare daily rates when windows
differ, rejecting unpaired or invalid periods. Generated estimates and draft
price/segment scenarios now serialize the tenant ID and `unverified_advisory`
status. Legacy unscoped observations can still be modeled for offline tests;
**they are not verified provider evidence**. Cost pass-through blends reject
duplicate or cross-tenant segments and missing positive sales-volume weights,
so the engine cannot silently claim a zero-percent blend with no volume basis.

## Chat 5 opportunity classification and drilldowns

The owned `growth/opportunity_taxonomy.py` is a pure projection helper for
Chat 5's requested department views. It supports a **closed vocabulary** of
signal codes, rather than trusting free-form model-generated event types.

| Department | Example signals | Briefing section / next-action category |
|---|---|---|
| Compliance | `compliance_renewal_due`, `compliance_source_stale` | Red/yellow alerts; review dated evidence |
| Brand | `brand_decline`, `supplier_service_decline`, `brand_growth` | Yellow alert or scoreboard; inspect source-backed scorecard |
| Product development | `concept_cannibalization_risk`, `launch_underperformance` | Yellow alerts; validate concept and observed launch cohorts |
| Portfolio | `portfolio_stock_at_risk`, `portfolio_stockout_with_demand`, `portfolio_contribution_gap` | Yellow/red/opportunity; dependency-safe SKU and supply review |
| Pricing | `margin_breach`, `promotion_roi_negative` | Red/yellow alerts; audit waterfall/baseline |
| Dynamic pricing | `price_exception`, `insufficient_elasticity` | Yellow alerts; human price approval or observation review |
| HR | `hr_training_due` | Restricted red alert; private HR task review only |

`GrowthSignal` carries tenant, opaque entity ref, exact signal code, metric,
source quality, record ID, revision and time. `PolicyScope` is a *server-validated
handoff requirement*, **not** an authentication credential. The classifier
rejects wrong tenant, disallowed role, missing entity grant or missing policy
decision ID. It removes metric values when evidence is stale, partial or
unavailable, retaining red-alert urgency instead of fabricating a healthy zero.
The classifier now emits Gate A `DigestEntry`-compatible statuses
(`needs_review` / `source_unavailable`) instead of an independent review label.
Before construction of a canonical `EvidenceRef` / `DigestEntry`, Chat 1 and
Chat 2 must still supply and validate the source system, fetched-at timestamp,
confidence, authenticated role/record scope and approved evidence quality.
Source revision here is not itself enough to claim `verified`. Unavailable
or stale evidence must create an `EvidenceRef` with quality `unavailable` if
the receiving entry has status `source_unavailable`; mixed quality/status
must fail Gate A validation rather than be silently coerced.
All outputs have `approval_required=True` and `execution_claimed=False`.
The `actions_taken` briefing section is intentionally never generated here:
that requires verified external receipts under Chat 1's authority.

The current platform RBAC has no dedicated HR role. Accordingly `hr_admin`
is only a proposed technical role label and must remain inaccessible until
Chat 1 authorizes its server-side policy mapping. General owners/managers do
not receive HR detail implicitly.

## Internal interface rule

Each engine accepts an immutable snapshot scoped to `company_id`. The caller
must validate the originating tenant, record identifiers, source revision,
currency/unit conversions, read permissions and audit correlation via Chat 1
contracts. An ID string or `documented_inputs=True` is **not** external
verification. Missing denominators remain `None` or explicit data gaps.

Suggested Gate A response envelope (not registered here):

```json
{
  "company_id": "<tenant>",
  "capability": "<capability_id>",
  "mode": "advisory",
  "as_of": "<UTC_timestamp>",
  "source_revisions": [],
  "data_quality": "unverified",
  "metrics": {},
  "draft_actions": [],
  "approval_required": true,
  "audit_id": null
}
```

No route is registered and no datastore, connector, common schema, worker,
financial posting, outbound message or personnel record is changed.

## Source integration handoffs

- **Chat 1:** canonical tenant/role/action policy and evidence envelopes,
  authenticated approval audit, HR private read permissions, router registration.
- **Chat 2:** observed brand/SKU/account and realized-price transactions,
  comparable periods, promotion/return and stock-out adjustments.
- **Chat 3:** audited landed-cost components, per-unit allocations,
  supplier PO receipt/fill/on-time/quality evidence, lot inventory aging.
- **Chat 2 + Chat 3:** independently verify the sales-velocity and available-
  stock sources for a `stockout_with_observed_demand` signal. A SKU's
  generic source reference is not proof of both inputs or of inventory
  completeness; the stockout alert remains advisory pending lineage checks.
- **Chat 5:** approved response mapping and role-specific UI for dashboards,
  review queues, scenario comparison and controlled approval submission.

Never interpret the static compliance rule matrix, caller-provided source
metadata or scenario numbers as regulatory verification. The legal review,
price posting, promotion eligibility, HR permissions, effective dates,
idempotency, tenant policy and persistent audit belong to integration.

## Acceptance evidence and remaining release blockers

Unit tests cover positive/negative scenarios, source gaps, invalid numeric
inputs, division-by-zero cases, tenant mismatches, inventory dependencies,
cannibalization, HR scoping and draft-only outcomes. Run together with
existing compliance and pricing tests using the Python test environment.

Not accepted until: Gate A contracts, source-backed adapters, API/RBAC/RLS
integration, real staging E2E, audit durability, browser/role UX tests,
human regulatory/pricing/HR approvals and CI/CodeQL exact-commit checks.
