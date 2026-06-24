from __future__ import annotations

import asyncio
import base64
import csv
import io
import logging
import os
import time
from collections import OrderedDict
from typing import Any

import defusedxml.ElementTree as ET
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_oracle_url
from src.constants import (
    BIP_CACHE_TTL_SECONDS,
    BIP_CHUNK_DOWNLOAD_SIZE,
    BIP_MAX_RETRIES,
    BIP_MAX_WAIT_SECONDS,
    BIP_MIN_WAIT_SECONDS,
    BIP_TIMEOUT,
)

logger = logging.getLogger(__name__)

BIP_MAX_CACHE_ENTRIES = 1000
_bip_cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_bip_locks: dict[str, asyncio.Lock] = {}


def _get_cache_key(report_type: str, parameters: list[dict[str, Any]]) -> str:
    sorted_params = sorted(parameters, key=lambda x: x["name"])
    param_str = "|".join([f"{p['name']}={p['values'][0]}" for p in sorted_params])
    return f"{report_type}::{param_str}"


def _cleanup_bip_cache() -> None:
    now = time.time()
    expired_keys = [k for k, (ts, _) in _bip_cache.items() if now - ts >= BIP_CACHE_TTL_SECONDS]
    for k in expired_keys:
        _bip_cache.pop(k, None)

    # Enforce maximum cache size by removing oldest elements (LRU-ish)
    while len(_bip_cache) > BIP_MAX_CACHE_ENTRIES:
        _bip_cache.popitem(last=False)

    # Safely clean up locks to prevent infinite memory leak
    keys_to_remove = []
    for k, lock in list(_bip_locks.items()):
        if not lock.locked() and k not in _bip_cache:
            keys_to_remove.append(k)
    for k in keys_to_remove:
        _bip_locks.pop(k, None)


