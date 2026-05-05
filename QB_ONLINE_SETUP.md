# QB Online Setup for Mac Development

This guide walks you through setting up QuickBooks Online sandbox on Mac for SecretaryAI testing.

## ✅ You Have Everything You Need

Your Intuit Developer credentials are ready. Now let's set them up.

## Step 1: Update Your .env File

Edit `backend/.env` and add these Intuit credentials:

```bash
# ── QuickBooks Online (OAuth 2.0) ─────────────────────────────────────────────
INTUIT_CLIENT_ID=ABuH2jRPl2YGs8kgbLjZiUtYf6hPruUUGKceK3pDZ4LBSoDANS
INTUIT_CLIENT_SECRET=P40BQz5o53KzdzAfzAbve9DUKK7rYqUj0JMwVOZI
INTUIT_ENVIRONMENT=sandbox
INTUIT_REDIRECT_URI=http://localhost:8000/auth/qbo/callback
```

**Notes:**
- `INTUIT_ENVIRONMENT=sandbox` uses Intuit's free sandbox (pre-loaded with test data)
- `INTUIT_REDIRECT_URI` must match the one registered in your Intuit app
- Your local dev server runs on `http://localhost:8000`

## Step 2: Verify App Registration in Intuit Developer Dashboard

1. Go: https://developer.intuit.com/app/developer/myapps
2. Find your app
3. Click **Settings** → **Keys & OAuth**
4. Verify **Redirect URIs** includes:
   - `http://localhost:8000/auth/qbo/callback` (development)
   - `https://yourdomain.com/auth/qbo/callback` (production)

If not, add it:
- **Redirect URI**: `http://localhost:8000/auth/qbo/callback`
- Click **Save**

## Step 3: Start SecretaryAI Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Make sure .env has your Intuit credentials
uvicorn app.main:app --reload
```

Backend will be at: `http://localhost:8000`

## Step 4: Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend will be at: `http://localhost:5173`

## Step 5: Connect QB Online from Frontend

1. Open frontend: `http://localhost:5173`
2. Log in (create test account)
3. Go: **Settings** → **Integrations** → **QuickBooks Online**
4. Click **Connect with Intuit**
5. You'll be redirected to Intuit OAuth
6. Log in with your Intuit sandbox account (or create one)
7. Click **Authorize** to grant access
8. You'll be redirected back to SecretaryAI with a connected QB Online account ✓

## What's in the QB Online Sandbox

Your sandbox comes pre-loaded with:

### Sample Company: "Sample Rock Castle Construction"

**Customers (5+)**
- Kristy Snyder
- Omer Douglas
- Michael Patrick
- ... (and more)

**Invoices**
- Multiple invoices across different dates
- Different statuses (paid, unpaid, draft)

**Inventory**
- Lumber, nails, fasteners (construction materials)
- Various quantities and pricing

**Vendors**
- Construction material suppliers
- Equipment rental companies

**Chart of Accounts**
- Full accounting structure
- Revenue, expense, asset accounts

## Step 6: Test QB Online Integration

Once connected, test with SecretaryAI:

### Via Web UI

**Chat queries:**
- "What's our inventory status?"
- "Show me unpaid invoices"
- "Which customers owe us money?"
- "What's our total revenue?"

**Settings → Sync Now**
- Manually triggers a QB data pull
- Checks connection health

### Via Python/Backend

```python
from app.connectors.qb_online import QBOnlineAdapter
from app.config import get_settings

settings = get_settings()

# Get the OAuth token from your database
# (After user connects via web UI)
adapter = QBOnlineAdapter(
    realm_id="your_realm_id",  # From QBO auth response
    access_token="your_access_token",  # From database
)

# Test connection
health = await adapter.test_connection()
print(f"Connected: {health}")

# Fetch data
customers = await adapter.get_customers()
print(f"Found {len(customers)} customers")

invoices = await adapter.get_invoices(date_from, date_to)
print(f"Found {len(invoices)} invoices")

inventory = await adapter.get_inventory()
print(f"Found {len(inventory)} inventory items")
```

## How QB Online Works vs QB Desktop

