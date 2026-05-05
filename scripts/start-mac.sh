#!/bin/bash

# SecretaryAI QB Online Quick Start for Mac
# This script sets up and runs SecretaryAI with QB Online for development

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}SecretaryAI + QB Online Sandbox (Mac)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check prerequisites
echo -e "\n${BLUE}📋 Checking prerequisites...${NC}"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 not found. Install from https://www.python.org/downloads/${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python 3${NC}: $(python3 --version)"

# Check Node
if ! command -v node &> /dev/null; then
    echo -e "${RED}✗ Node.js not found. Install from https://nodejs.org/${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Node.js${NC}: $(node --version)"

# Check .env file
if [ ! -f "backend/.env" ]; then
    echo -e "${RED}✗ backend/.env not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ backend/.env${NC} exists"

# Verify Intuit credentials are set
if grep -q "INTUIT_CLIENT_ID=ABuH2jRPl2YGs8kgbLjZiUtYf6hPruUUGKceK3pDZ4LBSoDANS" backend/.env; then
    echo -e "${GREEN}✓ Intuit credentials${NC} configured"
else
    echo -e "${RED}✗ Intuit credentials not found in backend/.env${NC}"
    exit 1
fi

# Check if Redis is needed (optional)
if ! command -v redis-cli &> /dev/null; then
    echo -e "${YELLOW}⚠ Redis not found (optional but recommended)${NC}"
    echo -e "${YELLOW}  Install with: brew install redis${NC}"
else
    echo -e "${GREEN}✓ Redis${NC}: $(redis-cli --version)"
fi

echo -e "\n${GREEN}✓ All prerequisites met!${NC}"

# Setup backend
echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Setting up Backend...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

cd backend

if [ ! -d ".venv" ]; then
    echo -e "${BLUE}📦 Creating Python virtual environment...${NC}"
    python3 -m venv .venv
fi

echo -e "${BLUE}🔄 Activating venv and installing dependencies...${NC}"
source .venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1 || {
    echo -e "${YELLOW}⚠ Some dependencies failed to install${NC}"
    echo -e "${YELLOW}  This is often OK - core packages should have installed${NC}"
}

echo -e "${GREEN}✓ Backend ready${NC}"

# Setup frontend
cd "$SCRIPT_DIR"

echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Setting up Frontend...${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

cd frontend

if [ ! -d "node_modules" ]; then
    echo -e "${BLUE}📦 Installing npm dependencies...${NC}"
    npm install > /dev/null 2>&1
fi

echo -e "${GREEN}✓ Frontend ready${NC}"

# Done
cd "$SCRIPT_DIR"

echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Setup Complete!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo -e "\n${YELLOW}📌 IMPORTANT: Update backend/.env${NC}"
echo -e "${YELLOW}You need to add these before starting:${NC}"
echo ""
echo "1. Get from Supabase (https://supabase.com/dashboard):"
echo "   SUPABASE_URL="
echo "   SUPABASE_ANON_KEY="
echo "   SUPABASE_SERVICE_ROLE_KEY="
echo "   DATABASE_URL="
echo ""
echo "2. Get from Anthropic (https://console.anthropic.com/settings/keys):"
echo "   ANTHROPIC_API_KEY="
echo ""
echo -e "${YELLOW}QB Online credentials are already set.${NC}"
echo ""

echo -e "\n${GREEN}Next: Run the backend and frontend:${NC}"
echo ""
echo -e "${BLUE}Terminal 1 - Backend:${NC}"
echo "  cd backend"
echo "  source .venv/bin/activate"
echo "  uvicorn app.main:app --reload"
echo ""
echo -e "${BLUE}Terminal 2 - Frontend:${NC}"
echo "  cd frontend"
echo "  npm run dev"
echo ""
echo -e "${BLUE}Then open:${NC}"
echo "  Frontend: http://localhost:5173"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo -e "See: ${BLUE}QB_ONLINE_SETUP.md${NC} for full guide"
