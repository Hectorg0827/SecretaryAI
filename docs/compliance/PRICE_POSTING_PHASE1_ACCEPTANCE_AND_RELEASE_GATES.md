# Lion BI Compliance — Price Posting Phase 1: acceptance and release gates

**Status:** Code-level release candidate only. **Not deployed; NOT legally certified.**
**Scope:** Synthetic pilot for NY wholesale, NY retail, NJ eCPL and FL malt-notice candidates.
**Approved architecture:** QBO and QBD selected per tenant; no provider writes; human verification of regulatory rules.
**Mandatory rule:** A green test suite is not a regulator filing, human legal approval, verified license, current posting deadline, or production authorization.

## 1. Draft patch stack (do not merge individually without coordinated review)

| Patch | PR | What it provides |
| --- | --- | --- |
| 1 | #163 | Safe scheduler behavior |
| 2 | #164 | No destructive compliance deletes |
| 3 | #165 | Policy and atomic record/audit |
| 4 | #167 | Versioned NY/NJ/FL price-posting **candidate** rules and read-only preview |
| 5 | #169 | Owner/manager source attestation, tenant audit and UI |
| 6 | #170 | Tenant-scoped read-only price/cost/product/license reconciliation |
| 7 | #171 | Immutable internal preparation and human-reported unverified reference ledger |
| 8 | #173 | Exact API capability and path verification, enforced again by atomic PostgreSQL audit trigger |
| 9 | #174 | Unverified, stored QBO/QBD item price observation comparison using exact decimal and explicit unit basis; no connector calls |

The PRs are **stacked**. Merge order, schema migration order and final commit SHAs must
be reviewed together. Do not describe an ancestor PR as production-ready while a
security fix exists only on a descendant. Explicit-allow hardening in #171
requires a **named matching tenant policy rule**, not a default or missing rule ID.
Patch 8 additionally requires `action_class=commit`, `effect=allow`,
`is_active=true`, `capabilities_match` including the exact regulated capability
and `paths_match` including `api`. The database rechecks the same criteria
at atomic audit insertion; a general `allow commits` rule is insufficient.
The three capabilities are distinct:
`compliance.rule_review`, `compliance.price_preparation`, and
`compliance.price_reference`. No automatic policy change is made.

## 2. Features supported by the synthetic pilot

- Display latest **candidate** jurisdictional version and underlying official authority URL.
- Show an unverified state (not a green clearance) for missing deadlines or official templates.
- Recheck the authenticated tenant/role on each API request; owner/manager only may record evidence.
- Fail closed on source outages, partial or ambiguous reads, cross-tenant IDs, duplicate IDs,
  missing source files, stale versions, nonmatching jurisdictions or invalid effective periods.
- Preserve a server-generated, append-only snapshot of the **stored** price-book revision.
- Record a separate, atomic audit event for each permitted source attestation, case creation
  and human-reported external reference. Denied writes are audited or rejected.
- Reject unreviewed source versions, missing tenant policy matches and re-used customer IDs.
- Show **human_reported_unverified**, never `filed`, `accepted` or `confirmed`,
  for an externally supplied document link or claimed agency message.
- Keep **agency_submission_enabled=false**, `submission_permitted=false`,
  `ready_to_file=false` and `price_publication_permitted=false`.
- Never call a QBO/QBD API or generate an agency-ready template in Phase 1.

### Unverified accounting observation comparisons (Patch 9)
The read-only endpoint `/api/insights/price-posting-accounting/compare`
accepts only existing price-book UUIDs, never caller-supplied accounting prices.
Its source is `pricing_accounting_price_observations`, an append-only
**unverified observation** table with service-role SELECT only.
There is intentionally **no insertion API, sync job or live QuickBooks call**
in this phase. Test data may be inserted only in disposable PostgreSQL CI.
In staging, the source should be unpopulated until a separately approved
read-only adapter capture produces a verifiable tenant-scoped source revision.

A QuickBooks InventoryItem.unit_price is not automatically per case, not
guaranteed current, and not necessarily jurisdiction/customer/channel-specific.
For `single_unit` records the comparator requires an explicit
`units_per_case`, and it still marks case-pack conversion **unverified**;
`case` records compare directly, `unknown` cannot be compared.
It uses `Decimal` arithmetic, no guessed currency exchange rate,
no matching by SKU alone, no auto-selection of multiple observations,
and no implied matching of QBO/QBD rates to an agency price schedule.
A numerically equal price is marked **numeric_match_unverified**,
*not* an independently reconciled accounting amount.
The blockers remain visible even with a numeric match.

Before this can become a verified accounting gate, a separately approved
connector-read-only capture process must record immutable provider item ID,
tenant, adapter identity, source revision, observed time and source document
without logging secrets or creating any price/accounting write. An independent
accountant must verify item identity, units/case, currency, effective pricing
period, distribution scope and associated freight/warehouse cost coverage.
No user or automation can convert these observations to filing clearance.

## 3. Mandatory human regulatory verification BEFORE operational activation

No agent, generated source text or green CI result may self-certify these items.
For **each** tenant, country, state, program, license type and rule version:
1. Verify **the governing current legal source** with the applicable regulator or
   qualified compliance professional; record reviewer, date, source URL/version
   and preserved underlying evidence, not a synthetic/sample document.