| Feature | QB Desktop (Conductor) | QB Online |
| --- | --- | --- |
| **Platform** | Windows-only app | Web-based, Mac/Linux/Windows |
| **Setup** | Complex Web Connector install | OAuth 2.0 (automatic) |
| **Authentication** | API key + End-User ID | OAuth 2.0 (browser redirect) |
| **Testing** | Real QB file or trial | Free sandbox (pre-loaded) |
| **Real-time Sync** | Webhooks | Webhooks + polling |
| **Data Freshness** | Minutes (via Web Connector) | Minutes (via API sync) |
| **Read Operations** | ✓ Customers, invoices, inventory, POs | ✓ Customers, invoices, inventory, transactions |
| **Write Operations** | ✓ Create invoices, POs, payments | ✓ Create invoices, payments, accounts |

**For testing:** QB Online sandbox is actually **better** — it has sample data pre-loaded and requires zero setup.

## Testing Your Integration

### Verify Tokens Are Stored

After connecting, check your Supabase database:

```sql
SELECT 
  id,
  email,
  qb_type,
  qbo_realm_id,
  qbo_connected_at
FROM companies
WHERE id = your_company_id;
```

Should show:
- `qb_type: 'online'`
- `qbo_realm_id: '1234567890'` (the QB Organization ID)
- `qbo_connected_at: 2026-05-03...`

### Test Data Sync

```python
# From SecretaryAI backend
from app.connectors.qb_online import QBOnlineAdapter
from app.config import get_settings

# Backend will automatically sync when a user connects
# Check logs for "QB Online sync completed"
```

## Troubleshooting

### "Invalid redirect URI"

**Problem:** OAuth callback fails with redirect URI mismatch

**Solution:**
1. Check Intuit Developer Portal → Your App → Settings → Keys & OAuth
2. Verify `INTUIT_REDIRECT_URI` in `.env` matches exactly
3. Intuit is case-sensitive: `http://localhost:8000/auth/qbo/callback`

### "Unauthorized - token expired"

**Problem:** Connection works initially but fails after a few hours

**Solution:**
- QB Online tokens expire after **1 hour**
- Backend automatically refreshes them (see `tasks/qbo_token_refresh.py`)
- If manual refresh needed:
  ```python
  from app.auth.oauth import refresh_qbo_token
  new_tokens = await refresh_qbo_token(refresh_token)
  ```

### "No customers/invoices found"

**Problem:** QB Online connected but no data shows

**Solution:**
1. **Sandbox may not have sample data yet:**
   - Log into QB Online sandbox: https://quickbooks.intuit.com
   - Create a test company with sample data
   - QB Online will auto-populate it

2. **Or use our test data:**
   - QB Online won't import CSV directly
   - But you can use `distributor_test_data.json` with a mock adapter
   - Or manually create invoices in QB Online for testing

### "Backend can't reach QB Online API"

**Problem:** Timeout or connection refused

**Solution:**
```bash
# Check backend logs
tail -f logs/app.log

# Verify internet connection
curl https://quickbooks.api.intuit.com/v3/

# Check credentials in .env
echo $INTUIT_CLIENT_ID  # Should not be empty
```

## Production Setup

When deploying to production:

### Update .env

```bash
# Use real Intuit app credentials (different from sandbox)
INTUIT_ENVIRONMENT=production
INTUIT_REDIRECT_URI=https://yourdomain.com/auth/qbo/callback
FRONTEND_URL=https://yourdomain.com  # For OAuth redirects
```

### Register Production Redirect URI

1. Intuit Developer Portal → Your App → Settings → Keys & OAuth
2. Add production redirect URI:
   - `https://yourdomain.com/auth/qbo/callback`
3. Save

### Backend HTTPS

Ensure backend is HTTPS (OAuth requires secure URLs):
```bash
# nginx config or AWS ALB should handle TLS
# Redirect HTTP → HTTPS
```

## Next Steps

1. ✅ Add Intuit credentials to `.env`
2. ✅ Start backend + frontend
3. ✅ Connect QB Online via Settings → Integrations
4. ✅ Test with chat queries and data sync
5. **Optional:** Use mock QB adapter for offline testing (no internet needed)

---

## See Also

- [QB Online API Docs](https://developer.intuit.com/app/developer/qbo/docs/get-started/hello-world)
- [Intuit OAuth 2.0 Flow](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2-0)
- [SecretaryAI QB Integration](../README.md#quickbooks-integration)
