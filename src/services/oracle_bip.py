import base64
import csv
import io
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_oracle_url

logger = logging.getLogger(__name__)

BIP_TIMEOUT = 60.0
CHUNK_DOWNLOAD_SIZE = -1
MAX_RETRIES = 3
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 10

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
    retry=retry_if_exception_type(httpx.RequestError),
    reraise=True
)
async def run_bip_bulk_match(client: httpx.AsyncClient, user: str, pwd: str, invoice_numbers: list[str]) -> dict[str, Any]:
    """
    Fetches all invoice details in a single bulk request via Oracle BI Publisher.
    Returns a dictionary mapping TransactionNumber -> Invoice details.
    """
    report_path = "Custom/Financials/Receivable Transactions/Invoice Details Report.xdo"
    url = f"{get_oracle_url()}/xmlpserver/services/rest/v1/reports/{report_path.replace('/', '%2F')}/run"

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
            clean_row = {k.strip().upper().replace(" ", ""): (v or "").strip() for k, v in row.items() if k}

            trx_num = clean_row.get("TRANSACTION_NUMBER") or clean_row.get("INVOICE_NUMBER") or clean_row.get("TRANSACTIONNUMBER")
            if trx_num:
                invoice_map[trx_num] = clean_row

        logger.info(f"Successfully loaded {len(invoice_map)} invoices from BIP cache.")
        return invoice_map

    except httpx.RequestError as e:
        logger.warning(f"Transient BIP fetch error: {e}")
        raise e
    except Exception as e:
        logger.error(f"Failed to execute BIP report: {e}")
        return {}
