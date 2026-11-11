#!/usr/bin/env bash
# Build database/schema.sql — ONE file containing every migration in order.
#
# supabase/migrations/ is the single source of truth. database/schema.sql is a
# generated copy for people who set the database up by pasting into the
# Supabase SQL editor. It is safe to run on an empty database or on an existing
# one (every statement is written to be re-runnable).
#
#   scripts/build_schema.sh          regenerate database/schema.sql
#   scripts/build_schema.sh --check  fail if it is out of date (used in CI)
set -euo pipefail
cd "$(dirname "$0")/.."

out="$(mktemp)"
{
  echo "-- ═════════════════════════════════════════════════════════════════════"
  echo "-- SecretaryAI database schema — GENERATED FILE, do not edit by hand."
  echo "-- Source: supabase/migrations/*.sql   Rebuild: scripts/build_schema.sh"
  echo "-- Safe to run on a new database or to bring an existing one up to date."
  echo "-- ═════════════════════════════════════════════════════════════════════"
  for f in supabase/migrations/*.sql; do
    echo
    echo "-- ▶ $(basename "$f")"
    cat "$f"
  done
} > "$out"

if [ "${1:-}" = "--check" ]; then
  if ! diff -q "$out" database/schema.sql >/dev/null; then
    echo "✗ database/schema.sql is out of date. Run scripts/build_schema.sh and commit the result."
    rm -f "$out"; exit 1
  fi
  echo "✓ database/schema.sql matches supabase/migrations."
  rm -f "$out"
else
  mkdir -p database
  mv "$out" database/schema.sql
  echo "Wrote database/schema.sql"
fi
