from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

# defusedxml only re-exports the parsing half of ElementTree, and the request builder needs
# the serialising half. The SOAP envelope is assembled from our own parameters and only ever
# written out, never parsed; the untrusted Oracle response is parsed by defusedxml below.
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring  # nosec B405

import defusedxml.ElementTree as DET  # type: ignore
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
    def __init__(self) -> None:
        self.local: TTLCache[str, Any] = TTLCache(maxsize=BIP_MAX_CACHE_ENTRIES, ttl=BIP_CACHE_TTL_SECONDS)
        self.redis: Any = None
        # Read through a local so the type narrowing applies to from_url as well: REDIS_URL
        # is `str | None` and the guard is the only thing that proves it is a str here.
        redis_url = settings.REDIS_URL
        if redis_url:
            try:
                import redis.asyncio as redis
                self.redis = redis.from_url(redis_url)
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

# One entry per cache key that is currently being fetched.
_inflight: dict[str, asyncio.Future[list[dict[str, Any]]]] = {}


async def _fetch_once(
    key: str, factory: Callable[[], Awaitable[list[dict[str, Any]]]]
) -> list[dict[str, Any]]:
    """Run `factory` for `key`, or await the run another caller already started.

    `_discover_by_invoice_sequence` fans out up to DEFAULT_CONCURRENCY fetches that all
    share one cache key, and the cache is cold for a key nobody has asked for before, so
    without this a cold start issues one BIP request per fan-out member. The lookup and the
    registration happen with no await between them, so no lock is needed: this is
    single-threaded asyncio and a yield would let a second caller in between.

    A failure reaches the waiters too, because they would have received the same error had
    they issued the request themselves. The entry is always removed, so a cancelled caller
    cannot leave a future behind for a later event loop to await.
    """
    existing = _inflight.get(key)
    if existing is not None:
        return await asyncio.shield(existing)

    owned: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
    _inflight[key] = owned
    try:
        results = await factory()
    except BaseException as error:
        if not owned.done():
            owned.set_exception(error)
            # Consumed here so the loop does not log it as never retrieved. A waiter that
            # awaits the future still sees it.
            owned.exception()
        raise
    else:
        if not owned.done():
            owned.set_result(results)
        return results
    finally:
        _inflight.pop(key, None)


def _get_cache_key(report_type: str, parameters: list[dict[str, Any]]) -> str:
    """Build an injective key from a report type and its parameters.

    Every component is percent-encoded, so a value can never contain the `=` that joins a
    name to a value or the `|` that joins two parameters. Without that, two different SOAP
    requests map to one key: the two parameters `A=1, B=2` and the single parameter
    `A=1|B=2` both rendered as `A=1|B=2` and shared a cache entry. `quote` is injective
    over byte strings and the encoded names pin each field's position, so once every
    component is escaped, distinct requests cannot share a key.
    """
    sorted_params = sorted(parameters, key=lambda x: x["name"])
    param_str = "|".join(
        f"{quote(str(p['name']), safe='')}={quote(str(p['values'][0]), safe='')}"
        for p in sorted_params
    )
    return f"{quote(report_type, safe='')}::{param_str}"





def _parse_soap_response_sync(response_text: str) -> list[dict[str, Any]]:
    report_bytes_b64 = None
    for _event, elem in DET.iterparse(io.StringIO(response_text), events=("end",)):
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

    async def _fetch() -> list[dict[str, Any]]:
        return await _post_bip_report(
            client, username, password, candidate_paths, valid_parameters, report_type, cache_key
        )

    return await _fetch_once(cache_key, _fetch)


async def _post_bip_report(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    candidate_paths: list[str],
    parameters: list[dict[str, Any]],
    report_type: str,
    cache_key: str,
) -> list[dict[str, Any]]:
    last_error = None
    valid_paths = [p for p in candidate_paths if p and p.strip()]
    base_url = settings.ORACLE_URL.rstrip("/")
    soap_url = f"{base_url}/xmlpserver/services/ExternalReportWSSService"

    if base_url.startswith("http://") and "localhost" not in base_url and "127.0.0.1" not in base_url:
        logger.warning(f"Sending Oracle BIP credentials over unencrypted HTTP protocol to {base_url}!")

    headers = {"Content-Type": 'application/soap+xml;charset=UTF-8;action=""', "User-Agent": "httpx"}

    soap_ns = "http://www.w3.org/2003/05/soap-envelope"
    pub_ns = "http://xmlns.oracle.com/oxp/service/PublicReportService"
    register_namespace("soap", soap_ns)
    register_namespace("pub", pub_ns)

    for report_path in valid_paths:
        envelope = Element(f"{{{soap_ns}}}Envelope")
        SubElement(envelope, f"{{{soap_ns}}}Header")
        body = SubElement(envelope, f"{{{soap_ns}}}Body")
        run_report = SubElement(body, f"{{{pub_ns}}}runReport")

        report_req = SubElement(run_report, f"{{{pub_ns}}}reportRequest")
        attr_format = SubElement(report_req, f"{{{pub_ns}}}attributeFormat")
        attr_format.text = "csv"

        if parameters:
            param_names_values = SubElement(report_req, f"{{{pub_ns}}}parameterNameValues")
            for param in parameters:
                item = SubElement(param_names_values, f"{{{pub_ns}}}item")
                name = SubElement(item, f"{{{pub_ns}}}name")
                name.text = param["name"]
                values = SubElement(item, f"{{{pub_ns}}}values")
                val_item = SubElement(values, f"{{{pub_ns}}}item")
                val_item.text = str(param["values"][0])

        report_path_el = SubElement(report_req, f"{{{pub_ns}}}reportAbsolutePath")
        report_path_el.text = report_path.strip()

        size = SubElement(report_req, f"{{{pub_ns}}}sizeOfDataChunkDownload")
        size.text = str(BIP_CHUNK_DOWNLOAD_SIZE)

        user_el = SubElement(run_report, f"{{{pub_ns}}}userID")
        user_el.text = username
        pass_el = SubElement(run_report, f"{{{pub_ns}}}password")
        pass_el.text = password

        xml_payload = tostring(envelope, encoding="utf-8", xml_declaration=False).decode("utf-8")

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
