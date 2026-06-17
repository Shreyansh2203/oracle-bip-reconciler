import asyncio, httpx, os
from src.services.oracle_bip import fetch_bip_receipts
from dotenv import load_dotenv
load_dotenv()

async def main():
    user = os.getenv('ORACLE_USER')
    pw = os.getenv('ORACLE_PASS')
    print("Fetching all receipts (base case)...")
    res = await fetch_bip_receipts(httpx.AsyncClient(timeout=60.0), user, pw)
    print(f"Total: {len(res)}")
    for r in res:
        rn = str(r.get('RECEIPT_NUMBER', ''))
        if '003' in rn:
            print(f"Found: {rn} - Status: {r.get('RECEIPT_STATUS_CODE')}")
            
asyncio.run(main())
