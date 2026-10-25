# SecretaryAI-Staging verification checkpoint — 2026-10-25

Scope: ONLY Railway project `77513c77-44df-485e-a927-d57d6a80b2c1`, environment `f9dc2025-cd5b-4df2-95bc-c3c54a3c3e36`, backend service `ef05db6e-da57-4e68-a879-7906cd2c0522`, Supabase project `xerktvdgjvjozvckjyfp`, and Anthropic workspace `wrkspc_01A2ZcW6vYqEGFr5rxmsH2Sp`. No production or LYO changes were made. No backend deploy was authorized or initiated in this checkpoint.

## Verified observations

- Railway staged patch `a6e7b4fb-78a2-4cee-95ea-f8d8b0c50b9f` now contains **three** changes: `DATABASE_URL` (added), `ANTHROPIC_API_KEY` (added), and outbound IPv6 (`false -> true`). Patch status STAGED, non-destructive, not deployed.
- The saved `DATABASE_URL` is a PostgreSQL direct connection URI addressed to **db.xerktvdgjvjozvckjyfp.supabase.co:5432**, with username and password. This is *format/project-binding validation*, **not** an authenticated network connection test.
- Supabase documents direct Postgres endpoints as IPv6 by default; Railway documents outbound IPv6 as disabled by default and required for IPv6-only destinations. Railway live backend config showed `ipv6EgressEnabled=false`. We staged `true` in SecretaryAI-Staging only with `staged=true`; no rollout. Docs: https://supabase.com/docs/guides/database/connecting-to-postgres and https://docs.railway.com/networking/outbound-networking#outbound-ipv6. Project-specific DNS/add-on status and actual reachability remain untested.
- Anthropic Console shows one key named `SecretaryAI-Staging-Railway` scoped to workspace `wrkspc_01A2ZcW6vYqEGFr5rxmsH2Sp`, created October 25, with no expiration and no last-used timestamp. The masked key prefix/suffix in that console match Railway's saved `ANTHROPIC_API_KEY` on a controlled value comparison without disclosing its value. Workspace spend shown: **$0.00 / $1.00 monthly limit**. No paid or synthetic model request was sent, so runtime validity is **not** certified.
- Railway values, inspected directly in the staging service, confirmed `SECRETARYAI_DEPLOYMENT_ENV=staging`, `SECRETARYAI_EXPECTED_SUPABASE_PROJECT_REF=xerktvdgjvjozvckjyfp`, `SECRETARYAI_TEST_DATABASE_ONLY=1`, `INTUIT_ENVIRONMENT=sandbox`, `COMPUTER_USE_ENABLED=false`, `SECRETARYAI_DURABLE_APPROVAL_ENABLED=false`, `SECRETARYAI_EMAIL_DELIVERY_MODE=disabled`, `SECRETARYAI_EMAIL_WORKER_MODE=disabled`, `SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED=false`, `SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED=false`, and `DEBUG=false`. Email staging allowlist had zero recipients in the DB.
- Stripe variable values have the expected `sk_test_` secret-key prefix and `whsec_` signing-secret prefix. Stripe sign-in via Google reached a separate passkey-only two-step challenge in the cloud browser; sign-in and dashboard access were **not** completed. The alternate path offered backup-code-based removal/recovery of two-step authentication; no code was entered and no security settings were changed. Therefore, live sandbox key validity, exact webhook destination, event selection, signing-secret match and delivery remain unverified.
- Fresh read-only SQL found **28** recorded staging migration versions with names matching the prior migration map (see `docs/staging-evidence/2026-10-24/migration-map.json`). All 28 prior audited SQL bodies were byte-exact source matches, and the previously verified 1,283-object schema catalog comparison had zero drift. Five initial baseline files are physically present in staging but absent from migration history. No history records were inserted, reverted, or fabricated in this checkpoint.
- GitHub PR #74's code branch was ahead of Railway's configured source SHA. Railway source is pinned to `3ec14f78d1f2db16f2a9fbc9b38fe9cd0de76833`; latest observed PR head before this evidence-only commit was `052977c06a8be9ecf7844fec46c8e3b336fbc17e`. The most recent backend deployment `b0e5405a-0e24-4f4d-b4be-a3f907bdc51d` is FAILED. Do not assert the configured SHA, current PR SHA and last deployed SHA are identical.

## Migration reconciliation plan — NOT YET EXECUTED

1. Preserve the staged database backup/schema audit and remote SQL body checksums. Choose a *staging-only* canonical migration source/workdir strategy so future production history is not silently rewritten.
2. Materialize the 28 already-applied files under their actual remote version IDs without modifying SQL bytes. Retain all five original V1–V5 baseline SQL files and explicitly include the eight backend-only migrations in the canonical future migration path. Remove conflicting old-name duplicates **only from the selected staging migration source**, never blindly from production's source tree.
3. Verify reproducible apply/order and no SQL/catalog changes in disposable PostgreSQL 17, rerun full CI/CodeQL and re-read remote history.
4. Use the official metadata-only Supabase migration repair workflow for the five verified V1–V5 baseline versions, with an explicit audit of *reconciled schema state rather than original execution history*. Do not alter the 28 already applied entries or call `db push` before source/history parity is reviewed.
5. Verify `supabase migration list` and `supabase db push --dry-run` show **no migration replay** against **staging-only** project `xerktvdgjvjozvckjyfp` before any future changes.

## Release gates still open

- Authenticate over TLS to staging PostgreSQL using the actually saved Railway URL from an appropriate runtime; verify that egress route, username/password, and project binding all work.
- Make one authorized, bounded synthetic Anthropic request within the $1 workspace cap; verify refusal/failure handling.
- Complete Stripe account/passkey verification through an approved human-visible channel. Read back sandbox endpoint and event subscription; test signed delivery after backend deployment. Do not remove or weaken 2FA.
- Reconcile migration source/history as above, with dry-run proof.
- Revalidate exact PR commit CI/CodeQL, source pin, side-effect controls and Redis durability, then deploy to SecretaryAI-Staging only. Verify terminal SUCCESS, /health, login/tenant/paywall controls, webhook rejection/acceptance/idempotence, and logs.

No customer communications, emails, financial actions, live billing charges, production deploys, or LYO mutations were performed. Secrets were not recorded in this evidence file.
