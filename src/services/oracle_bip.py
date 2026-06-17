from __future__ import annotations

import base64
import csv
import io
import logging
import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_oracle_url
from src.constants import (
    BIP_CHUNK_DOWNLOAD_SIZE,
    BIP_MAX_RETRIES,
    BIP_MAX_WAIT_SECONDS,
    BIP_MIN_WAIT_SECONDS,
    BIP_TIMEOUT,
)

logger = logging.getLogger(__name__)

class OracleBIPTransientError(Exception):
    pass

async def _run_bip_report(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    candidate_paths: list[str],
    parameters: list[dict[str, Any]],
    report_type: str
) -> list[dict[str, Any]]:
    # Filter out empty parameters to prevent Oracle BIP from treating them as empty strings
    # which causes ORA-01858 errors in TO_DATE functions.
    valid_parameters = [p for p in parameters if p["values"] and p["values"][0]]

    # Build parameter XML
    param_xml = ""
    for param in valid_parameters:
        param_xml += f"""
               <pub:item>
                  <pub:name>{param['name']}</pub:name>
                  <pub:values>
                     <pub:item>{param['values'][0]}</pub:item>
                  </pub:values>
               </pub:item>"""

    last_error = None
    valid_paths = [p for p in candidate_paths if p and p.strip()]
    base_url = get_oracle_url().rstrip('/')
    soap_url = f"{base_url}/xmlpserver/services/ExternalReportWSSService"

    headers = {
        "Content-Type": "application/soap+xml;charset=UTF-8;action=\"\"",
        "User-Agent": "httpx"
    }

    for report_path in valid_paths:
        xml_payload = f"""<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">
   <soap:Header/>
   <soap:Body>
      <pub:runReport>
         <pub:reportRequest>
            <pub:attributeFormat>csv</pub:attributeFormat>
            <pub:parameterNameValues>{param_xml}</pub:parameterNameValues>
            <pub:reportAbsolutePath>{report_path.strip()}</pub:reportAbsolutePath>
            <pub:sizeOfDataChunkDownload>{BIP_CHUNK_DOWNLOAD_SIZE}</pub:sizeOfDataChunkDownload>
         </pub:reportRequest>
         <pub:userID>{username}</pub:userID>
         <pub:password>{password}</pub:password>
      </pub:runReport>
   </soap:Body>
</soap:Envelope>"""

        try:
            response = await client.post(soap_url, content=xml_payload, headers=headers, auth=(username, password), timeout=BIP_TIMEOUT)
            response.raise_for_status()

            # Parse SOAP response
            try:
                root = ET.fromstring(response.text)
                # Find reportBytes (namespace agnostic)
                report_bytes_elem = next(root.iter("{http://xmlns.oracle.com/oxp/service/PublicReportService}reportBytes"), None)
                if report_bytes_elem is None or not report_bytes_elem.text:
                    continue
                report_bytes = base64.b64decode(report_bytes_elem.text)
                csv_text = report_bytes.decode("utf-8", errors="replace")
            except Exception as parse_error:
                logger.error(f"Failed to parse SOAP XML: {parse_error}")
                continue

            results = []
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                clean_row = {key.strip().upper().replace(" ", ""): (value or "").strip() for key, value in row.items() if key}
                if clean_row:
                    results.append(clean_row)
            return results

        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404 or "Report definition not found" in error.response.text:
                last_error = error
                continue
            if error.response.status_code in [429, 500, 502, 503, 504]:
                if "Report definition not found" in error.response.text or "not found" in error.response.text.lower():
                    last_error = error
                    continue
                raise OracleBIPTransientError(f"Transient BIP error {error}") from error
            raise
        except Exception as error:
            logger.exception(f"Failed to execute BIP report for {report_type} match: {error}")
            raise

    if last_error:
        raise last_error
    return []

@retry(
    stop=stop_after_attempt(BIP_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=BIP_MIN_WAIT_SECONDS, max=BIP_MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, OracleBIPTransientError)),
    reraise=True
)
async def fetch_bip_invoices(client: httpx.AsyncClient, username: str, password: str, invoice_number: str | None = None, customer_name: str | None = None) -> list[dict[str, Any]]:
    candidate_paths = [
        os.getenv("ORACLE_BIP_INVOICE_PATH", ""),
        "/Custom/Shreyansh/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo"
    ]

    parameters = [
        {"name": "P_INVOICE_NUM", "values": [invoice_number or ""]},
        {"name": "P_INVOICE_DATE", "values": [""]},
        {"name": "P_INVOICE_AMOUNT", "values": [""]},
        {"name": "P_CUSTOMER_NAME", "values": [customer_name or ""]}
    ]

    return await _run_bip_report(client, username, password, candidate_paths, parameters, "invoice")

@retry(
    stop=stop_after_attempt(BIP_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=BIP_MIN_WAIT_SECONDS, max=BIP_MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, OracleBIPTransientError)),
    reraise=True
)
async def fetch_bip_receipts(client: httpx.AsyncClient, username: str, password: str, receipt_number: str | None = None, customer_name: str | None = None) -> list[dict[str, Any]]:
    candidate_paths = [
        os.getenv("ORACLE_BIP_RECEIPT_PATH", ""),
        "/Custom/Shreyansh/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo"
    ]

    parameters = [
        {"name": "P_RECEIPT_NUMBER", "values": [receipt_number or ""]},
        {"name": "P_RECEIPT_DATE", "values": [""]},
        {"name": "P_RECEIPT_AMOUNT", "values": [""]},
        {"name": "P_CUSTOMER_NAME", "values": [customer_name or ""]}
    ]

    return await _run_bip_report(client, username, password, candidate_paths, parameters, "receipt")
