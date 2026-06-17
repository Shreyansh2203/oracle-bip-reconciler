import asyncio, httpx, os, json
from src.services.oracle_bip import fetch_bip_receipts
from dotenv import load_dotenv
load_dotenv()

async def main():
    user = os.getenv('ORACLE_USER')
    pw = os.getenv('ORACLE_PASS')
    
    with open('data/JSON/new 2.json', 'r') as fp:
        data = json.load(fp)
        
    print(f"Customer Name: {data.get('customer_name')}")
    print(f"Payment Date: {data.get('payment_date')}")
    print(f"Total Amount: {data.get('total_amount')}")
    print(f"Payment Reference: {data.get('payment_reference')}")
    
    print("\nFetching using customer name...")
    res = await fetch_bip_receipts(
        httpx.AsyncClient(timeout=60.0), 
        user, pw, 
        customer_name=data.get('customer_name')
    )
    print(f"Returned {len(res)} receipts.")
    for r in res:
        print(f" - {r.get('RECEIPT_NUMBER')} | Status: {r.get('RECEIPT_STATUS_CODE')} | Date: {r.get('RECEIPT_DATE')} | Amount: {r.get('RECEIPT_AMOUNT')}")

asyncio.run(main())
