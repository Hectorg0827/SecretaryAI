#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SecretaryAI — One-Command Full-Stack Installer (Linux / macOS)
#
# Usage:
#   curl -sSL https://get.secretaryai.com | bash
#   bash install.sh
#   bash install.sh --version 1.2.0
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
INSTALL_DIR="${SECRETARY_INSTALL_DIR:-$HOME/secretaryai}"
COMPOSE_URL="https://github.com/secretaryai/secretaryai/releases/latest/download/docker-compose.prod.yml"
ENV_EXAMPLE_URL="https://github.com/secretaryai/secretaryai/releases/latest/download/.env.example"
VERSION="${SECRETARY_VERSION:-latest}"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}  →${RESET} $*"; }
ok()   { echo -e "${GREEN}  ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}  !${RESET} $*"; }
fail() { echo -e "${RED}  ✗${RESET} $*"; exit 1; }

# ── Parse args ────────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --version=*) VERSION="${arg#*=}" ;;
    --dir=*)     INSTALL_DIR="${arg#*=}" ;;
    --help)
      echo "Usage: install.sh [--version=X.Y.Z] [--dir=/path/to/install]"
      exit 0 ;;
  esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║       SecretaryAI Installer          ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────
log "Checking prerequisites..."

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install it from https://docs.docker.com/get-docker/"
ok "Docker found: $(docker --version | head -1)"

# Check Docker is running
docker info >/dev/null 2>&1 || fail "Docker daemon is not running. Start Docker and try again."
ok "Docker daemon is running"

# Check docker compose (v2 plugin or v1 standalone)
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  fail "Docker Compose not found. Install it from https://docs.docker.com/compose/install/"
fi
ok "Docker Compose found: $($COMPOSE_CMD version --short 2>/dev/null || echo 'v1')"

# curl or wget
if command -v curl >/dev/null 2>&1; then
  FETCH="curl -fsSL"
elif command -v wget >/dev/null 2>&1; then
  FETCH="wget -qO-"
else
  fail "curl or wget is required."
fi

# ── Install directory ─────────────────────────────────────────────────────────
log "Installing to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── Download compose file ─────────────────────────────────────────────────────
if [ -f "docker-compose.prod.yml" ]; then
  warn "docker-compose.prod.yml already exists — updating."
fi
log "Downloading docker-compose.prod.yml ..."
$FETCH "$COMPOSE_URL" > docker-compose.prod.yml
ok "docker-compose.prod.yml downloaded"

# ── Set up .env ───────────────────────────────────────────────────────────────
if [ -f ".env" ]; then
  warn ".env already exists — skipping prompt (delete it to reconfigure)."
else
  log "Downloading .env template ..."
  $FETCH "$ENV_EXAMPLE_URL" > .env.example

  echo ""
  echo -e "${BOLD}Configure SecretaryAI${RESET}"
  echo -e "Press Enter to skip optional values.\n"

  # Required
  prompt_required() {
    local var="$1"; local label="$2"; local val=""
    while [ -z "$val" ]; do
      read -rp "  $label: " val
      [ -z "$val" ] && echo "  (required — cannot be empty)"
    done
    echo "$var=$val" >> .env
  }

  # Optional
  prompt_optional() {
    local var="$1"; local label="$2"; local val=""
    read -rp "  $label (optional): " val
    echo "$var=${val}" >> .env
  }

  # Auto-generate a secret key
  if command -v openssl >/dev/null 2>&1; then
    SECRET_KEY=$(openssl rand -hex 32)
  else
    SECRET_KEY=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 64)
  fi

  # Write .env
  cat .env.example > .env

  # Patch required values interactively
  echo "SECRET_KEY=${SECRET_KEY}" >> .env.override 2>/dev/null || true

  echo ""
  echo "──── Required ────────────────────────────────────"
  prompt_required "ANTHROPIC_API_KEY"          "Anthropic API key (sk-ant-...)"
  prompt_required "SUPABASE_URL"               "Supabase project URL (https://xxx.supabase.co)"
  prompt_required "SUPABASE_ANON_KEY"          "Supabase anon key"
  prompt_required "SUPABASE_SERVICE_ROLE_KEY"  "Supabase service role key"
  prompt_required "DATABASE_URL"               "Postgres connection string (postgresql://postgres:...)"

  echo ""
  echo "──── Optional integrations ───────────────────────"
  prompt_optional "CONDUCTOR_API_KEY"      "Conductor API key (QuickBooks Desktop)"
  prompt_optional "INTUIT_CLIENT_ID"       "Intuit client ID (QuickBooks Online)"
  prompt_optional "INTUIT_CLIENT_SECRET"   "Intuit client secret (QuickBooks Online)"
  prompt_optional "SENDGRID_API_KEY"       "SendGrid API key (email reports)"
  prompt_optional "GOOGLE_CLIENT_ID"       "Google client ID (Gmail/Sheets)"
  prompt_optional "GOOGLE_CLIENT_SECRET"   "Google client secret"
  prompt_optional "OPENAI_API_KEY"         "OpenAI API key (voice input)"

  # Merge overrides into .env — replace placeholder values with user input
  while IFS='=' read -r key val; do
    [ -z "$key" ] || [[ "$key" == \#* ]] && continue
    sed -i.bak "s|^${key}=.*|${key}=${val}|" .env 2>/dev/null || true
  done < .env.override
  rm -f .env.override .env.bak .env.example

  # Inject auto-generated secret key
  sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET_KEY}|" .env 2>/dev/null || \
    echo "SECRET_KEY=${SECRET_KEY}" >> .env
  rm -f .env.bak

  ok ".env configured"
fi

# ── Pull images ───────────────────────────────────────────────────────────────
echo ""
log "Pulling Docker images (this may take a few minutes on first run)..."
VERSION="$VERSION" $COMPOSE_CMD -f docker-compose.prod.yml pull
ok "Images pulled"

# ── Start services ────────────────────────────────────────────────────────────
log "Starting SecretaryAI..."
VERSION="$VERSION" $COMPOSE_CMD -f docker-compose.prod.yml up -d
ok "Services started"

# ── Health check ──────────────────────────────────────────────────────────────
log "Waiting for API to be ready..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    ok "API is healthy"
    break
  fi
  sleep 2
  [ "$i" -eq 30 ] && warn "API health check timed out — check logs with: $COMPOSE_CMD -f docker-compose.prod.yml logs api"
done

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║   SecretaryAI is running!            ║${RESET}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Web UI:   ${CYAN}http://localhost${RESET}"
echo -e "  API docs: ${CYAN}http://localhost:8000/docs${RESET}"
echo ""
echo -e "  Manage:   ${BOLD}cd $INSTALL_DIR${RESET}"
echo -e "  Logs:     ${BOLD}$COMPOSE_CMD -f docker-compose.prod.yml logs -f${RESET}"
echo -e "  Stop:     ${BOLD}$COMPOSE_CMD -f docker-compose.prod.yml down${RESET}"
echo -e "  Update:   ${BOLD}bash <(curl -sSL https://get.secretaryai.com)${RESET}"
echo ""
