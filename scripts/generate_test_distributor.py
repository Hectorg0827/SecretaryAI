#!/usr/bin/env python3
"""
Generate realistic test data for a fake distributor company in QuickBooks.

This script creates a complete distributor dataset including:
- Customers (retail chains, independent stores)
- Vendors (suppliers, manufacturers)
- Inventory items (products with pricing)
- Invoices (past sales)
- Purchase orders (orders to vendors)

Usage:
    python scripts/generate_test_distributor.py [--upload] [--conductor-key KEY] [--end-user-id ID]

    --upload              Upload directly to QB via Conductor API
    --conductor-key KEY   Conductor API key (required if --upload)
    --end-user-id ID      Conductor end-user ID (required if --upload)

Output:
    - distributor_test_data.json  (all test data)
    - distributor_customers.csv   (importable to QB)
    - distributor_inventory.csv   (importable to QB)

Requirements:
    pip install httpx
"""

import asyncio
import csv
import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


class DistributorTestDataGenerator:
    """Generate realistic distributor test data."""

    def __init__(self):
        self.customers = []
        self.vendors = []
        self.inventory = []
        self.invoices = []
        self.purchase_orders = []
        self.payments = []

    def generate_all(self):
        """Generate complete test dataset."""
        log.info("Generating test distributor data...")
        self._generate_customers()
        self._generate_vendors()
        self._generate_inventory()
        self._generate_invoices()
        self._generate_purchase_orders()
        self._generate_payments()
        log.info("✓ Generated test data")
        return self

    def _generate_customers(self):
        """Create customer list with retail chains and independent stores."""
        customers_data = [
            {
                "name": "Whole Foods Market",
                "type": "customer",
                "email": "purchasing@wholefoods.com",
                "phone": "(512) 477-4455",
                "billing_address": {
                    "street": "550 Bowie Street",
                    "city": "Austin",
                    "state": "TX",
                    "postal_code": "78703",
                    "country": "USA",
                },
                "shipping_address": {
                    "street": "550 Bowie Street",
                    "city": "Austin",
                    "state": "TX",
                    "postal_code": "78703",
                },
                "credit_limit": Decimal("50000"),
                "payment_terms": "Net 30",
                "notes": "Tier-1 retail customer, weekly orders",
            },
            {
                "name": "Trader Joe's Distribution Center",
                "type": "customer",
                "email": "logistics@traderjoes.com",
                "phone": "(626) 599-3700",
                "billing_address": {
                    "street": "800 S. Raymond Ave",
                    "city": "Pasadena",
                    "state": "CA",
                    "postal_code": "91105",
                    "country": "USA",
                },
                "shipping_address": {
                    "street": "800 S. Raymond Ave",
                    "city": "Pasadena",
                    "state": "CA",
                    "postal_code": "91105",
                },
                "credit_limit": Decimal("75000"),
                "payment_terms": "Net 30",
                "notes": "Large-scale regional distributor",
            },
            {
                "name": "Local Organic Market - Boston",
                "type": "customer",
                "email": "orders@localorganic.com",
                "phone": "(617) 555-0147",
                "billing_address": {
                    "street": "123 Newbury Street",
                    "city": "Boston",
                    "state": "MA",
                    "postal_code": "02115",
                    "country": "USA",
                },
                "shipping_address": {
                    "street": "123 Newbury Street",
                    "city": "Boston",
                    "state": "MA",
                    "postal_code": "02115",
                },
                "credit_limit": Decimal("25000"),
                "payment_terms": "Net 15",
                "notes": "Independent grocery chain, 12 locations",
            },
            {
                "name": "Fresh Express Foods - Miami",
                "type": "customer",
                "email": "procurement@freshexpress.com",
                "phone": "(305) 555-0123",
                "billing_address": {
                    "street": "456 Biscayne Blvd",
                    "city": "Miami",
                    "state": "FL",
                    "postal_code": "33132",
                    "country": "USA",
                },
                "shipping_address": {
                    "street": "456 Biscayne Blvd",
                    "city": "Miami",
                    "state": "FL",
                    "postal_code": "33132",
                },
                "credit_limit": Decimal("30000"),
                "payment_terms": "Net 30",
                "notes": "Regional wholesaler, 15 retail partners",
            },
            {
                "name": "Corner Store Collective",
                "type": "customer",
                "email": "admin@cornerstores.com",
                "phone": "(206) 555-0178",
                "billing_address": {
                    "street": "789 Pike Place",
                    "city": "Seattle",
                    "state": "WA",
                    "postal_code": "98101",
                    "country": "USA",
                },
                "shipping_address": {
                    "street": "789 Pike Place",
                    "city": "Seattle",
                    "state": "WA",
                    "postal_code": "98101",
                },
                "credit_limit": Decimal("15000"),
                "payment_terms": "Net 7",
                "notes": "Small independent cooperative",
            },
        ]

        for i, cust in enumerate(customers_data, 1):
            self.customers.append(
                {
                    "qb_id": f"CUST-{i:03d}",
                    **cust,
                }
            )

    def _generate_vendors(self):
        """Create vendor list (suppliers, manufacturers)."""
        vendors_data = [
            {
                "name": "Dole Food Company",
                "type": "vendor",
                "email": "orders@dole.com",
                "phone": "(831) 728-4900",
                "billing_address": {
                    "street": "1 Dole Drive",
                    "city": "Westlake Village",
                    "state": "CA",
                    "postal_code": "91362",
                    "country": "USA",
                },
                "payment_terms": "Net 45",
                "account_number": "DOLE-5001",
                "notes": "Primary fruit & vegetable supplier",
            },
            {
                "name": "ConAgra Foods",
                "type": "vendor",
                "email": "b2b@conagrafoods.com",
                "phone": "(402) 240-4000",
                "billing_address": {
                    "street": "222 Merchandise Mart",
                    "city": "Chicago",
                    "state": "IL",
                    "postal_code": "60654",
                    "country": "USA",
                },
                "payment_terms": "Net 60",
                "account_number": "CAG-2234",
                "notes": "Packaged goods distributor",
            },
            {
                "name": "Fresh Del Monte Produce",
                "type": "vendor",
                "email": "sales@freshdelmonte.com",
                "phone": "(212) 384-8500",
                "billing_address": {
                    "street": "570 Lexington Avenue",
                    "city": "New York",
                    "state": "NY",
                    "postal_code": "10022",
                    "country": "USA",
                },
                "payment_terms": "Net 30",
                "account_number": "FDM-4456",
                "notes": "Fresh produce, bananas & melons",
            },
            {
                "name": "Smucker Company",
                "type": "vendor",
                "email": "vendor@smuckers.com",
                "phone": "(330) 682-3000",
                "billing_address": {
                    "street": "1 Strawberry Lane",
                    "city": "Orrville",
                    "state": "OH",
                    "postal_code": "44667",
                    "country": "USA",
                },
                "payment_terms": "Net 45",
                "account_number": "SJM-7788",
                "notes": "Jams, preserves, specialty foods",
            },
        ]

        for i, vendor in enumerate(vendors_data, 1):
            self.vendors.append(
                {
                    "qb_id": f"VEND-{i:03d}",
                    **vendor,
                }
            )

    def _generate_inventory(self):
        """Create inventory items (products)."""
        inventory_data = [
            {
                "name": "Organic Bananas - Per Pound",
                "sku": "PROD-001",
                "description": "Fair-trade organic bananas, premium grade",
                "unit_of_measure": "lb",
                "quantity_on_hand": Decimal("1240"),
                "reorder_point": Decimal("500"),
                "purchase_cost": Decimal("0.45"),
                "sales_price": Decimal("0.89"),
                "taxable": True,
                "vendor_id": "VEND-001",
                "preferred_vendor": "Dole Food Company",
            },
            {
                "name": "Romaine Lettuce - Case",
                "sku": "PROD-002",
                "description": "Fresh romaine lettuce, 24 heads per case",
                "unit_of_measure": "case",
                "quantity_on_hand": Decimal("145"),
                "reorder_point": Decimal("50"),
                "purchase_cost": Decimal("12.50"),
                "sales_price": Decimal("24.99"),
                "taxable": True,
                "vendor_id": "VEND-001",
                "preferred_vendor": "Dole Food Company",
            },
            {
                "name": "Broccoli Crowns - Case",
                "sku": "PROD-003",
                "description": "Fresh organic broccoli crowns, 12 per case",
                "unit_of_measure": "case",
                "quantity_on_hand": Decimal("89"),
                "reorder_point": Decimal("40"),
                "purchase_cost": Decimal("18.00"),
                "sales_price": Decimal("34.99"),
                "taxable": True,
                "vendor_id": "VEND-003",
                "preferred_vendor": "Fresh Del Monte Produce",
            },
            {
                "name": "Orange Juice - Gallon",
                "sku": "PROD-004",
                "description": "100% pure orange juice, 1 gallon jug",
                "unit_of_measure": "each",
                "quantity_on_hand": Decimal("456"),
                "reorder_point": Decimal("200"),
                "purchase_cost": Decimal("4.25"),
                "sales_price": Decimal("8.99"),
                "taxable": True,
                "vendor_id": "VEND-003",
                "preferred_vendor": "Fresh Del Monte Produce",
            },
            {
                "name": "Grape Jelly - 18 oz Jar",
                "sku": "PROD-005",
                "description": "Smuckers premium grape jelly",
                "unit_of_measure": "each",
                "quantity_on_hand": Decimal("678"),
                "reorder_point": Decimal("200"),
                "purchase_cost": Decimal("1.85"),
                "sales_price": Decimal("3.99"),
                "taxable": False,
                "vendor_id": "VEND-004",
                "preferred_vendor": "Smucker Company",
            },
            {
                "name": "Peanut Butter - 18 oz Jar",
                "sku": "PROD-006",
                "description": "Smuckers creamy peanut butter",
                "unit_of_measure": "each",
                "quantity_on_hand": Decimal("521"),
                "reorder_point": Decimal("150"),
                "purchase_cost": Decimal("2.15"),
                "sales_price": Decimal("4.49"),
                "taxable": False,
                "vendor_id": "VEND-004",
                "preferred_vendor": "Smucker Company",
            },
            {
                "name": "Canned Corn - Case",
                "sku": "PROD-007",
                "description": "Whole kernel corn, 24 cans per case",
                "unit_of_measure": "case",
                "quantity_on_hand": Decimal("234"),
                "reorder_point": Decimal("100"),
                "purchase_cost": Decimal("8.50"),
                "sales_price": Decimal("15.99"),
                "taxable": False,
                "vendor_id": "VEND-002",
                "preferred_vendor": "ConAgra Foods",
            },
            {
                "name": "Pasta - 1 lb Box",
                "sku": "PROD-008",
                "description": "Premium semolina pasta assorted shapes",
                "unit_of_measure": "case",
                "quantity_on_hand": Decimal("867"),
                "reorder_point": Decimal("300"),
                "purchase_cost": Decimal("0.75"),
                "sales_price": Decimal("1.49"),
                "taxable": False,
                "vendor_id": "VEND-002",
                "preferred_vendor": "ConAgra Foods",
            },
        ]

        for i, item in enumerate(inventory_data, 1):
            self.inventory.append(
                {
                    "qb_id": f"INV-{i:03d}",
                    **item,
                }
            )

    def _generate_invoices(self):
        """Create past invoices (sales history)."""
        # Generate invoices over the past 90 days
        today = date.today()
        invoice_count = 0

        for days_ago in range(5, 91, 7):  # One invoice per week
            invoice_date = today - timedelta(days=days_ago)
            customer = self.customers[invoice_count % len(self.customers)]
            items = self.inventory[: 3 + (invoice_count % 3)]  # Vary items per invoice

            line_items = []
            total = Decimal("0")
            for item in items:
                qty = Decimal(str(2 + (invoice_count % 5)))
                amount = qty * item["sales_price"]
                total += amount
                line_items.append(
                    {
                        "item_id": item["qb_id"],
                        "item_name": item["name"],
                        "quantity": str(qty),
                        "unit_price": str(item["sales_price"]),
                        "amount": str(amount),
                    }
                )

            invoice_count += 1
            is_paid = invoice_count % 3 == 0  # Every 3rd invoice is paid

            self.invoices.append(
                {
                    "qb_id": f"INV-{invoice_count:05d}",
                    "customer_id": customer["qb_id"],
                    "customer_name": customer["name"],
                    "invoice_date": invoice_date.isoformat(),
                    "due_date": (invoice_date + timedelta(days=30)).isoformat(),
                    "line_items": line_items,
                    "total_amount": str(total),
                    "balance_remaining": str(0 if is_paid else total),
                    "status": "paid" if is_paid else "open",
                    "memo": f"Standard order - {customer['name']}",
                }
            )

    def _generate_purchase_orders(self):
        """Create past purchase orders (procurement history)."""
        today = date.today()
        po_count = 0

        for days_ago in range(10, 120, 14):  # One PO every 2 weeks
            po_date = today - timedelta(days=days_ago)
            vendor = self.vendors[po_count % len(self.vendors)]
            items = self.inventory[: 2 + (po_count % 3)]

            line_items = []
            total = Decimal("0")
            for item in items:
                qty = Decimal(str(50 + (po_count % 100)))
                amount = qty * item["purchase_cost"]
                total += amount
                line_items.append(
                    {
                        "item_id": item["qb_id"],
                        "item_name": item["name"],
                        "quantity": str(qty),
                        "unit_cost": str(item["purchase_cost"]),
                        "amount": str(amount),
                    }
                )

            po_count += 1
            is_received = po_count % 2 == 0  # Every other PO received

            self.purchase_orders.append(
                {
                    "qb_id": f"PO-{po_count:05d}",
                    "vendor_id": vendor["qb_id"],
                    "vendor_name": vendor["name"],
                    "po_date": po_date.isoformat(),
                    "expected_date": (po_date + timedelta(days=7)).isoformat(),
                    "line_items": line_items,
                    "total_amount": str(total),
                    "status": "received" if is_received else "open",
                    "memo": f"Bulk order from {vendor['name']}",
                }
            )

    def _generate_payments(self):
        """Create payment records."""
        paid_invoices = [inv for inv in self.invoices if inv["status"] == "paid"]

        for invoice in paid_invoices:
            self.payments.append(
                {
                    "qb_id": f"PAY-{invoice['qb_id']}",
                    "customer_id": invoice["customer_id"],
                    "customer_name": invoice["customer_name"],
                    "invoice_id": invoice["qb_id"],
                    "payment_date": (
                        date.fromisoformat(invoice["invoice_date"]) + timedelta(days=20)
                    ).isoformat(),
                    "payment_method": "check",
                    "amount": invoice["total_amount"],
                    "memo": f"Payment for {invoice['qb_id']}",
                }
            )

    def to_dict(self) -> dict:
        """Return all data as a dictionary."""
        return {
            "company": {
                "name": "Test Distributor Inc.",
                "description": "Realistic test distributor for SecretaryAI testing",
                "industry": "Food Distribution",
                "generated_date": date.today().isoformat(),
            },
            "customers": self.customers,
            "vendors": self.vendors,
            "inventory": self.inventory,
            "invoices": self.invoices,
            "purchase_orders": self.purchase_orders,
            "payments": self.payments,
        }

    def to_json_file(self, filename: str = "distributor_test_data.json"):
        """Save all data to JSON file."""
        with open(filename, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        log.info(f"✓ Saved test data to {filename}")

    def to_csv_files(self):
        """Export customers and inventory to CSV for QB import."""
        # Export customers
        customers_file = "distributor_customers.csv"
        with open(customers_file, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "name",
                    "email",
                    "phone",
                    "billing_address_street",
                    "billing_address_city",
                    "billing_address_state",
                    "billing_address_postal_code",
                    "credit_limit",
                    "payment_terms",
                    "notes",
                ],
            )
            writer.writeheader()
            for cust in self.customers:
                writer.writerow(
                    {
                        "name": cust["name"],
                        "email": cust.get("email", ""),
                        "phone": cust.get("phone", ""),
                        "billing_address_street": cust.get("billing_address", {}).get(
                            "street", ""
                        ),
                        "billing_address_city": cust.get("billing_address", {}).get(
                            "city", ""
                        ),
                        "billing_address_state": cust.get("billing_address", {}).get(
                            "state", ""
                        ),
                        "billing_address_postal_code": cust.get("billing_address", {}).get(
                            "postal_code", ""
                        ),
                        "credit_limit": cust.get("credit_limit", ""),
                        "payment_terms": cust.get("payment_terms", ""),
                        "notes": cust.get("notes", ""),
                    }
                )
        log.info(f"✓ Exported customers to {customers_file}")

        # Export inventory
        inventory_file = "distributor_inventory.csv"
        with open(inventory_file, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "name",
                    "sku",
                    "description",
                    "unit_of_measure",
                    "quantity_on_hand",
                    "reorder_point",
                    "purchase_cost",
                    "sales_price",
                    "preferred_vendor",
                ],
            )
            writer.writeheader()
            for item in self.inventory:
                writer.writerow(
                    {
                        "name": item["name"],
                        "sku": item["sku"],
                        "description": item.get("description", ""),
                        "unit_of_measure": item.get("unit_of_measure", ""),
                        "quantity_on_hand": item.get("quantity_on_hand", ""),
                        "reorder_point": item.get("reorder_point", ""),
                        "purchase_cost": item.get("purchase_cost", ""),
                        "sales_price": item.get("sales_price", ""),
                        "preferred_vendor": item.get("preferred_vendor", ""),
                    }
                )
        log.info(f"✓ Exported inventory to {inventory_file}")