def _parse_soap_response_sync(response_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(response_text)
    report_bytes_elem = next(root.iter("{http://xmlns.oracle.com/oxp/service/PublicReportService}reportBytes"), None)
    if report_bytes_elem is None or not report_bytes_elem.text:
        return []
    report_bytes = base64.b64decode(report_bytes_elem.text)
    csv_text = report_bytes.decode("utf-8", errors="replace")

    results = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        clean_row = {key.strip().upper().replace(" ", ""): (value or "").strip() for key, value in row.items() if key}
        if clean_row:
            results.append(clean_row)
    return results


class OracleBIPTransientError(Exception):
    pass


async def _run_bip_report(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    candidate_paths: list[str],
    parameters: list[dict[str, Any]],
    report_type: str,
) -> list[dict[str, Any]]:
    # We previously filtered out empty parameters, but since we updated the Oracle SQL
    # to use TRIM() around parameters, passing empty tags (which Oracle translates to ' ')
    # is now safely handled. Omitting parameters causes Oracle to use Data Model default values,
    # which may crash the query if they are invalid strings like 'null'.
    valid_parameters = parameters

    # Build parameter XML
    from xml.sax.saxutils import escape

    param_xml = ""
    for param in valid_parameters:
        safe_val = escape(str(param["values"][0]), {'"': "&quot;", "'": "&apos;"})
        param_xml += f"""
               <pub:item>
                  <pub:name>{param["name"]}</pub:name>
                  <pub:values>
                     <pub:item>{safe_val}</pub:item>
                  </pub:values>
               </pub:item>"""

    cache_key = _get_cache_key(report_type, valid_parameters)

    _cleanup_bip_cache()

    if cache_key not in _bip_locks:
        _bip_locks[cache_key] = asyncio.Lock()

    async with _bip_locks[cache_key]:
        if cache_key in _bip_cache:
            timestamp, cached_data = _bip_cache[cache_key]
            # Move to end to mark as recently used (LRU)
            _bip_cache.move_to_end(cache_key)
            if time.time() - timestamp < BIP_CACHE_TTL_SECONDS:
                return cached_data

        last_error = None
        valid_paths = [p for p in candidate_paths if p and p.strip()]
        base_url = get_oracle_url().rstrip("/")
        soap_url = f"{base_url}/xmlpserver/services/ExternalReportWSSService"

        headers = {"Content-Type": 'application/soap+xml;charset=UTF-8;action=""', "User-Agent": "httpx"}

        safe_username = escape(username, {'"': "&quot;", "'": "&apos;"})
        safe_password = escape(password, {'"': "&quot;", "'": "&apos;"})

        for report_path in valid_paths:
            safe_path = escape(report_path.strip(), {'"': "&quot;", "'": "&apos;"})
            param_block = f"<pub:parameterNameValues>{param_xml}</pub:parameterNameValues>" if param_xml else ""
            xml_payload = f"""<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">
   <soap:Header/>
   <soap:Body>
      <pub:runReport>
         <pub:reportRequest>
            <pub:attributeFormat>csv</pub:attributeFormat>
            {param_block}
            <pub:reportAbsolutePath>{safe_path}</pub:reportAbsolutePath>
            <pub:sizeOfDataChunkDownload>{BIP_CHUNK_DOWNLOAD_SIZE}</pub:sizeOfDataChunkDownload>
         </pub:reportRequest>
         <pub:userID>{safe_username}</pub:userID>
         <pub:password>{safe_password}</pub:password>
      </pub:runReport>
   </soap:Body>
</soap:Envelope>"""

            try:
                response = await client.post(
                    soap_url, content=xml_payload, headers=headers, auth=(username, password), timeout=BIP_TIMEOUT
                )
                response.raise_for_status()

                try:
                    results = await asyncio.to_thread(_parse_soap_response_sync, response.text)
                    _bip_cache[cache_key] = (time.time(), results)
                    return results
                except Exception as parse_error:
                    logger.error(f"Failed to parse SOAP XML: {parse_error}")
                    raise OracleBIPTransientError(f"Failed to parse SOAP response: {parse_error}") from parse_error

            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404 or "Report definition not found" in error.response.text:
                    last_error = error
                    continue
                if error.response.status_code in [429, 500, 502, 503, 504]:
                    if (
                        "Report definition not found" in error.response.text
                        or "not found" in error.response.text.lower()
                    ):
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
    reraise=True,
)
async def fetch_bip_invoices(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    customer_name: str | None = None,
    invoice_number: str | None = None,
    invoice_amount: str | None = None,
    invoice_date: str | None = None,
) -> list[dict[str, Any]]:
    candidate_paths = [
        os.getenv("ORACLE_BIP_INVOICE_PATH", ""),
        "/Custom/Shreyansh/Financials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo",
    ]

    from src.utils.date_formatter import format_oracle_date
    fmt_date = format_oracle_date(invoice_date) if invoice_date else ""

    # ALWAYS pass all 4 parameters to prevent BI Publisher "Missing Parameter" faults
    parameters = [
        {"name": "P_CUSTOMER_NAME", "values": [customer_name if customer_name else " "]},
        {"name": "P_INVOICE_NUM", "values": [invoice_number if invoice_number else " "]},
        {
            "name": "P_INVOICE_AMOUNT",
            "values": [str(invoice_amount) if invoice_amount is not None and str(invoice_amount).strip() else " "],
        },
        {"name": "P_INVOICE_DATE", "values": [fmt_date if fmt_date else " "]},
    ]

    return await _run_bip_report(client, username, password, candidate_paths, parameters, "invoice")


@retry(
    stop=stop_after_attempt(BIP_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=BIP_MIN_WAIT_SECONDS, max=BIP_MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, OracleBIPTransientError)),
    reraise=True,
)
async def fetch_bip_receipts(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    receipt_number: str | None = None,
    customer_name: str | None = None,
    receipt_date: str | None = None,
    receipt_amount: str | float | None = None,
) -> list[dict[str, Any]]:
    candidate_paths = [
        os.getenv("ORACLE_BIP_RECEIPT_PATH", ""),
        "/Custom/Shreyansh/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo",
    ]

    # Use standard Oracle format (YYYY-MM-DD) instead of BIP format (MM-DD-YYYY) to prevent ORA-01861 500 errors
    from src.utils.date_formatter import format_oracle_date
    fmt_date = format_oracle_date(receipt_date) if receipt_date else ""

    parameters = [
        {"name": "P_CUSTOMER_NAME", "values": [customer_name if customer_name else " "]},
        {"name": "P_RECEIPT_NUMBER", "values": [receipt_number if receipt_number else " "]},
        {"name": "P_RECEIPT_AMOUNT", "values": [str(receipt_amount) if receipt_amount is not None and str(receipt_amount).strip() else " "]},
        {"name": "P_RECEIPT_DATE", "values": [fmt_date if fmt_date else " "]},
    ]

    return await _run_bip_report(client, username, password, candidate_paths, parameters, "receipt")
