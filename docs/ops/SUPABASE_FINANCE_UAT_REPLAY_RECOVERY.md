# Supabase Finance AR preview replay recovery — review gate

## Update — November 19, 2026: newer isolated Finance preview reconstructed

**Supabase project `toujmmvlgktprabjbccq`, branch
`lionbi-finance-ar-isolated-uat-20261118`, status `FUNCTIONS_DEPLOYED`**
now exists independently of failed older preview `djfcginvavwuyfxxlyjv`.
Read-only live catalog comparisons against shared main `xerktvdgjvjozvckjyfp`
confirm 78 public tables, 25 functions, 14 triggers, 43 RLS policies and
matching deterministic fingerprints for columns, constraints, indexes,
functions, triggers, policies and RLS flags. Selected application/auth
table row counts were all zero, including companies, users, auth users,
conversations, employee WhatsApp connections and Lola memories.

The newer preview contains **51** migration-history records: all 45 versions
present on shared main plus six subsequent Finance restoration/refresh
migrations (`20261118190805`, `20261118190823`, `20261118190827`,
`20261118190830`, `20261118190834`, `20261118191121`). **Five original
history entries still have no stored SQL** on both shared main and new
preview. A functioning current preview **does not prove that a new branch
can now replay cleanly** from shared main. The older preview remains
`MIGRATIONS_FAILED` with 0 public tables. A separate from-scratch replay
certificate / supported main rebaseline is still necessary.

Main has **675** grants to `anon`, `authenticated`, `service_role`, whereas
the newer preview has **454**. The exactly **221 additional main grants**
affect 28 tables (main vs UAT role counts: anon 105 vs 45,
authenticated 134 vs 74, service_role 436 vs 335). The UAT has no
unexpected additional grant entries. Do not widen isolated privileges
merely to match main; determine minimal operational privileges and
verify RLS, cross-tenant access and privileged functions.

**Decision:** Use the new isolated preview as a schema-parity starting point
for further security acceptance. Do not reset it, merge into main, or
rewrite shared-main history without a reviewed canonical source and
proven recovery method. User authorized continuing history recovery;
that approval alone does not constitute independent technical signoff.
Railway and QuickBooks sandbox acceptance remain separate gates.

