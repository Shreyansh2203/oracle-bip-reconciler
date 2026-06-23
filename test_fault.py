import asyncio
import os

import httpx


async def fetch_raw_bip(client, amount, date_str):
    url = "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com/xmlpserver/services/ExternalReportWSSService"
    xml_payload = f"""<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">
   <soap:Header/>
   <soap:Body>
      <pub:runReport>
         <pub:reportRequest>
            <pub:attributeFormat>csv</pub:attributeFormat>
            <pub:parameterNameValues>
               <pub:item>
                  <pub:name>P_CUSTOMER_NAME</pub:name>
                  <pub:values><pub:item> </pub:item></pub:values>
               </pub:item>
               <pub:item>
                  <pub:name>P_RECEIPT_NUMBER</pub:name>
                  <pub:values><pub:item> </pub:item></pub:values>
               </pub:item>
               <pub:item>
                  <pub:name>P_RECEIPT_AMOUNT</pub:name>
                  <pub:values><pub:item>{amount}</pub:item></pub:values>
               </pub:item>
               <pub:item>
                  <pub:name>P_RECEIPT_DATE</pub:name>
                  <pub:values><pub:item>{date_str}</pub:item></pub:values>
               </pub:item>
            </pub:parameterNameValues>
            <pub:reportAbsolutePath>/Custom/Shreyansh/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo</pub:reportAbsolutePath>
         </pub:reportRequest>
         <pub:userID>{os.getenv("ORACLE_USER")}</pub:userID>
         <pub:password>{os.getenv("ORACLE_PASS")}</pub:password>
      </pub:runReport>
   </soap:Body>
</soap:Envelope>"""

    res = await client.post(url, data=xml_payload, headers={"Content-Type": "application/soap+xml;charset=UTF-8"})
    print(f"HTTP Status: {res.status_code}")
    if res.status_code != 200:
        print(f"Error Body: {res.text}")


async def main():
    async with httpx.AsyncClient(timeout=60.0) as client:
        print("--- Testing Amount ONLY ---")
        await fetch_raw_bip(client, "1731.61", " ")
        print("\n--- Testing Date ONLY ---")
        await fetch_raw_bip(client, " ", "03-02-2026")
        print("\n--- Testing Both ---")
        await fetch_raw_bip(client, "1731.61", "03-02-2026")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(main())
