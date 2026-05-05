# 🎉 QB Online Setup Complete!

Your SecretaryAI + QuickBooks Online integration is ready for Mac development.

## ✅ What's Been Set Up

### 1. **Intuit Credentials** (`backend/.env`)
```
INTUIT_CLIENT_ID=ABuH2jRPl2YGs8kgbLjZiUtYf6hPruUUGKceK3pDZ4LBSoDANS
INTUIT_CLIENT_SECRET=P40BQz5o53KzdzAfzAbve9DUKK7rYqUj0JMwVOZI
INTUIT_ENVIRONMENT=sandbox
INTUIT_REDIRECT_URI=http://localhost:8000/auth/qbo/callback
```
✓ Credentials are stored securely in `.env` (never committed to git)

### 2. **QB Online Setup Guide** (`QB_ONLINE_SETUP.md`)
Complete documentation for:
- Setting up OAuth 2.0 flow
- Connecting from SecretaryAI frontend
- Testing the integration
- Production deployment
- Troubleshooting

### 3. **Quick Start Script** (`scripts/start-mac.sh`)
Automated setup for Mac with:
- Python venv creation
- npm dependencies
- Verification of Intuit credentials
- Prerequisites checking

### 4. **Test Data Generator** (from earlier)
If you later want to use QB Desktop or a mock adapter:
- `scripts/generate_test_distributor.py`
- `distributor_test_data.json`
- `distributor_customers.csv`, `distributor_inventory.csv`

## 🚀 Getting Started (2 Steps)

### Step 1: Complete Your .env File

Edit `backend/.env` and add these missing fields (get from your dashboard):

```bash
# From Supabase (https://supabase.com/dashboard)
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_ANON_KEY=eyJhbGci...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGci...
DATABASE_URL=postgresql://postgres:...@db.your-project-id.supabase.co:5432/postgres

# From Anthropic (https://console.anthropic.com/settings/keys)
ANTHROPIC_API_KEY=sk-ant-api03-...
```

QB Online credentials are **already set**.

### Step 2: Run Setup Script

```bash
# Make script executable
chmod +x scripts/start-mac.sh

# Run setup
./scripts/start-mac.sh
```

This will:
- ✓ Check Python 3 and Node.js
- ✓ Create Python virtual environment
- ✓ Install dependencies
- ✓ Verify Intuit credentials are configured

## 📱 Starting Development

Once setup is complete, start the services in separate terminals:

**Terminal 1 - Backend API:**
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```
Backend runs on: `http://localhost:8000`
API docs: `http://localhost:8000/docs`

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
```
Frontend runs on: `http://localhost:5173`

## 🔌 Connect QB Online

1. Open frontend: `http://localhost:5173`
2. Create a test account (or log in)
3. Go: **Settings** → **Integrations** → **QuickBooks Online**
4. Click **"Connect with Intuit"**
5. Log in with your Intuit sandbox account (create one if needed)
6. Click **Authorize** to grant SecretaryAI access
7. Done! You're connected to QB Online sandbox ✓

## 💾 What's in QB Online Sandbox

Your free sandbox comes with sample company data:
- **Sample Rock Castle Construction** (pre-loaded)
- Customers, vendors, invoices, inventory, chart of accounts
- Ready to test immediately — no manual data entry needed

## 🧪 Test Queries

Once connected, try these in SecretaryAI:

**Inventory Questions:**
- "What's our inventory status?"
- "Which items are below reorder point?"
- "Show me slow-moving products"

**Sales Questions:**
- "What's our total revenue?"
- "Which customers owe us money?"
- "Show me unpaid invoices"

**Customer Questions:**
- "List our top customers"
- "Which customers bought in the last 30 days?"

## 📚 Full Documentation

- **[QB_ONLINE_SETUP.md](QB_ONLINE_SETUP.md)** — Complete QB Online guide
- **[QB_QUICK_START.md](QB_QUICK_START.md)** — 3-step quick start for test data (optional)
- **[README.md](README.md)** — SecretaryAI overview

## ⚙️ Architecture

```
┌─────────────────────────────────────────────────────┐
│ SecretaryAI Frontend (React + Vite)                 │
│ http://localhost:5173                               │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ OAuth 2.0
┌─────────────────────────────────────────────────────┐
│ Intuit OAuth Portal                                 │
│ (Browser redirect for authorization)                │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ Redirect back
┌─────────────────────────────────────────────────────┐
│ SecretaryAI Backend (FastAPI)                       │
│ http://localhost:8000                               │
│ - Handles OAuth callback                            │
│ - Stores QB tokens in Supabase                      │
│ - Syncs QB data via QBOnlineAdapter                │
└──────────────────────┬──────────────────────────────┘
                       │
                       ↓ REST API (OAuth token)
┌─────────────────────────────────────────────────────┐
│ QB Online API                                       │
│ https://quickbooks.api.intuit.com/v3/company/...   │
│ - Fetch customers, invoices, inventory             │
│ - Create payments, purchase orders                 │
└─────────────────────────────────────────────────────┘
```

## 🐛 Troubleshooting

### "Module not found" errors when starting backend
```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
```

### "Cannot find npm" or "npm install failed"
```bash
# Update npm
npm install -g npm

# Try again
cd frontend
npm install
npm run dev
```

### "Redirect URI mismatch" on OAuth
1. Check your Intuit app: https://developer.intuit.com/app/developer/myapps
2. Go: Settings → Keys & OAuth
3. Add redirect URI: `http://localhost:8000/auth/qbo/callback`
4. Save and restart backend

### "No Supabase URL" error
Your `.env` is missing Supabase credentials. Fill them in and restart:
```bash
# In backend terminal:
# Ctrl+C to stop
# Edit backend/.env
# Restart: uvicorn app.main:app --reload
```

## ✨ What's Next

### Option A: Develop with QB Online
- Use real QB data (sandbox or production)
- Test inventory, sales, procurement features
- Build against cloud QB API

### Option B: Use Mock Adapter for Offline Testing
- Use our test data generator
- Create a `MockQBAdapter` that loads from JSON
- Fast, offline testing without QB connection
- Perfect for CI/CD pipelines

### Option C: Both (Recommended)
- Mock adapter for unit tests
- QB Online for integration tests
- Real QB Desktop (Windows VM) for UAT

## 📞 Need Help?

- **QB Online API Docs:** https://developer.intuit.com/app/developer/qbo
- **SecretaryAI Docs:** See `/docs` folder
- **Intuit Developer:** https://developer.intuit.com

---

**You're all set! 🚀 Start with Terminal 1 (backend) and Terminal 2 (frontend), then connect QB Online from the web UI.**
