#!/usr/bin/env bash
# Local-only multi-statement tests in disposable Supabase Postgres.
set -euo pipefail
test "$#" -eq 1
test -f "$1"
test -z "$SUPABASE_ACCESS_TOKEN"
test -z "$DATABASE_URL"
db_container="$(docker ps --filter 'label=com.supabase.cli.project=finance-canonical-clean' --format '{{.Names}}' | grep '^supabase_db_' | head -n 1)"
test -n "$db_container"
docker exec -i "$db_container" psql -X -U postgres -d postgres -v ON_ERROR_STOP=1 < "$1"
