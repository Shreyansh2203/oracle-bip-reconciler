import asyncio
import os
import urllib.parse

import httpx
from dotenv import load_dotenv

load_dotenv()
ORACLE_URL = 'https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com'
user = os.getenv('ORACLE_USER')
pwd = os.getenv('ORACLE_PASS')

async def test():
    async with httpx.AsyncClient() as client:
        q = urllib.parse.quote("TransactionNumber='6650303378' or TransactionNumber='6650503306'")
        res = await client.get(f'{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/receivablesInvoices?q={q}', auth=(user, pwd))
        data = res.json().get('items', [])
        print(f"Found {len(data)} items")
        for d in data:
            print(d.get('TransactionNumber'))

asyncio.run(test())
