# Lion BI Gate 2 — isolated staging snapshot cron

## Scope

- Railway project `77513c77-44df-485e-a927-d57d6a80b2c1`, environment `staging` only.
- Staging Supabase ref `xerktvdgjvjozvckjyfp` and one approved company `89399925-ea7d-43f3-91cd-95f9a5636b73` only.
- Runs `python -m tasks.staging_snapshot_job` from `backend/Dockerfile.snapshot` on a Railway cron schedule `0 * * * *` (UTC).
- Writes only the scoped `report_snapshots` `dashboard_summary` row and, if QBO access tokens require refreshing, their encrypted OAuth credential metadata. It does **not** write accounting transactions, send messages, run agents, or dispatch other Celery tasks.
- QBO must be an active **sandbox** Online connection. The job fails closed otherwise.

## Required configuration

The cron service uses references to the existing **staging backend** secrets for the Supabase service role, DB isolation validation, encryption secret, Redis lock, and Intuit sandbox OAuth; it does not need SendGrid/Resend, Anthropic, OpenAI, or Stripe credentials. Required nonsecret values:

```
SECRETARYAI_DEPLOYMENT_ENV=staging
SECRETARYAI_EXPECTED_SUPABASE_PROJECT_REF=xerktvdgjvjozvckjyfp
SECRETARYAI_READONLY_REFRESH_COMPANY_ID=89399925-ea7d-43f3-91cd-95f9a5636b73
INTUIT_ENVIRONMENT=sandbox
COMPUTER_USE_ENABLED=false
SECRETARYAI_EMAIL_DELIVERY_MODE=disabled
SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED=false
SECRETARYAI_STAGING_EMAIL_PILOT_ENABLED=false
SECRETARYAI_INVITE_DELIVERY_ENABLED=false
SECRETARYAI_PUSH_DELIVERY_ENABLED=false
BILLING_ENABLED=false
```

The executable also checks Railway's injected project, environment ID, and environment name before opening a database connection. It performs a single-tenant company lookup, verifies `qb_connection_status=connected` and `qb_type=online`, holds a Redis lock across the refresh, and returns non-sensitive completion metadata. An unsuccessful source read does not replace the previously persisted snapshot with invented zero metrics.

## Acceptance and operations

1. Build the dedicated Dockerfile and run the cron once; expect a nonzero exit on missing/mismatched safeguards, missing source, or failed snapshot persistence.
2. Verify the new `report_snapshots` row's `generated_at`, `generated_by=staging_readonly_cron`, `source_read_status=completed`, and `source_quality=partial`.
3. Confirm the Manager dashboard no longer warns about age and `GET /api/briefing/today` returns a **partial** six-section briefing, not a certified complete digest.
4. Confirm no outbound emails, accounting transactions, or Celery beat tasks were triggered. No Desktop connector is expected for the Online tenant.
5. Monitor Railway cron logs and snapshot age. Alert when a refresh fails or the snapshot is older than 24 hours.

Rollback: disable the cron schedule or scale down/stop only the dedicated snapshot service. Leave Backend, Web, and Redis unchanged; the Manager UI will truthfully warn when the last snapshot ages out.

Limitations: An online connector can produce syntactically valid but incomplete sandbox data. The stored snapshot intentionally remains `source_quality=partial`; source-level revenue reconciliation and complete executive-digest validation are separate gates.
