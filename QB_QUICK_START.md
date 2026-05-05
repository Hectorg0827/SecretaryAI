# Quick Start: Load QB Desktop with Test Data

You now have everything needed to get QuickBooks Desktop loaded with realistic test distributor data for SecretaryAI development.

## 🚀 Three Quick Steps

### Step 1: Generate Test Data (Already Done!)

```bash
cd /Users/hectorgarcia/SecretaryAI
python3 scripts/generate_test_distributor.py
```

This created three files in your SecretaryAI folder:

1. **`distributor_test_data.json`** (31 KB)
   - Complete dataset: 5 customers, 4 vendors, 8 inventory items
   - 13 invoices (sales history over 90 days)
   - 8 purchase orders (procurement history)
   - 4 payments (recorded customer payments)

2. **`distributor_customers.csv`** (907 bytes)
   - Ready to import directly into QB Desktop
   - All 5 retail customers with contact info, credit limits, payment terms

3. **`distributor_inventory.csv`** (1 KB)
   - Ready to import directly into QB Desktop
   - 8 products with SKUs, costs, selling prices, reorder points

### Step 2: Install/Open QuickBooks Desktop

**Option A: Free Trial (Recommended)**

- Download: https://quickbooks.intuit.com/learn-support/en-us/set-up-quickbooks-desktop
- Install and launch
- Choose "New Company" when prompted
- Or use the "Sample Rock Castle Construction" (it has pre-loaded data you can override)

**Option B: Use Existing QB Installation**

Just open/create a company file

### Step 3: Import Test Data into QB (2 minutes)

**Import Customers:**

1. In QB, go: **Lists** → **Customer:Job List**
2. Right-click → **Import** (or **File** → **Utilities** → **Import**)
3. Choose `distributor_customers.csv` from your SecretaryAI folder
4. QB will show field mapping - just click through
5. Click **Import** ✓

**Import Inventory Items:**

1. In QB, go: **Lists** → **Item List**
2. Right-click → **Import**
3. Choose `distributor_inventory.csv`
4. Map fields:
   - `name` → Item Name
   - `sku` → Item Number  
   - `sales_price` → Sales Price
5. Click **Import** ✓

**Verify it worked:**

- Open **Lists** → **Customer:Job List** → Should see 5 customers (Whole Foods, Trader Joe's, etc.)
- Open **Lists** → **Item List** → Should see 8 products (Organic Bananas, Lettuce, etc.)

## 📊 What You Get

### Customers (5)
- **Whole Foods Market** — $50K credit limit, Austin TX
- **Trader Joe's Distribution** — $75K credit limit, Pasadena CA
- **Local Organic Market** — $25K credit limit, Boston MA
- **Fresh Express Foods** — $30K credit limit, Miami FL
- **Corner Store Collective** — $15K credit limit, Seattle WA

### Inventory (8 Products)
- Organic Bananas, Romaine Lettuce, Broccoli Crowns
- Orange Juice, Grape Jelly, Peanut Butter
- Canned Corn, Pasta
- All with realistic pricing, costs, stock levels, and reorder points

### Vendors (4)
- Dole Food Company, ConAgra Foods, Fresh Del Monte, Smucker Company

### Transactions (Generated History)
- 13 past invoices (sent to customers)
- 8 purchase orders (received from suppliers)
- 4 payments (recorded)

## 🔗 Connect to SecretaryAI

Once QB has test data:

1. **Desktop App**: Settings → QuickBooks Desktop → Set up
2. **Follow the Conductor auth flow** to connect
3. **Sync** to test SecretaryAI can read your QB data

Then test queries like:
- "What's our inventory status?"
- "Which customers owe us the most?"
- "Create a PO for low-stock items"
- "Show me sales trends"

## 🔄 Regenerate Different Data

Generate new test data each time:

```bash
python3 scripts/generate_test_distributor.py
```

This will overwrite the CSVs with new data while keeping the same structure.

## 📚 Full Documentation

See: [QUICKBOOKS_TEST_SETUP.md](./QUICKBOOKS_TEST_SETUP.md)

For troubleshooting, importing via Conductor API, or advanced options.

## ✅ You're Ready!

1. ✓ Test data generator created
2. ✓ CSV files for easy QB import
3. ✓ Full documentation provided
4. ✓ Next: Install QB Desktop and import the CSVs above

**Any issues?** Check [QUICKBOOKS_TEST_SETUP.md](./QUICKBOOKS_TEST_SETUP.md#troubleshooting) for troubleshooting.
