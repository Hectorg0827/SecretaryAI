#!/usr/bin/env bash
# Validate that the database schema files apply cleanly, catching DDL drift or
# errors before they reach Supabase. Requires psql + a reachable Postgres via the
# standard PG* env vars (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE).
set -euo pipefail
cd "$(dirname "$0")/.."

# Supabase provides auth.jwt(), used by the RLS policies; shim it so the schema
# applies on a plain Postgres in CI.
psql -v ON_ERROR_STOP=1 -c \
  "CREATE SCHEMA IF NOT EXISTS auth; CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb AS \$\$ SELECT coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb) \$\$ LANGUAGE sql STABLE;"

# Supabase predefines API roles. CI's vanilla PostgreSQL doesn't; emulate
# them only in this disposable validation database, without production secrets.
psql -v ON_ERROR_STOP=1 <<'SQL'
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='service_role') THEN
        CREATE ROLE service_role NOLOGIN BYPASSRLS;
    END IF;
END
$roles$;
GRANT USAGE ON SCHEMA auth TO authenticated, anon, service_role;
SQL

for f in database/schema.sql database/schema_v2_additions.sql database/schema_v3_integrations.sql database/schema_v4_connector_secret_digest.sql database/schema_v5_stock_ledger.sql database/schema_v6_purchasing_receiving.sql database/schema_v7_purchasing_evidence.sql database/schema_v8_evidence_integrity.sql database/schema_v9_malware_review.sql; do
  echo "── applying $f"
  psql -v ON_ERROR_STOP=1 -f "$f"
done
echo "✓ All schema files applied cleanly."
echo "── running real PostgreSQL stock-ledger integrity tests"
psql -v ON_ERROR_STOP=1 -f scripts/test_stock_ledger.sql
echo "✓ Stock-ledger integrity checks passed."

echo '── testing approved purchase-to-stock lifecycle'
psql -v ON_ERROR_STOP=1 -f scripts/test_purchasing_receiving.sql
echo '✓ Purchase-to-stock acceptance tests passed.'

echo '── testing unverified purchasing evidence and cost claims'
psql -v ON_ERROR_STOP=1 -f scripts/test_purchasing_evidence.sql
echo '✓ Purchasing evidence isolation and immutability checks passed.'

echo '── testing V8 document byte-integrity attestations'
psql -v ON_ERROR_STOP=1 -f scripts/test_evidence_integrity.sql
echo '✓ Evidence integrity attestation checks passed.'

echo '── testing V9 antivirus attestations and independent review'
psql -v ON_ERROR_STOP=1 -f scripts/test_evidence_malware_review.sql
echo '✓ Malware scan and independent review acceptance passed.'
