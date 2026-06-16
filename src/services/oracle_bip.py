from __future__ import annotations

import base64
import csv
import io
import logging
import os
import urllib.parse
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_oracle_url
from src.utils.date_formatter import format_bip_date

logger = logging.getLogger(__name__)

BIP_TIMEOUT = 60.0
CHUNK_DOWNLOAD_SIZE = -1
MAX_RETRIES = 3
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 10

class OracleBIPTransientError(Exception):
    pass

async def _run_bip_report(
    client: httpx.AsyncClient,
    user: str,
    pwd: str,
    candidate_paths: list[str],
    parameters: list[dict[str, Any]],
    report_type: str
) -> list[dict[str, Any]]:
    payload = {
        "byPassCache": True,
        "flattenXML": False,
        "attributeFormat": "csv",
        "sizeOfDataChunkDownload": CHUNK_DOWNLOAD_SIZE,
        "ReportRequest": {
            "parameterNameValues": {
                "listOfParamNameValues": parameters
            }
        }
    }

    last_error = None
    for report_path in candidate_paths:
        encoded_path = urllib.parse.quote(report_path, safe='')
        url = f"{get_oracle_url()}/xmlpserver/services/rest/v1/reports/{encoded_path}/run"

        try:
            response = await client.post(url, json=payload, auth=(user, pwd), timeout=BIP_TIMEOUT)
            response.raise_for_status()
            data = response.json()

            if "reportBytes" not in data:
                return []

            report_bytes = base64.b64decode(data["reportBytes"])
            csv_text = report_bytes.decode("utf-8", errors="replace")

            results = []
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                clean_row = {k.strip().upper().replace(" ", ""): (v or "").strip() for k, v in row.items() if k}
                if clean_row:
                    results.append(clean_row)
            return results

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                last_error = e
                continue
            if e.response.status_code in [429, 500, 502, 503, 504]:
                raise OracleBIPTransientError(f"Transient BIP error {e}") from e
            raise
        except Exception as e:
            logger.exception(f"Failed to execute BIP report for {report_type} match: {e}")
            raise

    if last_error:
        raise last_error
    return []

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, OracleBIPTransientError)),
    reraise=True
)
async def run_bip_invoice_match(client: httpx.AsyncClient, user: str, pwd: str, invoice_number: str | None, inv_date: str | None, amount: float | None, customer_name: str | None) -> list[dict[str, Any]]:
    user_path = os.getenv("ORACLE_BIP_USER_PATH", "~tripti.chugh@pinelabs.com/SHREYANSH/Get Invoice Details Report.xdo")
    candidate_paths = [
        "Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo",
        "Custom/Financials/Receivables/Upgrade/Get Invoice Details Report.xdo",
        "shared/Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo",
        user_path
    ]

    formatted_date = format_bip_date(inv_date or "")

    parameters = [
        {"name": "P_INVOICE_NUM", "values": [invoice_number or ""]},
        {"name": "P_INVOICE_DATE", "values": [formatted_date]},
        {"name": "P_INVOICE_AMOUNT", "values": [str(amount) if amount is not None else ""]},
        {"name": "P_CUSTOMER_NAME", "values": [customer_name or ""]}
    ]

    return await _run_bip_report(client, user, pwd, candidate_paths, parameters, "invoice")

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, OracleBIPTransientError)),
    reraise=True
)
async def run_bip_receipt_match(client: httpx.AsyncClient, user: str, pwd: str, receipt_number: str | None, receipt_date: str | None, amount: float | None, customer_name: str | None) -> list[dict[str, Any]]:
    user_path = os.getenv("ORACLE_BIP_USER_PATH", "~tripti.chugh@pinelabs.com/SHREYANSH/Get Receipt Details Report.xdo").replace("Get Invoice Details", "Get Receipt Details")
    if "Get Receipt Details" not in user_path:
        user_path = "~tripti.chugh@pinelabs.com/SHREYANSH/Get Receipt Details Report.xdo"

    candidate_paths = [
        "Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo",
        "Custom/Financials/Receivables/Upgrade/Get Receipt Details Report.xdo",
        "shared/Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo",
        user_path
    ]

    formatted_date = format_bip_date(receipt_date or "")

    parameters = [
        {"name": "P_RECEIPT_NUMBER", "values": [receipt_number or ""]},
        {"name": "P_RECEIPT_DATE", "values": [formatted_date]},
        {"name": "P_RECEIPT_AMOUNT", "values": [str(amount) if amount is not None else ""]},
        {"name": "P_CUSTOMER_NAME", "values": [customer_name or ""]}
    ]

    return await _run_bip_report(client, user, pwd, candidate_paths, parameters, "receipt")
