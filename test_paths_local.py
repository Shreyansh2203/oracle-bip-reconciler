import asyncio
import httpx
import os
from dotenv import load_dotenv

async def test_bip_path(client, base_url, user, pwd, raw_path, is_strict_encode=False):
    if is_strict_encode:
        import urllib.parse
        encoded_path = urllib.parse.quote(raw_path, safe='')
    else:
        encoded_path = raw_path.replace('/', '%2F').replace(' ', '%20')
        
    url = f"{base_url}/xmlpserver/services/rest/v1/reports/{encoded_path}/run"
    
    payload = {
        "byPassCache": True,
        "flattenXML": False,
        "attributeFormat": "csv",
        "sizeOfDataChunkDownload": 10,
        "ReportRequest": {
            "parameterNameValues": {
                "listOfParamNameValues": [
                    {"name": "P_DUMMY", "values": [""]}
                ]
            }
        }
    }
    
    try:
        resp = await client.post(url, json=payload, auth=(user, pwd), timeout=10)
        return resp.status_code, url
    except Exception as e:
        return str(e), url

async def main():
    load_dotenv('.env')
    base = os.getenv('ORACLE_URL')
    user = os.getenv('ORACLE_USER')
    pwd = os.getenv('ORACLE_PWD')
    
    if not user or not pwd:
        print('Missing ORACLE_USER or ORACLE_PWD in .env')
        return

    paths_to_test = [
        # 1. The literal path from your screenshot (with typos and spaces)
        ('Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo', False),
        
        # 2. The literal path but using strict urllib.parse encoding
        ('Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo', True),
        
        # 3. The OLD path that used to return 200 OK before the folders were modified
        ('Custom/Financials/Receivables/Upgrade/Get Invoice Details Report.xdo', False),
        
        # 4. Without the .xdo extension
        ('Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report', False),
        
        # 5. With shared prefix
        ('shared/Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo', False),
        
        # 6. With ~ personal folder prefix
        ('~tripti.chugh@pinelabs.com/SHREYANSH/Get Invoice Details Report.xdo', False)
    ]
    
    print(f'Testing against: {base}')
    print(f'User: {user}')
    print('-' * 70)
    
    async with httpx.AsyncClient() as client:
        for p, is_strict in paths_to_test:
            status, url = await test_bip_path(client, base, user, pwd, p, is_strict)
                
            if status == 200:
                print(f'[SUCCESS 200] {p} (Strict: {is_strict})')
                print(f'   -> Path is PERFECT!')
                print('-' * 70)
            elif status == 401:
                print(f'[UNAUTHORIZED 401] {p}')
            elif status == 404:
                print(f'[FAILED 404] {p} (Strict: {is_strict})')
            else:
                print(f'[ERROR {status}] {p}')

if __name__ == '__main__':
    asyncio.run(main())
