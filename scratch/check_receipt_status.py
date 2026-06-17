import asyncio, os, httpx, json
from dotenv import load_dotenv
load_dotenv()
from src.services.oracle_bip import fetch_bip_receipts

async def main():
    client = httpx.AsyncClient(timeout=60.0)
    user = os.getenv('ORACLE_USER')
    password = os.getenv('ORACLE_PASS')
    
    receipts = await fetch_bip_receipts(client, user, password, customer_name='New Horizon Foods')
    for r in receipts:
        print(f"{r.get('RECEIPT_NUMBER')} - Status: {r.get('RECEIPT_STATUS_CODE')}")
        
asyncio.run(main())
