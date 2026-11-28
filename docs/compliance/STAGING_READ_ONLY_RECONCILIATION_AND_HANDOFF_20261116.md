# Lion BI Compliance Phase 1 — Staging Migration Reconciliation and Non-Executing Handoff

**Evidence date:** 2026-11-16  
**Connected database examined:** `SecretaryAI-Staging` (`xerktvdgjvjozvckjyfp`), PostgreSQL 17.  
**Code baseline:** draft PR #187, commit `4a1e5aa7da8537603ff0e57a53fdbe38ad3b03cf`.  
**Decision:** **NO-GO for any migration, deployment, price publication or filing.**  
**Evidence:** [staging_migration_snapshot_20261116.json](staging_migration_snapshot_20261116.json), generated from authorized **read-only** Supabase migration/catalog queries plus repository Git blob checks.

## 1. Observed migration history

- **45** migration versions recorded remotely; **33/33** pinned October 24 baseline entries match version and migration name. This does not prove all 33 historical SQL bodies are identical.
- **12** later staging versions are recorded, ending at `20261114163243`.
- SQL statements are retained in the remote `supabase_migrations.schema_migrations` table, including a single stored statement body for each of the 12. Cryptographic Git blob SHA-1 was computed **inside PostgreSQL without returning the underlying SQL text**, using the UTF-8 statement bytes and the standard Git `blob <byte length>\0<contents>` prefix.
- **9/12** stored remote SQL statement blobs match exact Git blobs in the connected repository. Three are matched to current canonical SQL files; six are matched to repository historical blob objects. Exact blob agreement does not prove current live objects were not subsequently altered.
- For the other three — **`20261111005641` Logistics Journey**, **`20261111005840` Lola Core**, and **`20261112043411` Conversation Owner RLS** — no identical Git blob was retrievable from the connected repository. The SQL statements **are present in remote history**, but their source provenance cannot yet be certified against checked-in code. Their principal schema objects were independently found in the live catalog. This is **partial footprint verification**, not byte-level source reconciliation.

### Post-baseline result matrix

| Version | Purpose | Exact remote SQL Git-blob source? |
| --- | --- | --- |
| 20261102092256 | Stripe billing safety | Historical Git blob matched |
| 20261102232409 | Billing checkout history safety | Historical Git blob matched |
| 20261103022426 | Onboarding audit consistency | Historical Git blob matched |
| 20261108034956 | Provisional purchasing cost assessments | Canonical SQL file matched |
| 20261111005641 | Logistics Journey | **No matching Git blob found** |
| 20261111005840 | Lola Core | **No matching Git blob found** |
| 20261111010558 | Gate 6B finance department roles | Historical Git blob matched |
| 20261112043411 | Lola conversation owner RLS | **No matching Git blob found** |
| 20261114033708 | Lola employee private intelligence | Historical Git blob matched |
| 20261114041719 | Lola WhatsApp consent and ownership | Historical Git blob matched |
| 20261114162323 | Pricing price-book and cost foundation | Canonical SQL file matched |
| 20261114163243 | Pricing foreign-key/index follow-up | Canonical SQL file matched |

**Important:** The source hash check used remote **recorded** statements. The repository's October 24 staging migration bundle is historical and incomplete for subsequent staging changes; do not run it as a general-purpose “repair.”

## 2. Current staging schema footprint (read-only)

- `pricing_cost_snapshots` (12 columns), `pricing_cost_lines` (10), `pricing_price_book` (19) are present. All have RLS enabled and no `anon` or `authenticated` SELECT grant; `service_role` has SELECT, not INSERT.
- `policy_rules` (18 columns) and `audit_events` (23) exist and have RLS enabled. Presence **does not** prove the seven future Compliance migrations have been applied.
- Logistics table `logistics_events`, and `lola_action_receipts`, `lola_delegations`, `lola_memories`, `lola_org_members` exist with RLS enabled; no direct `anon` or `authenticated` SELECT grants were observed on those tables.
- `conversation_owner_scope` policy is present on `public.conversations` for `authenticated`, with command `ALL`. **Predicate logic has not been audited for complete correctness.**
- No expected new price-posting rulebook/review/preparation/external-reference/accounting-observation/capture-binding tables were found; selected new price preparation/review RPCs were also not present.
- Schema object footprints, permissions, and migration history are **not** substitutes for an isolated end-to-end staging acceptance test or a legal/regulatory sign-off.

## 3. Seven unrecorded migrations — precise non-executing plan

**Do not reapply either of the two already-recorded pricebook migrations:**
- `20261114162323_pricing_pricebook_cost_basis_staging_20261114`
- `20261114163243_pricing_pricebook_fk_indexes_staging_20261114`

