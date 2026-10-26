# SecretaryAI-Staging Supabase migration source

**This directory is for isolated SecretaryAI-Staging only.** It is intentionally separate from the repository-root `supabase/` migrations, which may be used by other environments and must not be silently rewritten.

Project ref: `xerktvdgjvjozvckjyfp`. This root contains exactly 33 byte-preserved SQL files:

- 28 files named with versions previously recorded as applied in the staging database (not re-executed).
- 5 original V1–V5 baseline files whose effects were independently audited and whose **migration-history metadata was repaired on October 25, 2026**. They were not re-executed.

`manifest.json` maps each file to its original audited source path and SHA-256. The content copies are Git blobs, not regenerated SQL. Run `python3 scripts/check_staging_migration_bundle.py` from the repository root to verify bytes and mappings without a network call.

## Staging history reconciliation — October 25, 2026

The five baseline migrations (`20240101000000`, `20240601000000`, `20240901000000`, `20261017000000`, `20261017010000`) were audited against staging: 19 expected tables, checked columns, functions and triggers all existed; 60/60 public tables retained RLS; zero company rows existed. The remote history matched the 28 other manifest versions exactly before repair.

Because the Supabase CLI and CLI credentials were unavailable in the agent environment, the repair was performed using the authenticated staging-only SQL connector as a **guarded, atomic, metadata-only INSERT** into `supabase_migrations.schema_migrations`. The transaction asserted exactly 28 previously recorded versions, no baseline duplicates, zero companies and 60 RLS-enabled tables before insertion; afterward it asserted exactly 33 expected versions. No migration SQL was executed. A separate read-back via `supabase_list_migrations` confirmed 33/33 version/name matches, and read-only SQL verified five metadata-only records, 60 public tables, 60 RLS-enabled tables and zero company rows.

**Still required before future migration pushes:** use the official Supabase CLI in this isolated staging workdir, link **only** `xerktvdgjvjozvckjyfp`, run `supabase migration list` and `supabase db push --dry-run`, and verify zero pending SQL. Do not run an actual `supabase db push` without separately authorizing and reviewing every proposed SQL statement. Do not link this workdir to SecretaryAI production or any LYO Supabase project. Do not repeat the metadata repair.

For context, see `docs/staging-evidence/2026-10-24/migration-map.json` and `docs/staging-evidence/2026-10-25/verification-checkpoint.md`.
