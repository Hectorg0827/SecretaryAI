#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SecretaryAI — deployment smoke test
#
# Proves a deployed backend is genuinely serving, not just that something
# answered on port 443. This is the evidence for GA exit criterion 1
# ("Backend reachable at a stable HTTPS host; /health returns status: ok").
#
# Usage:
#     bash scripts/smoke_test.sh https://your-backend-host
#
# Exit code 0 = every required check passed. Non-zero = do not ship.
# Run it from a machine OUTSIDE the deployment (a laptop, not the server), or
# it proves nothing about public reachability.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

BASE="${1:-}"
if [ -z "$BASE" ]; then
  echo "usage: bash scripts/smoke_test.sh https://your-backend-host" >&2
  exit 2
fi
BASE="${BASE%/}"   # strip trailing slash

case "$BASE" in
  https://*) ;;
  *) echo "FAIL  the URL must start with https:// (got '$BASE')" >&2; exit 2 ;;
esac

HOST="${BASE#https://}"; HOST="${HOST%%/*}"; HOST="${HOST%%:*}"

pass=0; fail=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
note() { printf '        %s\n' "$1"; }

echo "SecretaryAI smoke test against $BASE"
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') (UTC)"
echo

# ── 1. DNS ───────────────────────────────────────────────────────────────────
echo "1. DNS"
if getent hosts "$HOST" >/dev/null 2>&1 || host "$HOST" >/dev/null 2>&1; then
  ok "$HOST resolves"
else
  bad "$HOST does not resolve — the host does not exist yet"
  note "Nothing below can pass. Finish the deploy and point DNS first."
  echo; echo "RESULT: NOT DEPLOYED ($pass passed, $fail failed)"; exit 1
fi

# ── 2. /health ───────────────────────────────────────────────────────────────
echo
echo "2. GET /health"
BODY_FILE="$(mktemp)"; trap 'rm -f "$BODY_FILE"' EXIT
CODE="$(curl -sS -m 25 -o "$BODY_FILE" -w '%{http_code}' "$BASE/health" 2>"$BODY_FILE.err" || echo 000)"
BODY="$(cat "$BODY_FILE" 2>/dev/null || true)"

if [ "$CODE" = "000" ]; then
  bad "no HTTP response (TLS failure, connection refused, or timeout)"
  note "$(head -1 "$BODY_FILE.err" 2>/dev/null || echo 'no detail')"
elif [ "$CODE" = "200" ]; then
  ok "HTTP 200"
else
  bad "HTTP $CODE (expected 200; 503 means the app is up but a dependency is down)"
fi

STATUS="$(printf '%s' "$BODY" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("status",""))
except Exception: print("")' 2>/dev/null || true)"

if [ "$STATUS" = "ok" ]; then
  ok 'status: "ok"  <- exit criterion 1 satisfied'
elif [ -n "$STATUS" ]; then
  bad "status: \"$STATUS\" (criterion 1 needs exactly \"ok\")"
else
  bad "response is not the expected JSON health payload"
  note "first 200 bytes: $(printf '%s' "$BODY" | head -c 200)"
fi

# Per-dependency detail: tells you WHICH backing service is misconfigured.
printf '%s' "$BODY" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
v=d.get("version")
if v: print(f"        version: {v}")
for k,s in (d.get("checks") or {}).items():
    mark="PASS" if s=="ok" else "FAIL"
    print(f"  {mark}  dependency {k}: {s}")
' 2>/dev/null || true

if printf '%s' "$BODY" | grep -q '"redis": *"error"'; then
  note 'redis error -> REDIS_URL is wrong or the Redis service is not running.'
  fail=$((fail+1))
fi
if printf '%s' "$BODY" | grep -q '"db": *"error"'; then
  note 'db error -> SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY wrong, or the schema'
  note '             files were never applied (database/schema.sql, then _v2, then _v3).'
  fail=$((fail+1))
fi

# ── 3. The app is really routing, not a platform placeholder page ────────────
echo
echo "3. Application routing"
# An unauthenticated /auth/me must be rejected by the app (401/403).
# A 404 here means something other than SecretaryAI is answering.
ME="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/auth/me" || echo 000)"
case "$ME" in
  401|403) ok "GET /auth/me -> $ME (the real app is answering and auth is enforced)" ;;
  200)     bad "GET /auth/me -> 200 without a token — authentication is NOT enforced" ;;
  404)     bad "GET /auth/me -> 404 — this host is not serving SecretaryAI" ;;
  *)       bad "GET /auth/me -> $ME (expected 401/403)" ;;
esac

# ── 4. API docs must not be world-readable in production ────────────────────
echo
echo "4. API docs exposure"
DOCS="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/openapi.json" || echo 000)"
if [ "$DOCS" = "200" ]; then
  bad "/openapi.json is publicly readable — the full API surface is exposed"
  note "Unset DOCS_API_KEY (or set DEBUG=false) in the backend environment."
else
  ok "/openapi.json is not public (HTTP $DOCS)"
fi

# ── 5. HTTPS only ───────────────────────────────────────────────────────────
echo
echo "5. Transport"
if curl -sS -m 20 -o /dev/null --http1.1 "$BASE/health" 2>/dev/null; then
  ok "TLS certificate is valid (curl verified it without --insecure)"
else
  bad "TLS verification failed"
fi

echo
echo "────────────────────────────────────────────────────"
if [ "$fail" -eq 0 ]; then
  echo "RESULT: GREEN — $pass checks passed."
  echo "Exit criterion 1 is satisfied for $BASE"
  exit 0
fi
echo "RESULT: RED — $fail check(s) failed, $pass passed."
echo "Do not set API_URL or cut a release until this is green."
exit 1