The source tree holds the following **seven** ordered, currently *unrecorded* candidate SQL files. Their Git blob IDs are pinned in the JSON evidence snapshot and checked locally in CI.

| Order | Proposed migration version | Source | Purpose |
| --- | --- | --- | --- |
| 1 | 20261115000000 | `backend/supabase/migrations/20261115000000_compliance_atomic_policy_audit.sql` | Audited atomic compliance operations |
| 2 | 20261115010000 | `backend/supabase/migrations/20261115010000_compliance_price_posting_rulebook.sql` | Tenant-scoped rulebook, reviews and immutable records |
| 3 | 20261115020000 | `backend/supabase/migrations/20261115020000_price_posting_human_attestation.sql` | Owner/manager attestation and audit RPC |
| 4 | 20261115030000 | `backend/supabase/migrations/20261115030000_price_posting_preparation_ledger.sql` | Append-only internal case and unverified reference ledger |
| 5 | 20261115040000 | `backend/supabase/migrations/20261115040000_price_posting_specific_policy_guard.sql` | Enforce exact API capability/path in audit transaction |
| 6 | 20261115050000 | `backend/supabase/migrations/20261115050000_price_posting_accounting_observations.sql` | Immutable unverified price observations; no ingestion authority |
| 7 | 20261115060000 | `backend/supabase/migrations/20261115060000_price_posting_capture_bindings.sql` | Immutable unverified provider item-ID and pack candidates |

**Before any operator may prepare an actual staged migration bundle:**
1. Re-query the intended staging **project ref** and full remote migration history in read-only mode. Confirm exactly which versions have since been applied and that no conflicting version or duplicate revision exists.
2. Investigate the three remote SQL bodies whose exact Git sources were not found. Independently compare their full SQL, functions, grants, RLS policies and triggers to the currently deployed catalog. Do not recreate or overwrite a recorded migration to make history appear clean.
3. Compare current staging schema prerequisites with the seven migrations' target objects, dependencies, SECURITY DEFINER execute privileges, `search_path`, role/tenant checks and trigger effects. A target that already exists despite an absent history record is a conflict, **not** permission to reapply.
4. Assemble a **newly reviewed staging-only** migration source bundle preserving the 45 previously recorded versions. Decide explicitly which source tree and Supabase CLI version to use; never assume the repository's default `supabase/migrations` tree is the target linked remote project.
5. Restore a disposable clone of the current staging schema/migration state. Dry-run exactly those **seven** additions in timestamp order with no QBO/QBD calls and no external regulatory requests. Compare schema, function signatures, RLS, grants, audit event behavior, rollback, duplicate/cross-tenant denials, owner/manager permissions and the user-facing blocked filing status.
6. Obtain an independent technical reviewer sign-off, then **separate explicit staging migration/deployment authorization**. Only after that may a scoped operator apply changes to staging.
7. After any separately approved staging deployment, run authenticated owner/manager, pricing-only, viewer and second-tenant acceptance checks. Live QBO/QBD reconciliation, warehouse/freight costing, actual agency licensing and filing authority still require distinct reviews.

**Not authorized:** `supabase db push`, `migration repair`, `apply_migration`, any SQL DDL, Railway redeploy, release PR merges, pricing writes, QBO/QBD connections, government filings or live regulatory template exports.

## 4. Offline guardrail and expected CI behavior

`scripts/verify_compliance_staging_snapshot.py` verifies the sanitized snapshot structure and exact **local source Git blob hashes** for all seven unrecorded Compliance SQL files. It preserves the three unmatched historical migrations as unresolved and emits `NO_GO`. The code is offline and does not refresh remote state.

`--release-gate` deliberately exits nonzero **even for internally valid snapshots**. Ordinary source-verification CI may pass and publish the report; **green CI and CodeQL are not deployment approval**.

Run the separate historical root preflight (`scripts/compliance_migration_release_preflight.py`) as well; it is expected to find that the old staging bundle lacks these files. This is a bundle-coverage warning, not a claim that the pricebook foundation is unrecorded in the live staging project.

## 5. Remaining independent acceptance

- Stage/production rollout remain **NO-GO** pending explicit approvals.
- Current official NY/NJ/FL rules, filing deadlines/templates and authorized license scope remain to be verified by an appropriate compliance officer.
- Account-item identity, units-per-case, QBO/QBD provenance, costs, warehouse/freight and tenant-correct financial figures remain unverified.
- No external filing receipt may be labeled accepted on the basis of a human-provided link or internal synthetic source.

**Audit integrity:** all actions in this reconciliation were read-only Supabase project/migration/catalog queries, read-only GitHub source/blob inspection and documentation/CI changes to isolated draft branches. No live database mutations or runtime deployment.
