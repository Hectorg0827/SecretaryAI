#!/usr/bin/env bash
# Validate the database schema end to end. Requires psql + a reachable Postgres
# via the standard PG* env vars (PGHOST/PGPORT/PGUSER/PGPASSWORD). It creates and
# drops its own scratch databases; it never touches PGDATABASE's contents.
#
# It proves four things:
#   1. every migration in supabase/migrations applies cleanly, in order, on an
#      empty database;
#   2. the whole set can be applied a second time without error (re-runnable);
#   3. a database created the OLD way (database/schema.sql) upgrades cleanly;
#   4. every table and column the backend code uses exists afterwards, and
#      every table has row-level security switched on.
set -euo pipefail
cd "$(dirname "$0")/.."

ADMIN_DB="${PGDATABASE:-postgres}"
export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"

# Supabase provides the auth schema and these roles; shim them on plain Postgres.
SHIM=$(cat <<'SQL'
CREATE SCHEMA IF NOT EXISTS auth;
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb AS $$ SELECT '{}'::jsonb $$ LANGUAGE sql STABLE;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid AS $$ SELECT NULL::uuid $$ LANGUAGE sql STABLE;
DO $$ BEGIN CREATE ROLE authenticated; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE anon; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
SQL
)

fresh_db() {
  psql -q -d "$ADMIN_DB" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $1" -c "CREATE DATABASE $1"
  psql -q -d "$1" -v ON_ERROR_STOP=1 -c "$SHIM"
}

