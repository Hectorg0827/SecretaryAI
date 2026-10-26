#!/usr/bin/env bash
# Disposable HTTP fixture proves routing, never business/provider acceptance.
set -euo pipefail
network="secretary-web-test-$$"
web="${network}-web"
upstream="${network}-api"
fixture=$(mktemp -d)
cleanup() {
  docker rm -f "$web" "$upstream" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$fixture"
}
trap cleanup EXIT
cat > "$fixture/default.conf" <<'NGINX'
server {
  listen 80;
  location /auth/ { default_type application/json; return 401 '{"detail":"Invalid credentials"}'; }
  location /api/ { default_type application/json; return 403 '{"detail":"Permission denied"}'; }
  location /webhooks/ { default_type application/json; return 400 '{"detail":"Invalid signature"}'; }
}
NGINX
docker network create "$network" >/dev/null
docker run -d --name "$upstream" --network "$network" --network-alias fixture-api \
  -v "$fixture/default.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.27-alpine >/dev/null
docker build -t secretary-web-routing-test frontend
docker run -d --name "$web" --network "$network" -p 127.0.0.1:18081:8080 \
  -e PORT=8080 -e API_UPSTREAM=http://fixture-api \
  secretary-web-routing-test >/dev/null
ready=0
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18081/health > "$fixture/health"; then ready=1; break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then docker logs "$web"; exit 1; fi
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"status":"ok","service":"secretaryai-web"}' "$fixture/health"
for route in /login /register /settings /logistics; do
  curl -fsS "http://127.0.0.1:18081$route" | grep -q '<div id="root">'
done
for entry in auth/login:401 api/dashboard/summary:403 webhooks/test:400; do
  path=${entry%:*}; expected=${entry##*:}
  actual=$(curl -sS -o "$fixture/body" -w '%{http_code}' -X POST "http://127.0.0.1:18081/$path")
  test "$actual" = "$expected"
  python3 -c 'import json,sys; assert "detail" in json.load(open(sys.argv[1]))' "$fixture/body"
done
curl -fsSI http://127.0.0.1:18081/login > "$fixture/headers"
grep -qi 'X-Content-Type-Options: nosniff' "$fixture/headers"
grep -qi 'Cache-Control: no-cache' "$fixture/headers"
echo 'PASS: container port, SPA deep links, JSON proxy errors, security headers and HTML cache policy'
