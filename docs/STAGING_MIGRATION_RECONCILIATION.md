# SecretaryAI staging migration baseline — October 24, 2026

## Scope and state

This is an **isolated test environment**. Never execute these instructions
against production SecretaryAI, Lyo, or another Supabase project.

- Supabase staging project ref: `xerktvdgjvjozvckjyfp`
- Railway staging project: `77513c77-44df-485e-a927-d57d6a80b2c1`
- V1–V5 were already executed directly, BEFORE Supabase migration tracking.
- V6–V16 were subsequently applied using the Supabase migration operation.
- V1–V5 schema objects were verified in this project with the read-only
  `scripts/verify_staging_schema_baseline.sql` checker.
- In the 2026-10-24 audit, the database contained 30 public tables, all RLS
  enabled. The staging email allowlist was empty and email execution remained
  disabled. These counts must be rechecked, not assumed, before future work.

## Recorded staging migration history

These were the **remote** migration versions observed (not a mapping to
local migration filenames):

| Version | Remote name |
| --- | --- |
| 20261024041419 | secretaryai_v6_purchasing_receiving |
| 20261024041432 | secretaryai_v7_purchasing_evidence |
| 20261024041434 | secretaryai_v8_evidence_integrity |
| 20261024041436 | secretaryai_v9_malware_review |
| 20261024041442 | secretaryai_v10_agent_execution_ledger |
| 20261024041444 | secretaryai_v11_atomic_agent_execution_claims |
| 20261024041446 | secretaryai_v12_dedicated_execution_role |
| 20261024041453 | secretaryai_v13_atomic_durable_draft_approval |
| 20261024041455 | secretaryai_v14_immutable_approved_email_payload |
| 20261024041457 | secretaryai_v15_staging_email_allowlist_claim |
| 20261024041616 | secretaryai_v16_trigger_function_search_paths |

**Problem:** The repository contains a manually ordered `database/schema*.sql`
chain and two Supabase migration roots (`supabase/migrations` and
`backend/supabase/migrations`). The remote V6–V16 version numbers above were
allocated by Supabase when those files were applied and are NOT automatically
identical to existing timestamp-prefixed files. The missing V1–V5 history
is only one part of the discrepancy.

## Do not push, reset, or repair yet

- Do not run `supabase db push`, `supabase db reset --linked`, or a migration
  with CREATE statements already present in staging.
- Do not insert fabricated rows into `supabase_migrations.schema_migrations`.
- Do not mark any local migration as applied until its precise SQL effect and
  version have been matched against the target database. **Presence of table
  names alone does not prove full schema equivalence.**
- Do not allow deployments to update the existing production database.
- Do not enable the email worker, QuickBooks writes, billing checkout, or
  customer notifications as part of database reconciliation.

## Reconciliation procedure (human-reviewed)

1. In a fresh checkout of PR #74, inventory every `.sql` file in
   `database/`, `supabase/migrations/`, and
   `backend/supabase/migrations/`. Select ONE canonical Supabase CLI migration
   root. Keep the manual `database/` chain for CI until parity is proven.
2. Review the exact file bodies, dependencies, and checksums; create a manifest
   mapping each local filename to the corresponding, independently verified
   schema change already applied on staging.
3. Read `supabase migration list --help`, `supabase migration repair --help`
   and `supabase db diff --help` for the installed CLI release.
4. Link the CLI explicitly to **staging** (`xerktvdgjvjozvckjyfp`). Confirm
   its project ref and inspect `supabase migration list --linked` before any
   write. Never inherit a prior linked project.
5. Run the read-only baseline validator, compare schema using CLI diff/dump
   against a disposable database created from the intended canonical files,
   and verify no security-role, RLS, or function-privilege drift.
6. ONLY once exact correspondence is established, use the documented
   `supabase migration repair <version> --status applied`/reverted options
   to reconcile individual known migrations. Record each decision and its
   checksums. This updates history **only**, not the physical schema.
7. Re-run `supabase migration list --linked`, the baseline verifier, a
   schema diff and the Supabase security advisors. Stop if any discrepancy
   remains. Do not mark migration repair complete until no unintended
   migrations would run under a dry-run of future deployment.

References:
- https://supabase.com/docs/reference/cli/supabase-migration-repair
- https://supabase.com/docs/guides/deployment/database-migrations

## Release invariants

Until this is signed off: Railway backend configuration remains **staged, not
deployed**; real email sending remains disabled; staging email allowlist
remains empty; no Lyo projects or production SecretaryAI data are used.
