import base64
import csv
import io
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_oracle_url

logger = logging.getLogger(__name__)

RECEIPT_REPORT_PATH = "Custom/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo"
BIP_TIMEOUT = 60.0
CHUNK_DOWNLOAD_SIZE = -1
MAX_RETRIES = 3
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 10

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    reraise=True
)
async def run_bip_bulk_match(client: httpx.AsyncClient, user: str, pwd: str, invoice_numbers: list[str]) -> dict[str, Any]:
    """
    Fetches all invoice details in a single bulk request via Oracle BI Publisher.
    Returns a dictionary mapping TransactionNumber -> Invoice details.
    """
    # Updated paths from Oracle Cloud configuration
    report_path = "Custom/Finacials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo"
    url = f"{get_oracle_url()}/xmlpserver/services/rest/v1/reports/{report_path.replace('/', '%2F')}/run"

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
                        "values": invoice_numbers
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
                if trx_num not in invoice_map:
                    invoice_map[trx_num] = []
                invoice_map[trx_num].append(clean_row)

        logger.info(f"Successfully loaded {len(invoice_map)} invoices from BIP cache.")
        return invoice_map

    except httpx.HTTPStatusError as e:
        if e.response.status_code in [429, 500, 502, 503, 504]:
            logger.warning(f"Transient BIP HTTP error ({e.response.status_code}): {e}. Retrying...")
            raise e
        else:
            logger.error(f"Permanent BIP HTTP error ({e.response.status_code}): {e}")
            return {}
    except httpx.RequestError as e:
        logger.warning(f"Transient BIP fetch error: {e}")
        raise e
    except Exception as e:
        logger.error(f"Failed to execute BIP report: {e}")
        return {}
