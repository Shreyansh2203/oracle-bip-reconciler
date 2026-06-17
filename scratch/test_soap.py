import asyncio, os, httpx, xml.etree.ElementTree as ET
from dotenv import load_dotenv
load_dotenv()

async def main():
    client = httpx.AsyncClient(timeout=60.0)
    user = os.getenv('ORACLE_USER')
    password = os.getenv('ORACLE_PASS')
    url = os.getenv('ORACLE_URL') + '/xmlpserver/services/ExternalReportWSSService'
    
    soap_body = f"""
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">
       <soapenv:Header/>
       <soapenv:Body>
          <pub:runReport>
             <pub:reportRequest>
                <pub:attributeFormat>csv</pub:attributeFormat>
                <pub:parameterNameValues>
                   <pub:item>
                      <pub:name>P_INVOICE_NUM</pub:name>
                      <pub:values><pub:item>1301044</pub:item></pub:values>
                   </pub:item>
                </pub:parameterNameValues>
                <pub:reportAbsolutePath>/Custom/Shreyansh/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo</pub:reportAbsolutePath>
                <pub:sizeOfDataChunkDownload>-1</pub:sizeOfDataChunkDownload>
             </pub:reportRequest>
             <pub:userID>{user}</pub:userID>
             <pub:password>{password}</pub:password>
          </pub:runReport>
       </soapenv:Body>
    </soapenv:Envelope>
    """
    
    headers = {'Content-Type': 'text/xml;charset=UTF-8'}
    resp = await client.post(url, data=soap_body.encode('utf-8'), headers=headers)
    
    print(resp.text)
        
asyncio.run(main())
