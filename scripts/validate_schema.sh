#!/usr/bin/env bash
# Validate that the database schema files apply cleanly, catching DDL drift or
# errors before they reach Supabase. Requires psql + a reachable Postgres via the
# standard PG* env vars (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE).
set -euo pipefail
cd "$(dirname "$0")/.."

echo '── checking migration SQL source parity (read-only)'
python3 scripts/check_migration_source_parity.py
echo '✓ V1–V16 manual and Supabase migration copies match.'

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

for f in database/schema.sql database/schema_v2_additions.sql database/schema_v3_integrations.sql supabase/migrations/20250322000000_draft_expiry.sql supabase/migrations/20250322000001_ingested_files_hash.sql supabase/migrations/20250325000000_report_snapshots.sql supabase/migrations/20250325000001_memory.sql supabase/migrations/20250325000002_feed_events.sql supabase/migrations/20250325000003_workflows.sql supabase/migrations/20250325000004_connector_fields.sql supabase/migrations/20250325000005_conductor_end_user.sql database/schema_v4_connector_secret_digest.sql database/schema_v5_stock_ledger.sql database/schema_v6_purchasing_receiving.sql database/schema_v7_purchasing_evidence.sql database/schema_v8_evidence_integrity.sql database/schema_v9_malware_review.sql database/schema_v10_agent_execution_ledger.sql database/schema_v11_atomic_agent_execution_claims.sql database/schema_v12_dedicated_execution_role.sql database/schema_v13_atomic_durable_draft_approval.sql database/schema_v14_immutable_approved_email_payload.sql database/schema_v15_staging_email_allowlist_claim.sql database/schema_v16_trigger_function_search_paths.sql; do
  echo "── applying $f"
  psql -v ON_ERROR_STOP=1 -f "$f"
done
echo "✓ All schema files applied cleanly."
echo '── testing legacy core RLS and no unsafe client grants'
psql -v ON_ERROR_STOP=1 -f scripts/test_legacy_core_security.sql
echo '✓ Legacy module security checks passed.'

echo '── validating legacy billing/compliance migrations in disposable PostgreSQL'
psql -v ON_ERROR_STOP=1 -f backend/supabase/migrations/20250325000001_billing.sql
psql -v ON_ERROR_STOP=1 -f backend/supabase/migrations/20250325000003_compliance.sql
psql -v ON_ERROR_STOP=1 -f scripts/test_legacy_billing_compliance_security.sql
echo '✓ Billing and compliance schema RLS checks passed.'

echo '── validating internal browser, ingestion and computer-use queues'
psql -v ON_ERROR_STOP=1 -f backend/supabase/migrations/20250331000009_phase6_ingestion.sql
psql -v ON_ERROR_STOP=1 -f backend/supabase/migrations/20250331000010_phase7_cu.sql
psql -v ON_ERROR_STOP=1 -f scripts/test_legacy_internal_queues.sql
echo '✓ Internal queues RLS and tenant FK checks passed.'

echo '── validating Phase1 connector/automation control-plane schemas'
psql -v ON_ERROR_STOP=1 -f backend/supabase/migrations/20250328000007_phase1_schema.sql
psql -v ON_ERROR_STOP=1 -f backend/supabase/migrations/20250331000008_phase4_capability_routes.sql
psql -v ON_ERROR_STOP=1 -f scripts/test_phase1_control_plane_security.sql
echo '✓ Phase1/Phase4 control-plane access checks passed.'



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

echo '── testing V10 dormant execution ledger isolation and state guards'
psql -v ON_ERROR_STOP=1 -f scripts/test_agent_execution_ledger.sql
echo '✓ Execution ledger checks passed.'

echo '── testing V11 atomic claim and fencing contract'
psql -v ON_ERROR_STOP=1 -f scripts/test_atomic_agent_execution_claims.sql
echo '✓ Atomic claim checks passed.'

echo '── testing V12 least-privilege database execution role'
psql -v ON_ERROR_STOP=1 -f scripts/test_execution_role.sql
echo '✓ Execution role privilege checks passed.'

echo '── testing atomic durable approval and tenant authorization'
psql -v ON_ERROR_STOP=1 -f scripts/test_durable_approval.sql
echo '✓ Atomic approval checks passed.'

echo '── testing V15 immutable approved email payload and claim'
psql -v ON_ERROR_STOP=1 -f scripts/test_approved_email_snapshot.sql
echo '✓ Immutable email snapshot checks passed.'

echo '── testing V17 staging email recipient allowlist'
psql -v ON_ERROR_STOP=1 -f scripts/test_staging_email_allowlist.sql
echo '✓ Staging email allowlist checks passed.'

echo '── asserting trigger search paths are pinned'
psql -v ON_ERROR_STOP=1 -Atc "SELECT CASE WHEN count(*) = 3 AND bool_and('search_path=pg_catalog, pg_temp' = ANY(coalesce(p.proconfig, ARRAY[]::text[]))) THEN 'ok' ELSE 'bad' END FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname = 'public' AND p.proname IN ('update_updated_at','reject_stock_movement_rewrite','reject_purchase_evidence_rewrite')" | grep -qx ok
echo '✓ Trigger search paths are pinned.'

echo '── verifying V1–V5 installed baseline without history mutation'
psql -v ON_ERROR_STOP=1 -f scripts/verify_staging_schema_baseline.sql
echo '✓ V1–V5 baseline verified.'
