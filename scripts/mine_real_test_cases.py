import asyncio
import json
import os
import random
import uuid
from typing import Any

from dotenv import load_dotenv
import httpx

from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts

# Load environment variables (ORACLE_USER, ORACLE_PASS, etc.)
load_dotenv()

CUSTOMERS_FILE = "Customers.txt"
OUTPUT_DIR = "Real Test Cases"

# Number of customers to sample for mining
SAMPLE_SIZE = 50
CONCURRENCY_LIMIT = 5

async def mine_customer_data(client: httpx.AsyncClient, username: str, password: str, customer_name: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            # Fetch receipts for this customer
            receipts = await fetch_bip_receipts(client, username, password, customer_name=customer_name)
            if not receipts:
                return

            # Fetch invoices for this customer
            invoices_raw = await fetch_bip_invoices(client, username, password, customer_name=customer_name)
            if not invoices_raw:
                return

            # Construct payload
            # Pick the first receipt as the base
            receipt = receipts[0]
            r_num = receipt.get("RECEIPT_NUMBER", "")
            r_date = receipt.get("RECEIPT_DATE", "")
            # Convert amount if possible
            r_amt_str = receipt.get("AMOUNT", "0")
            try:
                r_amt = float(r_amt_str)
            except ValueError:
                r_amt = 0.0

            # Construct invoice lines
            payload_invoices = []
            for i, inv in enumerate(invoices_raw[:10]): # Take up to 10 invoices
                i_num = inv.get("TRX_NUMBER", "")
                i_date = inv.get("TRX_DATE", "")
                i_amt_str = inv.get("AMOUNT_DUE_ORIGINAL", "0")
                try:
                    i_amt = float(i_amt_str)
                except ValueError:
                    i_amt = 0.0
                    
                payload_invoices.append({
                    "Line_ID": random.randint(1000000000, 9999999999) + i,
                    "invoice_number": i_num,
                    "invoice_date": i_date,
                    "invoice_amount": i_amt,
                    "customer_invoice_number": "",
                    "storeNo": ""
                })

            payload = {
                "customer_name": customer_name,
                "payment_reference": r_num,
                "payment_date": r_date,
                "header_id": random.randint(1000000000, 9999999999),
                "invoices": payload_invoices,
                "total_amount": r_amt,
                "confidence_score": round(random.uniform(70.0, 99.9), 2)
            }

            safe_name = "".join([c if c.isalnum() else "_" for c in customer_name])[:30]
            filename = f"real_{safe_name}.json"
            path = os.path.join(OUTPUT_DIR, filename)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            print(f"Successfully mined real data for '{customer_name}' -> {filename}")

        except Exception as e:
            print(f"Failed mining '{customer_name}': {e}")


async def main():
    username = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASS")
    if not username or not password:
        print("Missing Oracle credentials.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    with open(CUSTOMERS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[1:]
        all_customers = list(set([line.strip() for line in lines if line.strip()]))

    # Pick a random subset to query so we don't DDOS Oracle with 30k requests
    sample_customers = random.sample(all_customers, min(SAMPLE_SIZE, len(all_customers)))
    print(f"Starting miner for {len(sample_customers)} random customers...")

    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = [mine_customer_data(client, username, password, cust, sem) for cust in sample_customers]
        await asyncio.gather(*tasks)
        
    print(f"Mining complete. Payloads generated in {OUTPUT_DIR}/")

if __name__ == "__main__":
    asyncio.run(main())
