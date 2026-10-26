# Workstream 04 — isolated domain engines, integration boundaries

Technical contract proposal, not an approved API or release claim. All sample
data and tests use synthetic identifiers. No customer, employee or supplier
records are included in this public repository.

## Executable domain calculations

| Capability | Owned pure engine | Output / decision control |
|---|---|---|
| Compliance #7 | `compliance/evidence_gate.py`, `compliance/rules_engine.py` | Document statuses, dated rule references, blockers; never shipping authorization |
| Brand #10 | `brands/scorecards.py` | Case growth, account reach/retention, margin, stock cover, supplier fill/on-time/damage; advisory |
| New brand #11 | `products/concepts.py` | Demand whitespace, unit economics, cannibalization, payback and observed launch KPIs; scenarios only |
| Portfolio #12 | `portfolio/rationalization.py` | Contribution, stock coverage, capital tied up and dependency-safe rationalization candidates; never deletes |
| Pricing #13 | `pricing/waterfall.py` | Itemized landed cost, net realization, gross/contribution margin, channel price scenario, exception flags and promotion ROI |
| People/HR #15 | `hr/onboarding.py` | Opaque staff onboarding/training/license renewal statuses and scoped briefing; no employment decisions |
| Dynamic pricing #23 | `wholesale_distribution/dynamic_pricing.py` | Observational confidence guard, modeled pass-through using real costs and human price-approval flags |

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
