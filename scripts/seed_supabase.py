#!/usr/bin/env python3
"""
Seed the Supabase database with generated test distributor data.

Usage:
    python scripts/seed_supabase.py

Requirements:
    pip install supabase python-dotenv
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def main():
    # Load environment variables
    env_path = Path(__file__).parent.parent / "backend" / ".env"
    load_dotenv(env_path)

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in backend/.env")
        return

    log.info("Connecting to Supabase...")
    supabase: Client = create_client(supabase_url, supabase_key)

    # Load test data
    data_file = Path(__file__).parent.parent / "distributor_test_data.json"
    if not data_file.exists():
        log.error(f"Test data file not found: {data_file}")
        log.info("Run `python scripts/generate_test_distributor.py` first.")
        return

    with open(data_file, "r") as f:
        test_data = json.load(f)

    # 1. Ensure a test company exists
    company_name = test_data.get("company", {}).get("name", "Test Distributor Inc.")
    
    response = supabase.table("companies").select("*").eq("name", company_name).execute()
    if not response.data:
        log.info(f"Creating test company: {company_name}")
        response = supabase.table("companies").insert({"name": company_name}).execute()
    
    company_id = response.data[0]["id"]
    log.info(f"Company ID: {company_id}")

    # 2. Seed Accounts (Customers)
    log.info("Seeding Accounts (Customers)...")
    accounts_map = {} # Maps qb_id -> Supabase UUID
    for cust in test_data.get("customers", []):
        account_data = {
            "company_id": company_id,
            "qb_customer_id": cust["qb_id"],
            "name": cust["name"],
            "email": cust.get("email"),
            "phone": cust.get("phone"),
            "state": cust.get("billing_address", {}).get("state"),
        }
        
        # Upsert by company_id and qb_customer_id
        res = supabase.table("accounts").upsert(account_data, on_conflict="company_id,qb_customer_id").execute()
        if res.data:
            accounts_map[cust["qb_id"]] = res.data[0]["id"]

    # 3. Seed Inventory
    log.info("Seeding Inventory...")
    for item in test_data.get("inventory", []):
        inventory_data = {
            "company_id": company_id,
            "qb_item_id": item["qb_id"],
            "product_name": item["name"],
            "sku": item.get("sku"),
            "warehouse_1_qty": int(float(item.get("quantity_on_hand", 0))),
        }
        supabase.table("inventory").upsert(inventory_data, on_conflict="company_id,qb_item_id").execute()

    # 4. Seed Orders (Invoices)
    log.info("Seeding Orders (Invoices)...")
    for inv in test_data.get("invoices", []):
        # Look up the Supabase account_id using the QB customer ID
        supa_account_id = accounts_map.get(inv["customer_id"])
        
        order_data = {
            "company_id": company_id,
            "account_id": supa_account_id,
            "qb_invoice_id": inv["qb_id"],
            "order_date": inv["invoice_date"].split("T")[0],
            "due_date": inv["due_date"].split("T")[0],
            "total_amount": float(inv["total_amount"]),
            "balance": float(inv["balance_remaining"]),
            "status": inv["status"],
            "items": inv.get("line_items", [])
        }
        supabase.table("orders").upsert(order_data, on_conflict="company_id,qb_invoice_id").execute()

    log.info("✅ Database seeding complete!")


if __name__ == "__main__":
    main()