apply_all() {
  for f in supabase/migrations/*.sql; do
    psql -q -d "$1" -v ON_ERROR_STOP=1 -f "$f" || { echo "✗ FAILED: $f (database $1)"; exit 1; }
  done
}

check_db() {
  PGDATABASE="$1" python3 scripts/check_schema_usage.py
  local no_rls
  no_rls=$(psql -At -d "$1" -c "select coalesce(string_agg(c.relname, ', '), '') from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' and not c.relrowsecurity")
  if [ -n "$no_rls" ]; then echo "✗ Tables without row-level security: $no_rls"; exit 1; fi
  echo "✓ Row-level security is on for every table."
}

echo "── 1/3 empty database → all migrations"
fresh_db sai_validate_fresh
apply_all sai_validate_fresh
echo "── 2/3 same database → all migrations again (must be re-runnable)"
apply_all sai_validate_fresh
check_db sai_validate_fresh

echo "── 3/3 database created the old way → all migrations"
fresh_db sai_validate_legacy
psql -q -d sai_validate_legacy -v ON_ERROR_STOP=1 -f scripts/fixtures/legacy_schema_v1.sql
psql -q -d sai_validate_legacy -v ON_ERROR_STOP=1 -c "INSERT INTO companies (id, name) VALUES ('11111111-1111-1111-1111-111111111111', 'Legacy Co'); INSERT INTO users (company_id, email, hashed_password, full_name, role) VALUES ('11111111-1111-1111-1111-111111111111', 'owner@legacy.test', 'x', 'Legacy Owner', 'owner');"
apply_all sai_validate_legacy
check_db sai_validate_legacy
kept=$(psql -At -d sai_validate_legacy -c "select password_hash || '|' || name from users where email='owner@legacy.test'")
[ "$kept" = "x|Legacy Owner" ] || { echo "✗ legacy user data was not carried over (got: $kept)"; exit 1; }
echo "✓ Existing users keep their password and name through the upgrade."

echo "── behaviour checks (the writes the app really makes)"
psql -q -d sai_validate_fresh -v ON_ERROR_STOP=1 <<'SQL'
INSERT INTO companies (id, name) VALUES ('22222222-2222-2222-2222-222222222222', 'Smoke Co');
INSERT INTO users (id, company_id, email, password_hash, name, role, is_active)
  VALUES ('33333333-3333-3333-3333-333333333333', '22222222-2222-2222-2222-222222222222', 'Owner@Smoke.test', 'h', 'Owner', 'owner', true);
-- approval queue: create → claim → finish, plus the failure path
INSERT INTO drafts (id, company_id, created_by, action_type, content, status, created_at, expires_at)
  VALUES ('44444444-4444-4444-4444-444444444444', '22222222-2222-2222-2222-222222222222', 'ai', 'email_reply', '{}', 'pending', now(), now() + interval '48 hours');
UPDATE drafts SET status = 'executing', reviewed_by = '33333333-3333-3333-3333-333333333333', reviewed_at = now() WHERE id = '44444444-4444-4444-4444-444444444444' AND status = 'pending';
UPDATE drafts SET status = 'pending', execution_status = 'failed', execution_detail = 'x' WHERE id = '44444444-4444-4444-4444-444444444444';
UPDATE drafts SET status = 'approved', execution_status = 'executed', executed_at = now() WHERE id = '44444444-4444-4444-4444-444444444444';
UPDATE drafts SET status = 'expired' WHERE id = '44444444-4444-4444-4444-444444444444';
-- audit trail: a person and a system actor
INSERT INTO audit_events (id, company_id, event_type, description, metadata, created_at, actor_id, user_id, action_class, approved_by)
  VALUES (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'draft_approved', 'draft approved', '{"draft_id":"d"}', now(),
          '33333333-3333-3333-3333-333333333333', '33333333-3333-3333-3333-333333333333', 'commit', '33333333-3333-3333-3333-333333333333');
INSERT INTO audit_events (id, company_id, event_type, description, metadata, created_at, actor_id)
  VALUES (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'email_sent', 'morning briefing', '{}', now(), 'scheduler');
-- chat history: one row per message
INSERT INTO conversations (id, conversation_id, company_id, user_id, role, content)
  VALUES (gen_random_uuid(), 'c-1', '22222222-2222-2222-2222-222222222222', '33333333-3333-3333-3333-333333333333', 'user', 'hi');
-- engine trail
INSERT INTO action_log (id, company_id, actor, action_type, autonomy_level, description, data_involved, status)
  VALUES (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'ai', 'send_low_stock_alert', 'notify', 'x', '{}', 'pending');
SQL
expect_fail() {  # $1 = description, $2 = SQL that MUST be rejected
  if psql -q -d sai_validate_fresh -v ON_ERROR_STOP=1 -c "$2" >/dev/null 2>&1; then
    echo "✗ expected the database to refuse: $1"; exit 1
  fi
  echo "✓ refused as intended: $1"
}
expect_fail "editing an audit record"   "UPDATE audit_events SET event_type = 'tampered'"
expect_fail "deleting an audit record"  "DELETE FROM audit_events"
expect_fail "same email with different capitals" "INSERT INTO users (company_id, email, password_hash, role) VALUES ('22222222-2222-2222-2222-222222222222', 'owner@smoke.test', 'h', 'viewer')"
psql -q -d sai_validate_fresh -v ON_ERROR_STOP=1 -c "INSERT INTO companies (id, name, conductor_end_user_id) VALUES ('55555555-5555-5555-5555-555555555555', 'A', 'end_usr_1')"
expect_fail "one QuickBooks Desktop connection attached to two companies" "INSERT INTO companies (name, conductor_end_user_id) VALUES ('B', 'end_usr_1')"
# deleting a whole company still removes its audit rows (cascade)
psql -q -d sai_validate_fresh -v ON_ERROR_STOP=1 -c "DELETE FROM companies WHERE id = '22222222-2222-2222-2222-222222222222'"
left=$(psql -At -d sai_validate_fresh -c "select count(*) from audit_events")
[ "$left" = "0" ] || { echo "✗ company delete did not cascade to audit_events"; exit 1; }
echo "✓ Deleting a company removes its own records."

if [ -f database/schema.sql ]; then
  bash scripts/build_schema.sh --check
fi

psql -q -d "$ADMIN_DB" -c "DROP DATABASE IF EXISTS sai_validate_fresh" -c "DROP DATABASE IF EXISTS sai_validate_legacy"
echo "✓ Schema validation passed."
