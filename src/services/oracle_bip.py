from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
from typing import Any

import defusedxml.ElementTree as ET  # type: ignore
import httpx
from cachetools import TTLCache
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.constants import (
    BIP_CACHE_TTL_SECONDS,
    BIP_CHUNK_DOWNLOAD_SIZE,
    BIP_MAX_RETRIES,
    BIP_MAX_WAIT_SECONDS,
    BIP_MIN_WAIT_SECONDS,
    BIP_TIMEOUT,
    DEFAULT_INVOICE_REPORT_PATH,
    DEFAULT_RECEIPT_REPORT_PATH,
)
from src.core.config import settings

logger = logging.getLogger(__name__)

BIP_MAX_CACHE_ENTRIES = 1000

class AsyncCache:
    def __init__(self):
        self.local = TTLCache(maxsize=BIP_MAX_CACHE_ENTRIES, ttl=BIP_CACHE_TTL_SECONDS)
        self.redis = None
        if getattr(settings, 'REDIS_URL', None):
            try:
                import redis.asyncio as redis
                self.redis = redis.from_url(settings.REDIS_URL)
                logger.info("Redis cache initialized for Oracle BIP")
            except ImportError:
                logger.warning("Redis is configured but redis package is not installed. Falling back to local cache.")

    async def get(self, key: str) -> Any:
        if self.redis:
            try:
                val = await self.redis.get(key)
                return json.loads(val) if val else None
            except Exception as e:
                logger.error(f"Redis get error: {e}")
                return self.local.get(key)
        return self.local.get(key)

    async def set(self, key: str, value: Any) -> None:
        if self.redis:
            try:
                await self.redis.set(key, json.dumps(value), ex=BIP_CACHE_TTL_SECONDS)
            except Exception as e:
                logger.error(f"Redis set error: {e}")
                self.local[key] = value
        else:
            self.local[key] = value

_bip_cache = AsyncCache()

def _get_cache_key(report_type: str, parameters: list[dict[str, Any]]) -> str:
    sorted_params = sorted(parameters, key=lambda x: x["name"])
    param_str = "|".join([f"{p['name']}={p['values'][0]}" for p in sorted_params])
    return f"{report_type}::{param_str}"





def _parse_soap_response_sync(response_text: str) -> list[dict[str, Any]]:
    report_bytes_b64 = None
    for _event, elem in ET.iterparse(io.StringIO(response_text), events=("end",)):
        if elem.tag.endswith("}reportBytes") or elem.tag == "reportBytes":
            report_bytes_b64 = elem.text
            break
        elem.clear()

    if not report_bytes_b64:
        return []

    report_bytes = base64.b64decode(report_bytes_b64)
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

    cache_key = _get_cache_key(report_type, valid_parameters)
    cached_val = await _bip_cache.get(cache_key)
    if cached_val is not None:
        return cached_val

    last_error = None
    valid_paths = [p for p in candidate_paths if p and p.strip()]
    base_url = settings.ORACLE_URL.rstrip("/")
    soap_url = f"{base_url}/xmlpserver/services/ExternalReportWSSService"

    if base_url.startswith("http://") and "localhost" not in base_url and "127.0.0.1" not in base_url:
        logger.warning(f"Sending Oracle BIP credentials over unencrypted HTTP protocol to {base_url}!")

    headers = {"Content-Type": 'application/soap+xml;charset=UTF-8;action=""', "User-Agent": "httpx"}

    soap_ns = "http://www.w3.org/2003/05/soap-envelope"
    pub_ns = "http://xmlns.oracle.com/oxp/service/PublicReportService"
    ET.register_namespace("soap", soap_ns)
    ET.register_namespace("pub", pub_ns)

    for report_path in valid_paths:
        envelope = ET.Element(f"{{{soap_ns}}}Envelope")
        ET.SubElement(envelope, f"{{{soap_ns}}}Header")
        body = ET.SubElement(envelope, f"{{{soap_ns}}}Body")
        run_report = ET.SubElement(body, f"{{{pub_ns}}}runReport")

        report_req = ET.SubElement(run_report, f"{{{pub_ns}}}reportRequest")
        attr_format = ET.SubElement(report_req, f"{{{pub_ns}}}attributeFormat")
        attr_format.text = "csv"

        if valid_parameters:
            param_names_values = ET.SubElement(report_req, f"{{{pub_ns}}}parameterNameValues")
            for param in valid_parameters:
                item = ET.SubElement(param_names_values, f"{{{pub_ns}}}item")
                name = ET.SubElement(item, f"{{{pub_ns}}}name")
                name.text = param["name"]
                values = ET.SubElement(item, f"{{{pub_ns}}}values")
                val_item = ET.SubElement(values, f"{{{pub_ns}}}item")
                val_item.text = str(param["values"][0])

        report_path_el = ET.SubElement(report_req, f"{{{pub_ns}}}reportAbsolutePath")
        report_path_el.text = report_path.strip()

        size = ET.SubElement(report_req, f"{{{pub_ns}}}sizeOfDataChunkDownload")
        size.text = str(BIP_CHUNK_DOWNLOAD_SIZE)

        user_el = ET.SubElement(run_report, f"{{{pub_ns}}}userID")
        user_el.text = username
        pass_el = ET.SubElement(run_report, f"{{{pub_ns}}}password")
        pass_el.text = password

        xml_payload = ET.tostring(envelope, encoding="utf-8", xml_declaration=False).decode("utf-8")

        try:
            response = await client.post(
                soap_url, content=xml_payload, headers=headers, auth=(username, password), timeout=BIP_TIMEOUT
            )
            response.raise_for_status()

            try:
                results = _parse_soap_response_sync(response.text)
                await _bip_cache.set(cache_key, results)
                return results
            except Exception as parse_error:
                logger.error(f"Failed to parse SOAP XML: {parse_error}")
                raise Exception(f"Failed to parse SOAP response: {parse_error}") from parse_error

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
        DEFAULT_INVOICE_REPORT_PATH,
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
        DEFAULT_RECEIPT_REPORT_PATH,
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
