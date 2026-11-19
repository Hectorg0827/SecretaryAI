# Finance AR canonical migration-source recovery (2026-11-19)

Source-only recovery of 46 distinct migration versions for isolated clean-replay testing.

Five files are exact GitHub source baselines corresponding to shared-main historical versions with missing SQL history; 40 are original one-block definitions recovered from shared-main Supabase migration history; one is the independently source-proven QBO token-refresh timestamp change. The manifest preserves provenance and content fingerprints.

The GitHub Actions workflow at .github/workflows/finance-canonical-local-replay.yml runs Supabase CLI v3 using only a disposable local Docker stack. It verifies the source, constructs a temporary local project, replays all files, compares public catalog counts and key QBO/RLS objects, resets the local stack and replays again.

No access tokens, customer table data, external provider calls, deployment secrets, or --remote commands are permitted. Do not point the workflow or these historical SQL files at a shared or production database. Historical SQL can include data-transforming statements.

This package DOES NOT repair the five NULL-SQL rows of shared-main migration metadata. Supabase migration repair changes tracking, not applied DDL. A separate, authenticated CLI v3 and independently approved history/rollback procedure are still required for remote reconciliation, followed by a fresh hosted branch and authenticated Owner QuickBooks sandbox tests. Production NO-GO.

Official docs: https://supabase.com/changelog/supabase-cli-v3 and https://supabase.com/docs/reference/cli/supabase-migration-repair
