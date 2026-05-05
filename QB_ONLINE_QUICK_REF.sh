#!/usr/bin/env bash

# 🎯 QUICK REFERENCE: QB Online Setup for SecretaryAI on Mac
# 
# Everything you need is set up. Here's the quickest path forward.

cat << 'EOF'

╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║                    🚀 QB Online for SecretaryAI - Mac                      ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝

✅ COMPLETED:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Intuit Client ID & Secret stored in backend/.env
✓ QB Online OAuth 2.0 configured for sandbox
✓ Test data generator created (optional, for offline testing)
✓ Setup script created (checks prerequisites)
✓ Documentation complete (QB_ONLINE_SETUP.md)


📋 WHAT YOU NEED TO DO:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣  Add Supabase & Anthropic credentials to backend/.env

    Edit: backend/.env

    Add these (get from your dashboards):
    ┌────────────────────────────────────────────┐
    │ SUPABASE_URL=https://your-project...      │
    │ SUPABASE_ANON_KEY=eyJhbGci...             │
    │ SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...    │
    │ DATABASE_URL=postgresql://...             │
    │ ANTHROPIC_API_KEY=sk-ant-api03-...       │
    └────────────────────────────────────────────┘

    QB Online credentials are ALREADY SET ✓


2️⃣  Run the setup script

    ./scripts/start-mac.sh

    This will:
    ✓ Check you have Python 3 and Node.js
    ✓ Create Python virtual environment
    ✓ Install pip and npm dependencies
    ✓ Verify Intuit credentials


3️⃣  Start Backend (Terminal 1)

    cd backend
    source .venv/bin/activate
    uvicorn app.main:app --reload

    API runs on: http://localhost:8000
    API docs:   http://localhost:8000/docs


4️⃣  Start Frontend (Terminal 2)

    cd frontend
    npm run dev

    Frontend runs on: http://localhost:5173


5️⃣  Connect QB Online (in browser)

    1. Open: http://localhost:5173
    2. Create account or log in
    3. Go: Settings → Integrations → QuickBooks Online
    4. Click: "Connect with Intuit"
    5. Log in with Intuit sandbox account (or create one)
    6. Click: Authorize
    7. Done! Connected to QB Online ✓


🎯 WHAT YOU GET:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

QB Online Sandbox (Free, Pre-loaded)
├─ Sample Company: Rock Castle Construction
├─ Customers (5+)
├─ Invoices (20+)
├─ Inventory Items (10+)
├─ Vendors & Accounts
└─ Ready to query with AI

SecretaryAI Features
├─ Ask AI questions about QB data
├─ Automatic QB data syncing
├─ Invoice and payment management
├─ Inventory visibility
└─ Integration tests against real QB


📚 DOCUMENTATION:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

QB_ONLINE_READY.md     ← Start here (you are here)
QB_ONLINE_SETUP.md     ← Full detailed guide
README.md              ← SecretaryAI overview
QB_QUICK_START.md      ← Alternative: test data generator


⚡ QUICK COMMANDS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Run setup (checks prerequisites)
./scripts/start-mac.sh

# Start backend
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload

# Start frontend
cd frontend && npm run dev

# View test data (optional)
cat distributor_test_data.json

# Generate new test data (optional)
python3 scripts/generate_test_distributor.py


🔍 VERIFY SETUP:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Check Intuit credentials are set:
  grep "INTUIT_CLIENT_ID" backend/.env

Check Python is available:
  python3 --version

Check Node is available:
  node --version

Check Supabase URL is in .env:
  grep "SUPABASE_URL" backend/.env


❓ TROUBLESHOOTING:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"Module not found" when starting backend:
  → cd backend && source .venv/bin/activate && pip install -r requirements.txt

"Cannot connect to Supabase":
  → Add SUPABASE_URL and keys to backend/.env

"Redirect URI mismatch" on OAuth:
  → Go to https://developer.intuit.com/app/developer/myapps
  → Settings → Keys & OAuth → Add: http://localhost:8000/auth/qbo/callback

"QB Online shows no customers":
  → Create sample data in QB Online: https://quickbooks.intuit.com
  → Or wait for first sync (check backend logs)


🎉 YOU'RE READY!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Next step: Fill in backend/.env with Supabase & Anthropic credentials,
then run ./scripts/start-mac.sh

Questions? See QB_ONLINE_SETUP.md for detailed docs.

EOF
