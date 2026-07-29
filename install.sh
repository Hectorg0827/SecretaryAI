#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SecretaryAI — Self-Hosted Backend Deployer (Linux / macOS)
#
# FOR OPERATORS self-hosting the SecretaryAI backend stack with Docker.
# This is NOT the desktop app — end users download the installer from the
# GitHub Releases page (see docs/RELEASE_RUNBOOK.md).
#
# Usage (download, REVIEW, then run — do not pipe a remote script into a shell):
#   bash install.sh
#   bash install.sh --version 1.2.0
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
INSTALL_DIR="${SECRETARY_INSTALL_DIR:-$HOME/secretaryai}"
COMPOSE_URL="https://github.com/Hectorg0827/SecretaryAI/releases/latest/download/docker-compose.prod.yml"
ENV_EXAMPLE_URL="https://github.com/Hectorg0827/SecretaryAI/releases/latest/download/.env.example"
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

docker info >/dev/null 2>&1 || fail "Docker daemon is not running. Start Docker and try again."
ok "Docker daemon is running"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  fail "Docker Compose not found. Install it from https://docs.docker.com/compose/install/"
fi
ok "Docker Compose found: $($COMPOSE_CMD version --short 2>/dev/null || echo 'v1')"

if command -v curl >/dev/null 2>&1; then
  FETCH="curl -fsSL"
elif command -v wget >/dev/null 2>&1; then
  FETCH="wget -qO-"
else
  fail "curl or wget is required."
fi

# ── Install directory ─────────────────────────────────────────────────────────
log "Installing to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR" || fail "Cannot create install directory: $INSTALL_DIR"
[ -w "$INSTALL_DIR" ]   || fail "Install directory is not writable: $INSTALL_DIR"
cd "$INSTALL_DIR"

# ── Download compose file ─────────────────────────────────────────────────────
if [ -f "docker-compose.prod.yml" ]; then
  warn "docker-compose.prod.yml already exists — updating."
fi
log "Downloading docker-compose.prod.yml ..."
$FETCH "$COMPOSE_URL" > docker-compose.prod.yml \
  || fail "Failed to download docker-compose.prod.yml — check your internet connection."
ok "docker-compose.prod.yml downloaded"

# ── Set up .env ───────────────────────────────────────────────────────────────
if [ -f ".env" ]; then
  warn ".env already exists — skipping prompt (delete it to reconfigure)."
else
  log "Downloading .env template ..."
  $FETCH "$ENV_EXAMPLE_URL" > .env.example \
    || fail "Failed to download .env.example — check your internet connection."

  cp .env.example .env

  # Safe env setter: uses grep+append instead of sed to avoid injection with
  # special characters (|, &, \, =, $) in values like API keys and passwords.
  set_env() {
    local key="$1" val="$2"
    # Remove any existing line for this key (handles commented-out lines too)
    grep -v "^${key}=" .env > .env.tmp 2>/dev/null && mv .env.tmp .env || true
    # Append the correct value — printf is safe for any content in $val
    printf '%s=%s\n' "$key" "$val" >> .env
  }

  # Auto-generate a cryptographically random 64-char hex secret key
  if command -v openssl >/dev/null 2>&1; then
    SECRET_KEY=$(openssl rand -hex 32)
  elif [ -r /dev/urandom ]; then
    SECRET_KEY=$(LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c 64) || true
    [ "${#SECRET_KEY}" -eq 64 ] \
      || fail "Failed to generate secret key — please install openssl and retry."
  else
    fail "Cannot generate secret key. Please install openssl and retry."
  fi
  set_env "SECRET_KEY" "$SECRET_KEY"

  # Required
  prompt_required() {
    local key="$1" label="$2" val=""
    while [ -z "$val" ]; do
      read -rp "  $label: " val
      [ -z "$val" ] && echo "  (required — cannot be empty)"
    done
    set_env "$key" "$val"
  }

  # Optional — only writes to .env if user provides a value
  prompt_optional() {
    local key="$1" label="$2" val=""
    read -rp "  $label (optional, Enter to skip): " val
    [ -n "$val" ] && set_env "$key" "$val"
  }

  echo ""
  echo -e "${BOLD}Configure SecretaryAI${RESET}"
  echo ""

  echo "──── Required ────────────────────────────────────"
  prompt_required "ANTHROPIC_API_KEY"          "Anthropic API key (sk-ant-...)"
  prompt_required "SUPABASE_URL"               "Supabase project URL (https://xxx.supabase.co)"
  prompt_required "SUPABASE_ANON_KEY"          "Supabase anon key"
  prompt_required "SUPABASE_SERVICE_ROLE_KEY"  "Supabase service role key"
  prompt_required "DATABASE_URL"               "Postgres connection string (postgresql://postgres:...)"

  echo ""
  echo "──── Optional integrations ───────────────────────"
  prompt_optional "CONDUCTOR_API_KEY"    "Conductor API key (QuickBooks Desktop)"
  prompt_optional "INTUIT_CLIENT_ID"     "Intuit client ID (QuickBooks Online)"
  prompt_optional "INTUIT_CLIENT_SECRET" "Intuit client secret (QuickBooks Online)"
  prompt_optional "SENDGRID_API_KEY"     "SendGrid API key (email reports)"
  prompt_optional "GOOGLE_CLIENT_ID"     "Google client ID (Gmail/Sheets)"
  prompt_optional "GOOGLE_CLIENT_SECRET" "Google client secret"
  prompt_optional "OPENAI_API_KEY"       "OpenAI API key (voice input)"

  rm -f .env.example
  ok ".env configured"
fi

# ── Pull images ───────────────────────────────────────────────────────────────
echo ""
log "Pulling Docker images (this may take a few minutes on first run)..."
VERSION="$VERSION" $COMPOSE_CMD -f docker-compose.prod.yml pull \
  || fail "Failed to pull Docker images. Check your internet connection and try again."
ok "Images pulled"

# ── Start services ────────────────────────────────────────────────────────────
log "Starting SecretaryAI..."
VERSION="$VERSION" $COMPOSE_CMD -f docker-compose.prod.yml up -d \
  || fail "Failed to start services. Run: $COMPOSE_CMD -f docker-compose.prod.yml logs"
ok "Services started"

# ── Health check ──────────────────────────────────────────────────────────────
log "Waiting for API to be ready (up to 60s)..."
HEALTHY=0
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    HEALTHY=1
    ok "API is healthy"
    break
  fi
  sleep 2
done

if [ "$HEALTHY" -eq 0 ]; then
  echo ""
  warn "API did not become healthy in time. Recent logs:"
  $COMPOSE_CMD -f docker-compose.prod.yml logs --tail=20 api || true
  echo ""
  fail "Startup failed. Fix the issue above and rerun: $COMPOSE_CMD -f docker-compose.prod.yml up -d"
fi

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
echo -e "  Update:   ${BOLD}re-run: bash install.sh${RESET} (after pulling the latest scripts)"
echo ""
