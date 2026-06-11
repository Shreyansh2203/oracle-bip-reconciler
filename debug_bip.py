import os, httpx, asyncio
import base64
from dotenv import load_dotenv

load_dotenv()
ORACLE_URL = 'https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com'
user = os.getenv('ORACLE_USER')
pwd = os.getenv('ORACLE_PASS')

async def run_bip_report():
    async with httpx.AsyncClient() as client:
        # Construct the URL for running a BI Publisher report
        # The endpoint is usually /xmlpserver/services/rest/v1/reports/<path to report>/run
        report_path = "Custom/Finacials/Receivable Transactions/Invoice Details Report.xdo"
        url = f"{ORACLE_URL}/xmlpserver/services/rest/v1/reports/{report_path.replace('/', '%2F')}/run"
        
        payload = {
            "byPassCache": True,
            "flattenXML": False,
            "attributeFormat": "csv",
            "sizeOfDataChunkDownload": -1
        }
        
        print(f"Requesting: {url}")
        res = await client.post(url, json=payload, auth=(user, pwd))
        print("Status:", res.status_code)
        if res.status_code == 200:
            data = res.json()
            if 'reportBytes' in data:
                report_bytes = base64.b64decode(data['reportBytes'])
                print(report_bytes[:500].decode('utf-8', errors='replace'))
            else:
                print(data)
        else:
            print(res.text)

asyncio.run(run_bip_report())