[Source-only verification evidence](finance_ar_recovered_preview_verification_20261119.json).
Official Supabase documentation: [repair a failed branch](https://supabase.com/kb/troubleshooting/branch-in-migrations-failed-status)
and [restore missing branch schema](https://supabase.com/kb/troubleshooting/new-branch-doesnt-copy-database).

---

**Status:** blocked / no production changes permitted. Evidence: [issue #211](https://github.com/Hectorg0827/SecretaryAI/issues/211). This is a *schema reproducibility incident*, not a QuickBooks provider failure. The isolated Supabase preview `djfcginvavwuyfxxlyjv` costs **$0.01344/hour** until deleted.

## Confirmed source observations — November 18, 2026

| Boundary | Observation |
| --- | --- |
| Existing main Supabase `xerktvdgjvjozvckjyfp` | 78 public tables, 25 functions, 14 noninternal triggers, 43 RLS policies; 45 applied versions; **5 earliest have NULL stored SQL**, next 40 have SQL |
| Git branch currently linked to Supabase main | `claude/secretaryai-tech-spec-lqBZj`; only 11 root migration files |
| Finance RC `9d3bf47e` | 30 root migration files; a second, divergent `backend/supabase/migrations` directory exists |
| Isolated finance UAT preview | `MIGRATIONS_FAILED`; 5 versions marked applied; **0** public tables, functions, triggers, or RLS policies; **all five have NULL stored SQL statements** |
| Failure statement | V6 `purchasing_orders` references `companies`, but `companies` is absent |

The preview marks the initial five versions applied without supplying the schema they should establish. Read-only comparison of `supabase_migrations.schema_migrations.statements` independently confirmed that those same five versions contain no stored SQL on **main** or the preview, while all 40 later main versions have SQL statements. This strongly supports a missing-baseline replay cause, compounded by the mismatched linked Git branch; it does not by itself certify the full cause of every later migration mismatch. Do not infer a working schema from an `ACTIVE_HEALTHY` database process or from migration-history entries alone.

The source-of-truth problem is broader than one file: **main's actual history, the connected Git branch, and the Finance candidate migration directories disagree**. The migration-file content also diverges: **five of the eleven filenames/versions shared by the linked Git branch and Finance RC have different Git blob fingerprints** (the affected versions are `20250322000000`, `20250325000000`, `20250325000001`, `20250325000002`, `20250325000003`). Version parity alone cannot prove equivalent SQL. A new migration dated after V6 will not solve the first failing statement if it is never reached.

## Automated, non-destructive preflight

From a checkout of the Finance RC or the separate migration audit branch:

```bash
python backend/scripts/audit_supabase_preview_replay.py \
  --snapshot docs/ops/finance_ar_replay_observation_20261118.json \
  --migrations supabase/migrations --strict
```

This uses only the checked-in, **credential-free metadata** snapshot and local Git blob hashes of candidate migration SQL files. The checker requires both version and same-version content parity across the linked branch and candidate; hashes are source fingerprints, not cryptographic approval or an RLS/schema equivalence proof. It is expected to exit **2** and show missing schema and migration-version divergence until the incident is repaired. Refresh the observation from **read-only** queries before relying on it; do not consider an old snapshot an acceptance test.

Read-only SQL for each named project, never querying tenant data:

```sql
SELECT version, name, coalesce(cardinality(statements),0) AS statement_count
FROM supabase_migrations.schema_migrations ORDER BY version;
SELECT count(*) AS public_base_tables
FROM pg_catalog.pg_tables WHERE schemaname = 'public';
SELECT
  to_regclass('public.companies') IS NOT NULL AS companies,
  to_regclass('public.users') IS NOT NULL AS users,
  to_regclass('public.stock_warehouses') IS NOT NULL AS stock_warehouses;
```

## Repair plan — do not execute against main without a separate change approval

1. **Reconstruct the initial five missing SQL definitions from canonical, reviewed source** before attempting a clean preview replay. Compare repository `20240101000000`, `20240601000000`, `20240901000000`, `20261017000000`, and `20261017010000` files to the actual main schema; those main history rows currently have NULL SQL. Review how to populate or re-baseline them using the supported Supabase migration workflow, **not ad hoc direct UPDATE of `supabase_migrations`**.
2. **Select a single canonical migration root.** Inventory every file under `supabase/migrations`, `backend/supabase/migrations`, and the actual main `supabase_migrations` history. Prefer the repository's Supabase CLI root after reviewing the missing historical/renamed versions; do not silently discard any applied DDL.
3. **Fetch main's recorded migrations into an isolated working copy** using Supabase's supported `migration fetch --remote` workflow. Inspect `supabase --version` and `supabase migration --help` first: CLI v3 changed its hosted targeting behavior. Fetch locally for review, not into the original deployed Git branch. **Do not approve a remote migration-history repair prompt.** Supabase's `db pull --remote` procedure may ask to synchronize the remote history, which is a **hosted metadata write** and requires separate authorization. Preserve an unchanged main throughout the diagnostic phase.
4. **Diff the actual main database schema**, including `public` relations, views, functions, triggers, privileges and RLS, against a clean migration replay. The main schema includes manual additions that may not appear in the recorded history. Generate a separate proposed baseline **without any production data or OAuth tokens** only if the historical migration repair path is insufficient.
5. **Review and repair the source migration chain itself**, preserving applied historical files and creating new reviewed correction artifacts as needed. A check-in with a new timestamp that runs *after* broken historical DDL is insufficient; the history/replay bootstrap must be fixed with Supabase-supported mechanisms. Reconcile renamed timestamps so the exact remote history has a documented file correspondence.
6. **Prove clean replay** using a disposable local Supabase stack or approved isolated preview, then verify: at least the 78 known baseline public tables where applicable; successful migration status; required auth tables and tenant keys; permissions, RLS and `security_definer` search paths; unique constraints; a no-data schema diff. Keep an audit log of which migration versions ran.
7. **Only after independent database/security review**, reset or recreate the finance UAT branch (loss of isolated test data is expected) and rerun the read-only preflight with **fresh** metadata. Never merge this branch into main merely to clear a failed status.

## Isolated application guardrails

The separate Railway project `LionBI-FinanceAR-Isolated-UAT` currently holds **21 staged changes** (patch `2e23530b-e031-4cae-a6c3-ac4f9b86180a`), pins Finance PR #208 `9d3bf47e`, and has no live deployments or copied database secrets. Keep `SECRETARYAI_AR_PAYMENT_PREVIEW_ENABLED=false`, `INTUIT_ENVIRONMENT=sandbox`, email/push/billing/computer use OFF.

The existing Railway staging patch `cf6ac083-7e09-4eff-a5a0-1a4d15a0096a` is unrelated and must not be accepted as part of Finance UAT. Do not use `service_role` credentials to impersonate an Owner or use production QBO OAuth tokens in an isolated environment.

## Mandatory release evidence

- Reproducible clean schema replay with exact version manifest, safety advisor result, and independent review.
- Separate staging-only deploy approval, isolated credentials, runtime health and fail-closed endpoint-off proof.
- Real **Intuit sandbox** Owner-authenticated read-only QBO invoice/payment reconciliation; no financial POST or customer record publication.
- Explicit rollback, feature re-disable and finance/security human signoff. **Production NO-GO until then.**

References: [Supabase migration failure guidance](https://supabase.com/kb/troubleshooting/branch-in-migrations-failed-status), [branch troubleshooting](https://supabase.com/docs/guides/deployment/branching/troubleshooting), [CLI v3 hosted-targeting change](https://supabase.com/changelog/supabase-cli-v3).


## Transactional full-chain replay evidence — November 18, 2026

An actual rollback-only test against the isolated Finance UAT Postgres branch, with the application and provider credentials disconnected, successfully executed:

- Five original baseline definitions from `supabase/migrations` in Finance RC `9d3bf47e`.
- Forty definitions from main's `supabase_migrations.schema_migrations.statements` (the other five had no stored SQL).
- Three legacy `BEGIN/COMMIT` wrappers were **stripped only in the temporary test copy** so the entire replay could be enclosed in one outer `BEGIN; ... ROLLBACK;`. Those archival SQL records were not changed.
- Result: **78 public tables, 25 functions, 14 noninternal triggers, 43 RLS policies**. The previously failing V6 `purchasing_orders` creation succeeded.
- Eight deterministic catalog fingerprint categories compared to main: constraints, indexes, functions, triggers, RLS policies and RLS flags were **identical**; columns and table grants initially differed.
- Column divergence was exactly `companies.qbo_token_refreshed_at`, which exists in main but is absent from its 45 recorded migration definitions. Replaying the **already-existing** candidate `20261106030900_qbo_token_refresh_timestamp.sql` at its chronological position made the **full column fingerprint equal** to main.
- Permission divergence: main has 675 recorded grants on `anon`, `authenticated`, `service_role` (from this catalog query); replay has 454. The **221 missing grants are present only on main**, not vice versa. **Do not automatically copy those permissions**. [Supabase branching documentation](https://supabase.com/docs/guides/deployment/branching/working-with-branches#default-privileges-on-branches) explains new branches default to **no privileges on public objects**. A security reviewer must decide the minimum grants needed for the application's role and RLS model.
- Separate post-transaction read verified the isolated branch remained unchanged: **zero tables, zero policies, five migration-history rows**. No shared-main writes, no production data, no OAuth token access, and no persistent isolated DB mutation.

Credential-free evidence with hash comparisons: [finance_ar_full_replay_evidence_20261118.json](finance_ar_full_replay_evidence_20261118.json). Hashes are comparison fingerprints, **not** a security signature or proof of exact environment equivalence. Other schemas, privileges and PostgreSQL settings require additional validation.

### Controlled repair decision

A **reproducible schema** has now been demonstrated in a rollback-only isolated test; the **hosted branch creation pipeline is still broken**. Do not confuse these.

The next deployment-impacting change is to rebuild the canonical migration history through [Supabase's supported schema baseline and remote-history workflow](https://supabase.com/kb/troubleshooting/new-branch-doesnt-copy-database), using a reviewed `db pull --remote` / `migration fetch --remote` process. CLI v3 requires explicit hosted-targeting and confirmation. Main's five history rows with NULL SQL and divergent linked Git migrations must be reconciled. `migration repair` updates migration **tracking** only, not SQL; it cannot be used to claim schema DDL was executed.

**Do not** manually `UPDATE supabase_migrations.schema_migrations` on shared main, introduce backdated unreviewed migrations, run broad `GRANT` commands copying main's default privileges, or reset the existing Finance branch until the baseline repair is independently reviewed and the rollback plan signed. Afterwards, reset/recreate only the isolated branch, confirm clean replay, and proceed to Owner-authenticated **Intuit sandbox** acceptance. Independent approval remains open.


## Canonical deduplicated replay — November 18, 2026

All **22 Finance candidate migration files absent from main's version history** have been compared with main's stored SQL:

- **20 have byte-equal source SQL** to already recorded migrations under different timestamps. The explicit version-to-version map is [finance_ar_migration_alias_manifest_20261118.json](finance_ar_migration_alias_manifest_20261118.json).
- Candidate `20261112043000_lola_conversation_owner_scope.sql` is a **semantic overlap**, not byte-equal, with archived main `20261112043411`: the same `conversation_owner_scope` policy would be created twice. A rollback-only attempt with both versions correctly failed with duplicate-policy error.
- Candidate `20261106030900_qbo_token_refresh_timestamp.sql` adds the *sole distinct* missing schema column `companies.qbo_token_refreshed_at`. Its existence was confirmed on main, but none of the 45 main stored migrations defines it.
- A **chronologically ordered 46-definition replay** (five repository baselines + forty archived main SQL definitions + the one QBO timestamp migration) completed in a single transaction on the isolated branch. It reproduced **78 tables, 25 functions, 14 noninternal triggers, 43 RLS policies**, the token timestamp column, and Lola's authenticated owner-scoped conversation policy. Test-only removal of three historical transaction wrappers enabled the final `ROLLBACK`. No database objects persisted.
- Structural catalog hashes match main for constraints, indexes, functions, triggers, RLS policy definitions and RLS flags. Historical 45-migration replay lacked one QBO column; adding it resolves the column fingerprint discrepancy. **28 tables still have role-grant differences** from main: do not copy permissive grants until independent security review.

[Complete credential-free 46-definition replay evidence](finance_ar_canonical_46_replay_evidence_20261118.json) is a proof of *transactional reproducibility*, **not** a deployed clean Supabase branch. The branch itself remains `MIGRATIONS_FAILED` with five initial migration versions whose stored SQL is NULL. Main migration-history repair still requires Supabase-supported workflow and separate authorization.

---


## Read-only preflight v2 — explicit evidence binding (Nov 18, 2026)

The preflight now fails closed for *stale or unbound* observations, archived-main
SQL fingerprint mismatches, candidate-specific schema drift, unexpected
view/sequence definitions or ACLs, and unreviewed grant differences. It also
recognizes `MIGRATIONS_PASSED` as migration replay success while keeping
**staging readiness blocked** until function deployment finishes.

**Archived snapshots are historical evidence, not valid release certificates.**
The original `finance_ar_replay_observation_20261118.json` predates several new
requirements. Its failure under `--strict` is expected, and must not be
"repaired" by substituting guesses or fabricating current observations.

For a *new* read-only observation generated from the exact live project IDs,
current linked Git ref and candidate commit, require:
- `observed_at`: exact UTC ISO-8601 timestamp, no older than 1 hour.
- `source_main_project_ref`, `source_preview_project_ref`,
  `linked_github_branch`, `candidate_commit`: all non-empty and independently
  bound to operator-supplied expected values.
- `main_sql_blob_sha`: Git blob SHA-1 fingerprints over **exact reconstructed
  stored SQL bytes** for every non-null main migration. Do not claim exact
  equality where the storage representation cannot be reconstructed; missing
  proof blocks release.
- `main_schema_objects` / `branch_schema_objects`: include `sequences`;
  `main_schema_digests` / `branch_schema_digests`: also include `views`,
  `sequences`, `sequence_acls` using **identical deterministic catalog query
  definitions in both environments**. Count equality alone cannot validate
  view security or sequence grants.
- If the candidate intentionally adds migration versions absent from main,
  capture independently checked `candidate_schema_objects` and
  `candidate_schema_digests`; compare the preview against the *candidate*,
  while retaining main migration/history provenance checks.
- If least-privilege public-schema grants intentionally differ from main,
  `grant_review` must name the independent reviewer, link the review, and
  specify the precise approved branch grants digest. The operator must also
  explicitly supply the matching `--approved-grant-review-url`. The CLI
  matching this metadata is **not** cryptographic verification of approval;
  independently inspect the linked review before relying on the exception.

Example strict invocation, once a *new* complete snapshot is captured
and source SQL fingerprints are independently verified:

```bash
python backend/scripts/audit_supabase_preview_replay.py \
  --snapshot /path/to/fresh-no-secrets-snapshot.json \
  --migrations supabase/migrations \
  --expect-main-project xerktvdgjvjozvckjyfp \
  --expect-preview-project djfcginvavwuyfxxlyjv \
  --expect-linked-git claude/secretaryai-tech-spec-lqBZj \
  --expect-candidate-commit <exact-reviewed-commit-sha> \
  --strict
```

Do not accept this preflight's *metadata* as a substitute for a successful
clean Supabase hosted preview replay, tested role access, independent human
security approval, or authenticated QuickBooks sandbox UAT.
