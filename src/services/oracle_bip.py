import base64
import csv
import io
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ORACLE_URL = os.getenv("ORACLE_URL", "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com")
BIP_TIMEOUT = 60.0
CHUNK_DOWNLOAD_SIZE = -1

async def run_bip_bulk_match(client: httpx.AsyncClient, user: str, pwd: str, invoice_numbers: list[str]) -> dict[str, Any]:
    """
    Fetches all invoice details in a single bulk request via Oracle BI Publisher.
    Returns a dictionary mapping TransactionNumber -> Invoice details.
    """
    report_path = "Custom/Finacials/Receivable Transactions/Invoice Details Report.xdo"
    url = f"{ORACLE_URL}/xmlpserver/services/rest/v1/reports/{report_path.replace('/', '%2F')}/run"

    invoices_str = ",".join(invoice_numbers)

    payload = {
        "byPassCache": True,
        "flattenXML": False,
        "attributeFormat": "csv",
        "sizeOfDataChunkDownload": CHUNK_DOWNLOAD_SIZE,
        "ReportRequest": {
            "parameterNameValues": {
                "listOfParamNameValues": [
                    {
                        "name": "P_INVOICE_LIST",
                        "values": [invoices_str]
                    }
                ]
            }
        }
    }

    logger.info(f"Triggering bulk BIP fetch for {len(invoice_numbers)} invoices...")

    try:
        response = await client.post(url, json=payload, auth=(user, pwd), timeout=BIP_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if "reportBytes" not in data:
            logger.error(f"BIP response missing reportBytes. Response: {data}")
            return {}

        report_bytes = base64.b64decode(data["reportBytes"])
        csv_text = report_bytes.decode("utf-8", errors="replace")

        invoice_map = {}
        reader = csv.DictReader(io.StringIO(csv_text))

        for row in reader:
            clean_row = {k.strip().replace(" ", ""): v.strip() for k, v in row.items() if k}

            trx_num = clean_row.get("TransactionNumber") or clean_row.get("InvoiceNumber")
            if trx_num:
                invoice_map[trx_num] = clean_row

        logger.info(f"Successfully loaded {len(invoice_map)} invoices from BIP cache.")
        return invoice_map

    except Exception as e:
        logger.error(f"Failed to execute BIP report: {e}")
        return {}
