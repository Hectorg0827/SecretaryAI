# Finance AR isolated UAT — startup and acceptance preflight (2026-11-19)

**Scope:** name-only inventory, offline test coverage and evidence. No Railway deployment, Supabase DDL or external provider calls.

## Exact observed state (historical 2026-11-19 snapshot, NOT current)

The state below is preserved for provenance only. Later isolated staging operations
staged Redis, explicit safe flags, a branch-specific anon key and an independent
signing key. Re-read the exact environment and patch **immediately before**
any deployment decision; do not use the historical 24-change count for clearance.

## Original observed state

- Railway project: `LionBI-FinanceAR-Isolated-UAT`; environment: `finance-uat`; service: `FinanceAR-Backend-Preview`.
- One staged, **unapplied** patch with 24 changes. No previous deployment was found for the service.
- A read-only Railway variable inventory reported only the eight built-in `RAILWAY_*` variables live. The staged changes include variables for Supabase URL, anon key, expected project reference and the default-off AR preview, but **neither the live names nor the staged patch includes**: `SECRET_KEY`, `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, or `ANTHROPIC_API_KEY`.
- `backend/app/config.py` requires these four configuration names to instantiate `Settings`. The current staging startup would be blocked if they are still absent when attempting deployment.
- For authenticated QuickBooks sandbox acceptance, `REDIS_URL`, `INTUIT_CLIENT_ID`, `INTUIT_CLIENT_SECRET`, and `INTUIT_REDIRECT_URI` must also be checked; they are not among the observed live or staged names.

These findings are **name-presence only**. A present variable may still have an incorrect or unsafe value. Never assume a valid Supabase project binding, disabled external writes, a sandbox realm, or verified tenant identity merely from the presence of a name.

## Check the proposed Railway environment without reading secrets

1. Read Railway `list_variables` and extract **only** `variableNames`
   (no variable values) to an ephemeral `live-names.json` outside the repository.
2. Read Railway `get_staged_changes`. Record environment ID, patch ID and
   patch update timestamp; extract **only** added/updated variable names to
   `staged-names.json` and **deleted** variable names to
   `staged-removed-names.json` (JSON `[]` when none). Never infer names from
   unrelated fields such as Repo, Branch or Healthcheck.
3. Independently verify the effective non-secret `BILLING_ENABLED` and
   `DEBUG` values from Railway's targeted environment, respecting pending
   updates and avoiding exposure of credentials. If these cannot be verified,
   **do not run a PASS-bearing preflight or deploy**.
4. Run (for a deliberately free, non-debug isolated UAT deployment):

   ```sh
   python scripts/check_finance_uat_env.py \
     --live-names live-names.json \
     --staged-names staged-names.json \
     --staged-removed-names staged-removed-names.json \
     --billing-enabled false --debug false --mode boot
   ```

   If billing is actually enabled and debug disabled, pass
   `--billing-enabled true --debug false`; the preflight then also requires
   `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` by name. For later QBO
   sandbox readiness, use `--mode sandbox` with the same current inventory.
   The guard also blocks a staged deletion of a required name and requires
   `ENCRYPTION_KEY` if `ENCRYPTION_KEY_PREVIOUS` is present.
5. **Immediately before a separately approved Railway patch commit**, re-read
   `get_staged_changes` and `list_variables`; compare environment ID,
   current patch ID, patch update timestamp, and the full name sets with the
   inputs used above. If any changed, discard inputs and rerun. Serialize
   operator changes during this interval. Because this is an offline name-only
   tool, it **cannot** independently prove freshness or prevent a later
   time-of-check/time-of-use race. The operator must recheck at the actual
   deployment boundary.

**Never commit the input files or store secret values in them.** Passing this
preflight proves only that required *names* are present in a claimed snapshot.
It does **not** establish valid secret contents, project ID isolation,
safe runtime flag values, authenticated access or authorization to deploy.

## Required second-stage controls (not automated here)

- Validate in Railway with trusted authorization that `SECRETARYAI_DEPLOYMENT_ENV=staging`, `INTUIT_ENVIRONMENT=sandbox`, `SECRETARYAI_AR_PAYMENT_PREVIEW_ENABLED=false` until separately authorized UAT, `COMPUTER_USE_ENABLED=false`, email/push/invite delivery disabled, and billing/Stripe safe. Do not print credentials.
- Confirm the runtime `DATABASE_URL` and `SUPABASE_URL` belong to the exact isolated Supabase branch; verify the valid configured project ref and actual database role, not just textual configuration.
- Verify that the owner and separate employee/revoked/foreign tenant are genuinely authenticated, not impersonated through service-role calls. Apply the actual RLS/permission matrix and document denied paths.
- Perform QuickBooks **GET-only sandbox** verification under the authorized Owner session with exact Preferences / Invoice and Payment COUNT and paginated parity, partial/split/unapplied payments, source-drift handling and redacted evidence.
- Obtain independent security/finance review and specific isolated-staging deployment approval. Do not commit the existing mixed-source shared staging patch, merge Supabase branches, authorize payment posting, or promote production.

References: #208, #211, #215, #216, #217, #218. This check adds a missing guard; it is **not** a production release gate or proof of authenticated acceptance.
