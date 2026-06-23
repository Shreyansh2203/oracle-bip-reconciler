import asyncio
import os

import httpx
from dotenv import load_dotenv

from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts

load_dotenv()


async def debug():
    u = os.getenv("ORACLE_USER")
    p = os.getenv("ORACLE_PASS")
    async with httpx.AsyncClient(timeout=60.0) as client:
        print("Fetching receipts for 000000324185...")
        receipts = await fetch_bip_receipts(client, u, p, receipt_number="000000324185")
        for r in receipts:
            print(f"RAW RECEIPT: {r}")

        print("Fetching invoice 5000619625A2507...")
        invoices = await fetch_bip_invoices(client, u, p, invoice_number="5000619625A2507")
        for i in invoices:
            print(f"RAW INVOICE: {i}")


if __name__ == "__main__":
    asyncio.run(debug())
