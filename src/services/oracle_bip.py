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

from src.constants import (
    BIP_CHUNK_DOWNLOAD_SIZE,
    BIP_MAX_RETRIES,
    BIP_MAX_WAIT_SECONDS,
    BIP_MIN_WAIT_SECONDS,
    BIP_TIMEOUT,
)
from src.config import get_oracle_url
from src.utils.date_formatter import format_bip_date

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
    payload = {
        "byPassCache": True,
        "flattenXML": False,
        "attributeFormat": "csv",
        "sizeOfDataChunkDownload": BIP_CHUNK_DOWNLOAD_SIZE,
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
            response = await client.post(url, json=payload, auth=(username, password), timeout=BIP_TIMEOUT)
            response.raise_for_status()
            data = response.json()

            if "reportBytes" not in data:
                return []

            report_bytes = base64.b64decode(data["reportBytes"])
            csv_text = report_bytes.decode("utf-8", errors="replace")

            results = []
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                clean_row = {key.strip().upper().replace(" ", ""): (value or "").strip() for key, value in row.items() if key}
                if clean_row:
                    results.append(clean_row)
            return results

        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                last_error = error
                continue
            if error.response.status_code in [429, 500, 502, 503, 504]:
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
async def run_bip_invoice_match(client: httpx.AsyncClient, username: str, password: str, invoice_number: str | None, invoice_date: str | None, amount: float | None, customer_name: str | None) -> list[dict[str, Any]]:
    # Paths retrieved from Oracle Catalog UI
    candidate_paths = [
        os.getenv("ORACLE_BIP_INVOICE_PATH", ""),
        "Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo",
        "~tripti.chugh@pinelabs.com/SHREYANSH/Get Invoice Details Report.xdo",
        "Custom/Financials/Receivables/Upgrade/Get Invoice Details Report.xdo",
        "shared/Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo"
    ]

    formatted_date = format_bip_date(invoice_date or "")

    parameters = [
        {"name": "P_INVOICE_NUM", "values": [invoice_number or ""]},
        {"name": "P_INVOICE_DATE", "values": [formatted_date]},
        {"name": "P_INVOICE_AMOUNT", "values": [str(amount) if amount is not None else ""]},
        {"name": "P_CUSTOMER_NAME", "values": [customer_name or ""]}
    ]

    return await _run_bip_report(client, username, password, candidate_paths, parameters, "invoice")

@retry(
    stop=stop_after_attempt(BIP_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=BIP_MIN_WAIT_SECONDS, max=BIP_MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, OracleBIPTransientError)),
    reraise=True
)
async def run_bip_receipt_match(client: httpx.AsyncClient, username: str, password: str, receipt_number: str | None, receipt_date: str | None, amount: float | None, customer_name: str | None) -> list[dict[str, Any]]:
    # Paths retrieved from Oracle Catalog UI
    candidate_paths = [
        os.getenv("ORACLE_BIP_RECEIPT_PATH", ""),
        "Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo",
        "~tripti.chugh@pinelabs.com/SHREYANSH/Get Receipt Details Report.xdo",
        "Custom/Financials/Receivables/Upgrade/Get Receipt Details Report.xdo",
        "shared/Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo"
    ]

    formatted_date = format_bip_date(receipt_date or "")

    parameters = [
        {"name": "P_RECEIPT_NUMBER", "values": [receipt_number or ""]},
        {"name": "P_RECEIPT_DATE", "values": [formatted_date]},
        {"name": "P_RECEIPT_AMOUNT", "values": [str(amount) if amount is not None else ""]},
        {"name": "P_CUSTOMER_NAME", "values": [customer_name or ""]}
    ]

    return await _run_bip_report(client, username, password, candidate_paths, parameters, "receipt")
