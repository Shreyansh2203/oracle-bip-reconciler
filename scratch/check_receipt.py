import asyncio
import json
import os
import sys

sys.path.insert(0, r"c:\urban-octo-tribble")
from dotenv import load_dotenv
load_dotenv(r"c:\urban-octo-tribble\.env")

from src.services.oracle_bip import fetch_bip_receipts
import httpx

async def main():
    user = os.getenv("ORACLE_USER", "")
    pwd = os.getenv("ORACLE_PASS", "")

    async with httpx.AsyncClient(timeout=None) as client:
        receipts_raw = await fetch_bip_receipts(client, user, pwd, customer_name="New Horizon Foods")

    print(f"Oracle has {len(receipts_raw)} receipts for 'New Horizon Foods'\n")

    for r in receipts_raw:
        amount = r.get("RECEIPT_AMOUNT")
        if str(amount) == "2300" or str(amount) == "2300.0":
            print("Found match!")
            print(json.dumps(r, indent=2))

asyncio.run(main())
