#!/usr/bin/env bash
# Standalone local Supabase CLI v3 verification for Issue #211 5-row broken baseline
# recovery, single-transaction rollback atomicity, and post-replay least privilege.
# Never uses remote tokens or hosted endpoints.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/lionbi-hosted-replay-verify.XXXXXX")"
trap 'cd "$WORK_DIR" && supabase stop >/dev/null 2>&1 || true; rm -rf "$WORK_DIR"' EXIT

test -z "${SUPABASE_ACCESS_TOKEN:-}"
test -z "${DATABASE_URL:-}"

(cd "$WORK_DIR" && supabase init)
rm -f "$WORK_DIR/supabase/migrations/"*.sql

python3 "$REPO_ROOT/docs/ops/finance_canonical_replay_20261119/hosted_replay.py" \
  --project-ref ontqkqgofkghsdgtqwsm \
  --output "$WORK_DIR/hosted_replay.sql"
python3 "$REPO_ROOT/docs/ops/finance_canonical_replay_20261119/hosted_post_replay_least_privilege.py" \
  --project-ref ontqkqgofkghsdgtqwsm \
  --mode finance-uat \
  --output "$WORK_DIR/post_finance_uat.sql"
python3 "$REPO_ROOT/docs/ops/finance_canonical_replay_20261119/hosted_post_replay_least_privilege.py" \
  --project-ref ontqkqgofkghsdgtqwsm \
  --mode rls-acceptance \
  --output "$WORK_DIR/post_rls_acceptance.sql"

(cd "$WORK_DIR" && supabase start)
DB_CONTAINER="$(docker ps --filter "label=com.supabase.cli.project=$(basename "$WORK_DIR")" --format '{{.Names}}' | grep '^supabase_db_' | head -n 1)"
test -n "$DB_CONTAINER"

run_psql() {
  docker exec -i "$DB_CONTAINER" psql -X -U postgres -d postgres -v ON_ERROR_STOP=1
}

# Seed exact Issue #211 broken preview state: 0 public tables + 5 empty baseline rows.
run_psql <<'SQL'
TRUNCATE supabase_migrations.schema_migrations;
INSERT INTO supabase_migrations.schema_migrations(version, name, statements) VALUES
  ('20240101000000', 'initial_schema', NULL),
  ('20240601000000', 'v2_additions', ARRAY[]::text[]),
  ('20240901000000', 'v3_integrations', NULL),
  ('20261017000000', 'connector_install_secret_digest', NULL),
  ('20261017010000', 'stock_ledger', ARRAY[]::text[]);
SQL

# 1. Prove single-transaction rollback atomicity on failure at migration #46.
python3 - "$WORK_DIR/hosted_replay.sql" "$WORK_DIR/hosted_replay_abort.sql" <<'PY'
from pathlib import Path
import sys
src = Path(sys.argv[1]).read_text()
assert src.endswith("\nCOMMIT;\n")
Path(sys.argv[2]).write_text(
    "SET app.disposable_project_ref = 'ontqkqgofkghsdgtqwsm';\n"
    + src[:-len("COMMIT;\n")]
    + "DO $$ BEGIN RAISE EXCEPTION 'simulated_post_replay_abort'; END $$;\nCOMMIT;\n"
)
PY
if run_psql < "$WORK_DIR/hosted_replay_abort.sql"; then
  echo "ERROR: simulated abort unexpectedly succeeded" >&2
  exit 1
fi
run_psql <<'SQL'
DO $$
BEGIN
  IF (SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public') <> 0 THEN
    RAISE EXCEPTION 'Atomicity failure: public tables persisted after aborted replay';
  END IF;
  IF (SELECT count(*) FROM supabase_migrations.schema_migrations) <> 5
     OR EXISTS (SELECT 1 FROM supabase_migrations.schema_migrations WHERE coalesce(cardinality(statements), 0) <> 0) THEN
    RAISE EXCEPTION 'Atomicity failure: schema_migrations mutated after aborted replay';
  END IF;
END $$;
SQL

# 2. Execute clean atomic replay + post-replay least-privilege modes.
{
  echo "SET app.disposable_project_ref = 'ontqkqgofkghsdgtqwsm';"
  cat "$WORK_DIR/hosted_replay.sql"
} | run_psql

{
  echo "SET app.disposable_project_ref = 'ontqkqgofkghsdgtqwsm';"
  cat "$WORK_DIR/post_rls_acceptance.sql"
} | run_psql

echo "PASS: local CLI v3 broken-5-row recovery, rollback atomicity, and least-privilege bootstrap verified"
