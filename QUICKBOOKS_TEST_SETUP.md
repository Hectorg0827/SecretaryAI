# QuickBooks Test Data Setup Guide

This guide walks you through getting QuickBooks Desktop loaded with realistic test data for SecretaryAI development.

## Quick Start

### 1. Generate Test Data

```bash
cd /Users/hectorgarcia/SecretaryAI
python scripts/generate_test_distributor.py
```

This creates:

- `distributor_test_data.json` — Full dataset (customers, vendors, inventory, invoices, etc.)
- `distributor_customers.csv` — Customer import file
- `distributor_inventory.csv` — Inventory import file

### 2. Install QuickBooks Desktop

#### Option A: Free Trial (Recommended)

- Download: [QuickBooks Desktop Free Trial](https://quickbooks.intuit.com/learn-support/en-us/set-up-quickbooks-desktop/top-faq-s-about-quickbooks-desktop-editions/00/185155)
- Install and open
- Choose "Sample Rock Castle Construction" when prompted (has pre-loaded data)
- Or start with "New Company"

#### Option B: Use Existing QB Installation

If you already have QB installed, open or create a company file.

### 3. Import Test Data into QB

#### Option A: Manual CSV Import (Quickest)

**Import Customers:**

1. In QB, go: **File** → **Utilities** → **Import** → **IIF Files**
   - Or: **Lists** → **Customer:Job List** → Right-click → **Import**
   - Choose `distributor_customers.csv`
2. QB will guide you through field mapping
3. Click **Import**

**Import Inventory:**

1. Go: **Lists** → **Item List** → Right-click → **Import**
   - Choose `distributor_inventory.csv`
2. Map fields:
   - `name` → Item Name
   - `sku` → Item Number
   - `description` → Description
   - `sales_price` → Sales Price
   - `quantity_on_hand` → Qty On Hand
3. Click **Import**

#### Option B: Use Conductor API (Automatic)

**Prerequisites:**

- QB Desktop connected via Conductor
- Conductor API key (from env var `CONDUCTOR_API_KEY`)
- End-user ID (from your SecretaryAI dashboard)

**Upload all data automatically:**

```bash
python scripts/generate_test_distributor.py \
  --upload \
  --conductor-key YOUR_CONDUCTOR_API_KEY \
  --end-user-id YOUR_END_USER_ID
```

This uploads:

- ✓ Customers
- ✓ Vendors
- ✓ Inventory items
- ✓ Invoices (via API, dates vary)
- ✓ Purchase orders (via API)

### 4. Verify Data in QB

After import, check in QB:

**Customers:**

```
Lists → Customer:Job List
```

Should show: Whole Foods Market, Trader Joe's, Local Organic Market, etc.

**Inventory:**

```
Lists → Item List
```

Should show: Organic Bananas, Romaine Lettuce, Broccoli Crowns, etc.

**Invoices (optional, if using Conductor):**

```
Lists → Customers → Double-click customer → Recent Transactions
```

## Test Data Overview

### Customers (5 total)

| Name                      | Type                  | Credit Limit | Location      |
| -----                     | -----                 | -----        | -----         |
| Whole Foods Market        | Retail Chain          | $50,000      | Austin, TX    |
| Trader Joe's              | Distribution Center   | $75,000      | Pasadena, CA  |
| Local Organic Market      | Independent Grocery   | $25,000      | Boston, MA    |
| Fresh Express Foods       | Regional Wholesaler   | $30,000      | Miami, FL     |
| Corner Store Collective   | Cooperative           | $15,000      | Seattle, WA   |

### Vendors (4 total)

| Name                      | Products              | Location            |
| -----                     | -----                 | -----               |
| Dole Food Company         | Fruits & Vegetables   | Westlake Village, CA |
| ConAgra Foods             | Packaged Goods        | Chicago, IL         |
| Fresh Del Monte Produce   | Fresh Produce         | New York, NY        |
| Smucker Company           | Jams & Spreads        | Orrville, OH        |

### Inventory (8 total)

| SKU      | Item              | Unit Price    | Stock |
| -----    | -----             | -----         | -----  |
| PROD-001 | Organic Bananas   | $0.89/lb      | 1,240  |
| PROD-002 | Romaine Lettuce   | $24.99/case   | 145    |
| PROD-003 | Broccoli Crowns   | $34.99/case   | 89     |
| PROD-004 | Orange Juice      | $8.99/gal     | 456    |
| PROD-005 | Grape Jelly       | $3.99/jar     | 678    |
| PROD-006 | Peanut Butter     | $4.49/jar     | 521    |
| PROD-007 | Canned Corn       | $15.99/case   | 234    |
| PROD-008 | Pasta             | $1.49/box     | 867    |

### Transactions (Generated)

- **Invoices**: 13 past invoices over 90 days (mix of paid & open)
- **Purchase Orders**: 9 PO records (mix of open & received)
- **Payments**: 4+ recorded customer payments

## Using with SecretaryAI

### 1. Connect QB Desktop to SecretaryAI

**Desktop App Setup:**

1. Launch SecretaryAI desktop app
2. Go: **Settings** → **Integrations** → **QuickBooks Desktop**
3. Click **Set up**
4. Follow Conductor auth flow
5. Once connected, test sync: **Settings** → **Sync Now**

**Backend Setup (if needed):**

```python
from app.connectors.qb_desktop import QBDesktopAdapter

adapter = QBDesktopAdapter(
    api_key="your_conductor_api_key",
    end_user_id="your_end_user_id"
)

# Fetch your test data
customers = await adapter.get_customers()
inventory = await adapter.get_inventory()
invoices = await adapter.get_invoices(date_from, date_to)
```

### 2. Test AI Features Against Real QB Data

With the test distributor loaded:

**Inventory Management:**

- "What items are below reorder point?"
- "Show me slow-moving inventory"
- "What's our current stock of bananas?"

**Sales Analysis:**

- "Which customers owe us the most?"
- "Show Q2 revenue by customer"
- "What's our payment rate?"

**Purchasing:**

- "Generate PO for low-stock items"
- "Compare supplier pricing"
- "Which vendor gives us best terms?"

## Troubleshooting

### CSV Import Fails

**Problem:** "Cannot import this file type"

**Solution:**

1. Save CSV files in UTF-8 without BOM
2. Or convert to Excel format (`.xls`):
   ```bash
   python scripts/csv_to_excel.py distributor_customers.csv
   ```

### Conductor API Upload Fails

**Problem:** "401 Unauthorized"

**Solution:**

```bash
# Check Conductor API key is set
echo $CONDUCTOR_API_KEY
# Update .env with correct key from Conductor dashboard
```

**Problem:** "404 Not Found - endpoint doesn't exist"

**Solution:**

- The Conductor API may not yet support creating customers/vendors/inventory via this endpoint
- Use manual CSV import instead
- Or check Conductor docs: https://docs.conductor.is

### QB Shows No Data After Import

**Problem:** Data doesn't appear in lists

**Solution:**

1. Close and reopen QB file
2. Go: **Lists** → **Chart of Accounts** (forces refresh)
3. Check import log: **File** → **Utilities** → **Audit Trail**

## Next Steps

### Load More Realistic Data

Generate variants:

```bash
# Generate with different seed (different random data)
python scripts/generate_test_distributor.py --seed 12345

# Generate larger dataset (100+ customers)
python scripts/generate_test_distributor.py --scale 10
```

### Automate Test Data Refresh

Add to `.env`:

```
TEST_DATA_AUTO_REFRESH=true
TEST_DATA_REFRESH_INTERVAL_DAYS=7
```

Then SecretaryAI will regenerate and reload test data weekly.

### Create QB Connection for Testing

Once QB has test data, connect it:

```bash
# Create an end-user in Conductor for your QB instance
curl -X POST https://api.conductor.is/v1/end-users \
  -H "Authorization: Bearer YOUR_CONDUCTOR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "sourceId": "test-company-001",
    "email": "test@secretaryai.com",
    "companyName": "Test Distributor Inc."
  }'
```

Copy the returned `id` and use it in your `.env`:

```
CONDUCTOR_END_USER_ID=returned_id_from_above
```

## See Also

- [QuickBooks Desktop Import Help](https://quickbooks.intuit.com/learn-support/en-us/import-data/00/185000)
- [Conductor API Docs](https://docs.conductor.is)
- [SecretaryAI QB Integration Docs](../README.md#quickbooks-integration)
