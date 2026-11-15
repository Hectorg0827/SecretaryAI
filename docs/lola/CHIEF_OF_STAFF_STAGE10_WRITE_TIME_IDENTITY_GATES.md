# Lola Stage 10 — verified identity registry and commit-time consent

**Status: GitHub staging-only draft. NO hosted Supabase migrations, Railway deploy,
production changes, WhatsApp sends or live provider account reads.**

## Why

Stage 9 authenticates an employee and verifies provider consent in Python
before fetching Gmail/Graph changes and before invoking the replay checkpoint
RPC. A consent revocation could otherwise commit after Python's final check
but before the SQL cursor write. Stage 5 also references verified registry
tables not previously materialized in the repository's staged migrations.

## New additive migration

`supabase/migrations/20261115203000_lola_verified_identity_replay_write_gate_staging.sql`
introduces three **empty**, service-role-only tables with RLS enabled:

1. `lola_channel_authorizations`: one current employee-owned Gmail/Outlook
   email grant and one official WhatsApp Business grant, with consent,
   verification, expiry, source revision and HMAC-indexed provider identity.
2. `lola_verified_case_links`: independently verified contact/work-item link,
   employee confirmation, expiry/revocation and mapping revision exactly
   matching Stage 5 `resolve_verified_case` fields.
3. `lola_authorized_email_resources`: independently authorized employee
   mailbox or Graph folder HMAC key, provider, source and expiry.

**The migration seeds zero verified records.** An empty registry deliberately
blocks every provider checkpoint until trusted employee and provider
onboarding populates the records.

### Write-time trigger

A `BEFORE INSERT OR UPDATE` trigger on the Stage 8 replay checkpoint table
requires the active scoped user and BOTH verified channel grants, employee
consent not revoked, matching OAuth-provider identity HMAC, an authorized
email resource and an independently verified work-item mapping.
Using built-in PostgreSQL SHA256, it recomputes the exact Stage 5 mapping
revision from tenant, employee, contact, work item, link revision and BOTH
provider grant revisions. All relevant rows are locked with `FOR SHARE`
until the replay transaction commits, serializing conflicting revocations.

An additional event trigger rejects event records without a matching
same-tenant/employee/provider/resource replay checkpoint and digest;
a foreign key prevents orphaned events. The new functions are
`SECURITY INVOKER`, with function EXECUTE revoked from browser roles.

### Existing migrations untouched

- The Stage 8 migration is NOT edited.
- Stage 9 Python contracts are NOT changed.
- No hosted SQL or database connector mutation was executed.

## Tests

- `backend/tests/test_lola_stage10_registry_sql_contract.py`: asserts
  registry field parity, RLS/grants, trigger, hash protocol and invariants.
- `scripts/test_lola_stage10_replay_security.sql`: synthetic transaction
  exercises missing consent rejection, successful atomic Stage 8 RPC +
  event persistence, then revocation rejection with unchanged generation.
- `.github/workflows/ci.yml` now applies BOTH Stage 8 and Stage 10
  migrations to the **disposable PostgreSQL 17** CI service after the
  existing baseline and runs the synthetic SQL acceptance test.
  Prior `DB schema validation` jobs did not apply these new migrations;
  prior green outcomes are NOT evidence of their runtime acceptance.

## Remaining release blockers

1. Real provider/employee ownership onboarding, linked resource evidence
   and backend-only registry write paths are not yet implemented.
2. Database role grants, deadlocks, concurrent revocation and transaction
   isolation must be independently reviewed and tested under Supabase
   PostgREST service-role context before a hosted migration.
3. Provider-side OAuth revocation and subscription expiration cannot be
   proven through SQL alone; backend reauthorization and monitoring remain
   mandatory, including Gmail full baseline and Graph per-folder coverage.
4. Signed Meta WhatsApp app echo, exhaustive source history, verified
   contact mapping and human-review decisions still need end-to-end staging
   testing. No automatic reminder or outbound operation is enabled.
5. No sensitive provider data, message bodies or plaintext tokens may be
   logged or exposed by onboarding endpoints.

## Documentation checks

- Supabase Data API security: https://supabase.com/docs/guides/api/securing-your-api
- Postgres built-in SHA256: https://www.postgresql.org/docs/15/functions-binarystring.html
- Supabase changelog: https://supabase.com/changelog.md

**Only commit and CI verification were authorized. Hosted staging and
production remain untouched.**