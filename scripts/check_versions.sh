#!/usr/bin/env bash
# Verify every manifest's version matches the canonical /VERSION file.
# The single source of truth for the release version is the VERSION file.
set -euo pipefail
cd "$(dirname "$0")/.."

WANT="$(tr -d '[:space:]' < VERSION)"
fail=0

check() { # name expected actual
  if [ "$2" != "$WANT" ]; then
    echo "MISMATCH: $1 = '$2' (expected '$WANT')"
    fail=1
  else
    echo "ok: $1 = $2"
  fi
}

grep_ver() { grep -m1 "$2" "$1" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }

check "backend app.__version__" "$(grep -m1 '__version__' backend/app/__init__.py | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
check "desktop/src-tauri/tauri.conf.json" "$(grep_ver desktop/src-tauri/tauri.conf.json '"version"')"
check "desktop/src-tauri/Cargo.toml" "$(grep -m1 '^version' desktop/src-tauri/Cargo.toml | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
check "desktop/package.json" "$(grep_ver desktop/package.json '"version"')"
check "frontend/package.json" "$(grep_ver frontend/package.json '"version"')"
# The connector is now a shipped artifact (packaged by release-connector.yml and
# bundled into the desktop installer), so its reported version must not drift —
# it had silently stayed at 1.0.0 while everything else moved to 1.0.6.
check "connector __version__" "$(grep -m1 '__version__' connector/__init__.py | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "Version drift detected. Update all manifests to match /VERSION ($WANT)."
  exit 1
fi
echo "All versions match VERSION=$WANT"
