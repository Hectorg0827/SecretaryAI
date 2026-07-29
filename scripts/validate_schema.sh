#!/usr/bin/env bash
# Validate that the database schema files apply cleanly, catching DDL drift or
# errors before they reach Supabase. Requires psql + a reachable Postgres via the
# standard PG* env vars (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE).
set -euo pipefail
cd "$(dirname "$0")/.."

# Supabase provides auth.jwt(), used by the RLS policies; shim it so the schema
# applies on a plain Postgres in CI.
psql -v ON_ERROR_STOP=1 -c \
  "CREATE SCHEMA IF NOT EXISTS auth; CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb AS \$\$ SELECT '{}'::jsonb \$\$ LANGUAGE sql STABLE;"

for f in database/schema.sql database/schema_v2_additions.sql database/schema_v3_integrations.sql; do
  echo "── applying $f"
  psql -v ON_ERROR_STOP=1 -f "$f"
done
echo "✓ All schema files applied cleanly."