2. Verify filer license category, responsible legal entity, product classification,
   beer/wine/spirits distinctions, registration/COLA status, supplier/agent relationship,
   jurisdiction, eligible channels, location/counties and distributor territory.
3. Verify required record/column layout and agency-provided live import template.
   No invented column names, form numbers, deadlines, tax rates or discount rules.
4. Verify applicable pricing periods, amendment/revision rules, exceptions,
   discount eligibility, source pricing, publication timing and notice lead times.
5. Independently reconcile **tenant-selected QBO or QBD** price records to
   approved internal price book, cost snapshots and warehouse/logistics expenses;
   retain immutable evidence and discrepancy resolution.
6. Obtain two distinct, authorized sign-offs for any future **regulatory filing**:
   internal business approval and regulator-facing filing authorization. The
   Phase 1 documentary attestation is **not** either filing authorization.
7. Receive and independently authenticate the government response/receipt through
   an approved integration or verified manual channel. A human-reported URL is
   not evidence of a completed regulator filing.
8. Re-verify on new rule versions, license change, operator change or authority update.

Current candidate references (not final legal certification):
- New York SLA manufacturers/wholesalers and price posting:
  https://sla.ny.gov/node/1141
- New Jersey ABC eCPL FAQ (revised January 8, 2026):
  https://nj.gov/oag/abc/downloads/eCPL_faqs_010826.pdf
- New Jersey ABC submission help:
  https://abc.lps.nj.gov/prod/ABCPublic/Help/cpl_submission.htm
- Florida DBPR ABT forms / malt differential-change notice via EDS:
  https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/forms-and-publications/

The code deliberately does **not** encode a specific filing date or current official
template as verified. Rules often vary by product/license and can change.

## 4. Automated sign-off evidence (exact SHA, not just branch)

In GitHub Actions for the **same final head SHA**:
- CI: backend pytest and security mutation tests, frontend lint/typecheck/build/Vitest,
  disposable PostgreSQL migration + rollback tests, desktop, web container, responsive
  Chromium/Firefox/WebKit, mobile typecheck, connector selfcheck and dependency scan.
- CodeQL: Python and JavaScript/TypeScript both **success**.
- No unresolved high-priority static or PR review findings.
- Verify production and staging deployments were **not modified**.
- Record the exact head SHA and CI/CodeQL run IDs in release review notes.

Synthetic acceptance must include a happy path and each negative path:
- Role mismatch, inactive user, cross-tenant IDs and changed policy.
- Missing/malformed regulator rule version and unrecorded human verification.
- Wrong state, wrong effective date, missing cost line, provisional costs,
  mismatched SKU/currency, customer-specific price, unapproved price book.
- Missing/duplicate IDs, oversized pages, source outages and inconsistent reads.
- Unit-of-measure ambiguity, per-unit to per-case conversion, mismatched or
  missing accounting record, one-cent difference, unverified provenance,
  future observation and foreign-tenant accounting source.
- Audit unavailable: case/review/reference and audit must **roll back together**.
- Named but broad `COMMIT allow`, missing `capabilities_match`, wrong `paths_match`,
  foreign-tenant rules and revoked rules are all rejected at the DB audit boundary.
- Direct client table UPDATE/DELETE/INSERT denied; audit and histories are append-only.
- No official filing action, exported template or price writes under any path.

## 5. Actual staging acceptance (not yet performed for this patch stack)

A separately authorized staging-only deployment using **synthetic tenants**:
1. Apply reviewed additive migrations in dependency order to a disposable clone,
   take before/after schema snapshots and verify RLS/RPC grants.
2. Test owner, manager, pricing-only, viewer and a second isolated tenant.
3. Confirm Compliance > Price Posting and internal preparation register load with
   accessible navigation, no document leakage, meaningful source-error states,
   responsive mobile layout, and no accidental filing/publishing buttons.
4. Simulate stale roles, revoked policy, regulator source outage and concurrent
   edits while preparing a case; confirm fail-closed behavior and audit rollback.
5. Verify all four candidate programs remain unverified for deadlines/templates
   until human evidence is supplied; mock a non-verified receipt and check
   `human_reported_unverified` stays visible.
6. Verify no QBO/QBD write, publication or government request was emitted.
7. Compare evidence snapshots and audit timestamps against staging database
   under a separate reviewer account and document observed results.

**Staging deployment requires an explicit new approval.**
**Production deployment requires a different, later approval** and full
authenticated acceptance. Do not infer either from 'Complete', 'Continue'
or a CI check.

## 6. Launch blockers and go/no-go

| Gate | Phase 1 synthetic code | Operational filing |
| --- | --- | --- |
| Candidate source rulebook and UI | Implemented; CI/code review required | Human verified current law + version |
| Internal price-book reconciliation | Synthetic-only / advisory | Independent per-tenant QBO/QBD evidence |
| Approval and access control | Owner/manager + named tenant policy | Filing-specific dual approval |
| Append-only case and audit | Implemented for internal preparation | Immutable verified filing receipt |
| Deadline/template validity | Not attested | Current official template and notice timing |
| Regulator connector | Disabled | Explicitly designed and independently approved |
| Live authenticated acceptance | Not performed | Mandatory before launch |
| Staging/production deployment | Not approved | Separate explicit approvals |

**NO-GO for regulatory submission or price publication** until every operational
filing column is resolved with documented human evidence. A new country/state
requires a new versioned, human-reviewed jurisdiction rule set; do not assume
NY/NJ/FL conditions generalize.
