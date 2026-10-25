# SecretaryAI-Staging Supabase migration source

**This directory is for isolated SecretaryAI-Staging only.** It is intentionally separate from the repository-root `supabase/` migrations, which may be used by other environments and must not be silently rewritten.

Project ref: `xerktvdgjvjozvckjyfp`. This root contains exactly 33 byte-preserved SQL files:

- 28 files named with the versions already present in the staging database's `supabase_migrations.schema_migrations` table (not re-executed).
- 5 original V1–V5 baseline files whose schema effects have been verified but whose remote migration-history entries are still **absent**.

`manifest.json` maps each file to its original audited source path and SHA-256. The content copies are Git blobs, not regenerated SQL. Run `python3 scripts/check_staging_migration_bundle.py` from the repository root to verify bytes and mappings without a network call.

**Do not run** `supabase db push` or `supabase migration repair` until the five baseline records are reconciled with an explicit audit, the staging project is linked in this separate workdir, and `supabase migration list` plus `supabase db push --dry-run` prove no replay. Do not link this workdir to SecretaryAI production or any LYO Supabase project. Record commands, versions and results in the staging evidence.

For context, see `docs/staging-evidence/2026-10-24/migration-map.json` and `docs/staging-evidence/2026-10-25/verification-checkpoint.md`.