class ConductorUploader:
    """Upload test data to QB via Conductor API."""

    def __init__(self, api_key: str, end_user_id: str):
        self._api_key = api_key
        self._end_user_id = end_user_id
        self._base_url = "https://api.conductor.is/v1"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Conductor-End-User-Id": end_user_id,
            "Content-Type": "application/json",
        }

    async def upload_all(self, data: dict) -> dict:
        """Upload all test data to QB via Conductor."""
        results = {
            "customers_created": 0,
            "vendors_created": 0,
            "inventory_created": 0,
            "invoices_created": 0,
            "purchase_orders_created": 0,
            "errors": [],
        }

        try:
            # Upload customers
            for customer in data.get("customers", []):
                try:
                    await self._create_customer(customer)
                    results["customers_created"] += 1
                    log.info(f"✓ Created customer: {customer['name']}")
                except Exception as e:
                    log.error(f"✗ Failed to create customer {customer['name']}: {e}")
                    results["errors"].append(f"Customer {customer['name']}: {str(e)}")

            # Upload vendors
            for vendor in data.get("vendors", []):
                try:
                    await self._create_vendor(vendor)
                    results["vendors_created"] += 1
                    log.info(f"✓ Created vendor: {vendor['name']}")
                except Exception as e:
                    log.error(f"✗ Failed to create vendor {vendor['name']}: {e}")
                    results["errors"].append(f"Vendor {vendor['name']}: {str(e)}")

            # Upload inventory items
            for item in data.get("inventory", []):
                try:
                    await self._create_inventory_item(item)
                    results["inventory_created"] += 1
                    log.info(f"✓ Created inventory: {item['name']}")
                except Exception as e:
                    log.error(f"✗ Failed to create inventory {item['name']}: {e}")
                    results["errors"].append(f"Inventory {item['name']}: {str(e)}")

            # Note: Creating invoices and POs via Conductor may require special handling
            # For now, we just count what we've done
            log.info(f"\nUpload Summary: {results}")
            return results

        except Exception as e:
            log.error(f"Upload failed: {e}")
            raise

    async def _create_customer(self, customer: dict):
        """Create a customer in QB via Conductor."""
        # Note: Conductor API customer creation endpoint may vary
        # This is a placeholder for the correct endpoint
        payload = {
            "name": customer["name"],
            "email": customer.get("email"),
            "phone": customer.get("phone"),
            "billingAddress": customer.get("billing_address", {}),
            "shippingAddress": customer.get("shipping_address", {}),
            "creditLimit": float(customer.get("credit_limit", 0)),
            "paymentTerms": customer.get("payment_terms"),
            "notes": customer.get("notes"),
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/quickbooks-desktop/customers",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def _create_vendor(self, vendor: dict):
        """Create a vendor in QB via Conductor."""
        payload = {
            "name": vendor["name"],
            "email": vendor.get("email"),
            "phone": vendor.get("phone"),
            "billingAddress": vendor.get("billing_address", {}),
            "paymentTerms": vendor.get("payment_terms"),
            "accountNumber": vendor.get("account_number"),
            "notes": vendor.get("notes"),
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/quickbooks-desktop/vendors",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def _create_inventory_item(self, item: dict):
        """Create an inventory item in QB via Conductor."""
        payload = {
            "name": item["name"],
            "sku": item.get("sku"),
            "description": item.get("description"),
            "unitOfMeasure": item.get("unit_of_measure", "each"),
            "quantityOnHand": float(item.get("quantity_on_hand", 0)),
            "reorderPoint": float(item.get("reorder_point", 0)),
            "purchaseCost": float(item.get("purchase_cost", 0)),
            "salesPrice": float(item.get("sales_price", 0)),
            "taxable": item.get("taxable", True),
            "preferredVendorId": item.get("vendor_id"),
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/quickbooks-desktop/inventory-items",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate test distributor data for QB")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload directly to QB via Conductor API",
    )
    parser.add_argument(
        "--conductor-key", type=str, help="Conductor API key (required for --upload)"
    )
    parser.add_argument(
        "--end-user-id", type=str, help="Conductor end-user ID (required for --upload)"
    )

    args = parser.parse_args()

    # Generate test data
    generator = DistributorTestDataGenerator()
    generator.generate_all()
    data = generator.to_dict()

    # Export to files
    generator.to_json_file()
    generator.to_csv_files()

    # Summary
    print("\n" + "=" * 60)
    print("TEST DISTRIBUTOR DATA GENERATED")
    print("=" * 60)
    print(f"Customers:       {len(data['customers'])}")
    print(f"Vendors:         {len(data['vendors'])}")
    print(f"Inventory Items: {len(data['inventory'])}")
    print(f"Invoices:        {len(data['invoices'])} (sales history)")
    print(f"PurchaseOrders:  {len(data['purchase_orders'])} (procurement history)")
    print(f"Payments:        {len(data['payments'])} (recorded payments)")
    print("\nExported Files:")
    print("  - distributor_test_data.json    (full dataset)")
    print("  - distributor_customers.csv     (for QB import)")
    print("  - distributor_inventory.csv     (for QB import)")
    print("=" * 60)

    # Upload to QB if requested
    if args.upload:
        if not args.conductor_key or not args.end_user_id:
            log.error(
                "Error: --conductor-key and --end-user-id required for --upload"
            )
            return

        log.info("Uploading to QB via Conductor API...")
        uploader = ConductorUploader(args.conductor_key, args.end_user_id)
        try:
            results = await uploader.upload_all(data)
            print("\nUpload Results:")
            for key, value in results.items():
                if key != "errors":
                    print(f"  {key}: {value}")
            if results["errors"]:
                print("\nErrors encountered:")
                for error in results["errors"]:
                    print(f"  - {error}")
        except Exception as e:
            log.error(f"Upload failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
