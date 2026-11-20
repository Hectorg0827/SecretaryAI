# Finance AR isolated UAT — startup and acceptance preflight (2026-11-19)

**Scope:** name-only inventory, offline test coverage and evidence. No Railway deployment, Supabase DDL or external provider calls.

## Exact observed state

- Railway project: `LionBI-FinanceAR-Isolated-UAT`; environment: `finance-uat`; service: `FinanceAR-Backend-Preview`.
- One staged, **unapplied** patch with 24 changes. No previous deployment was found for the service.
- A read-only Railway variable inventory reported only the eight built-in `RAILWAY_*` variables live. The staged changes include variables for Supabase URL, anon key, expected project reference and the default-off AR preview, but **neither the live names nor the staged patch includes**: `SECRET_KEY`, `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, or `ANTHROPIC_API_KEY`.
- `backend/app/config.py` requires these four configuration names to instantiate `Settings`. The current staging startup would be blocked if they are still absent when attempting deployment.
- For authenticated QuickBooks sandbox acceptance, `REDIS_URL`, `INTUIT_CLIENT_ID`, `INTUIT_CLIENT_SECRET`, and `INTUIT_REDIRECT_URI` must also be checked; they are not among the observed live or staged names.

These findings are **name-presence only**. A present variable may still have an incorrect or unsafe value. Never assume a valid Supabase project binding, disabled external writes, a sandbox realm, or verified tenant identity merely from the presence of a name.

## Check the proposed Railway environment without reading secrets

1. Read Railway `list_variables` and extract **only** `variableNames` (no `variables` values). Save this list to `live-names.json` outside the repository.
2. Read Railway `get_staged_changes` and extract only added/updated **environment variable names**, excluding other fields such as Branch, Repo or Healthcheck. Save the array as `staged-names.json`.
3. Run `python scripts/check_finance_uat_env.py --live-names live-names.json --staged-names staged-names.json --mode boot`.
4. For later sandbox readiness, run the same command with `--mode sandbox`. Nonzero means do not deploy. Passing means names are present, **not** that credentials, values, or authorization are valid.

**Do not commit these input files or store secret values in them.** The script rejects value dictionaries; the CI regression suite uses synthetic names only.

## Required second-stage controls (not automated here)

- Validate in Railway with trusted authorization that `SECRETARYAI_DEPLOYMENT_ENV=staging`, `INTUIT_ENVIRONMENT=sandbox`, `SECRETARYAI_AR_PAYMENT_PREVIEW_ENABLED=false` until separately authorized UAT, `COMPUTER_USE_ENABLED=false`, email/push/invite delivery disabled, and billing/Stripe safe. Do not print credentials.
- Confirm the runtime `DATABASE_URL` and `SUPABASE_URL` belong to the exact isolated Supabase branch; verify the valid configured project ref and actual database role, not just textual configuration.
- Verify that the owner and separate employee/revoked/foreign tenant are genuinely authenticated, not impersonated through service-role calls. Apply the actual RLS/permission matrix and document denied paths.
- Perform QuickBooks **GET-only sandbox** verification under the authorized Owner session with exact Preferences / Invoice and Payment COUNT and paginated parity, partial/split/unapplied payments, source-drift handling and redacted evidence.
- Obtain independent security/finance review and specific isolated-staging deployment approval. Do not commit the existing mixed-source shared staging patch, merge Supabase branches, authorize payment posting, or promote production.

References: #208, #211, #215, #216, #217, #218. This check adds a missing guard; it is **not** a production release gate or proof of authenticated acceptance.
