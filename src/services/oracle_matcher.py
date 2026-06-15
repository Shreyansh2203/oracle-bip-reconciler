import asyncio
import logging
import math
import os
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_oracle_url
from src.utils.date_formatter import format_oracle_date, safe_date_match

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 10
DEFAULT_ORACLE_LIMIT = 499
CENTS_MULTIPLIER = 100

@dataclass
class OracleClientContext:
    client: httpx.AsyncClient
    user: str
    password: str
    sem: asyncio.Semaphore | None = None

class OracleTransientError(Exception):
    pass

def escape_oracle(val: Any) -> str:
    """Escape single quotes for Oracle REST API query injection prevention."""
    if val is None:
        return ""
    return str(val).replace("'", "''")

async def fetch_oracle_candidates(context: OracleClientContext, endpoint: str, query: str, limit: int | None = None, fields: str = "") -> list[dict[str, Any]]:
    """
    Fetch candidates from Oracle using indexable fields, with pagination to fix truncation.
    TODO: Move the @retry decorator inside the pagination loop to retry per-page rather than resetting the entire fetch on transient errors.
    """
    try:
        if limit is None:
            limit = int(os.getenv("ORACLE_LIMIT", str(DEFAULT_ORACLE_LIMIT)))
        q = urllib.parse.quote(query)
        all_items = []
        offset = 0
        has_more = True

        @retry(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=MIN_WAIT_SECONDS, max=MAX_WAIT_SECONDS),
            retry=retry_if_exception_type((OracleTransientError, httpx.RequestError)),
            reraise=True
        )
        async def _fetch_page(current_offset: int) -> dict[str, Any]:
            page_url = f"{get_oracle_url()}/fscmRestApi/resources/11.13.18.05/{endpoint}?q={q}&limit={limit}&offset={current_offset}"
            if fields:
                page_url += f"&fields={fields}"

            if context.sem:
                async with context.sem:
                    response = await context.client.get(page_url, auth=(context.user, context.password), timeout=15.0)
            else:
                response = await context.client.get(page_url, auth=(context.user, context.password), timeout=15.0)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in [429, 500, 502, 503, 504]:
                logger.warning(f"Transient Oracle fetch error ({response.status_code}): {response.text}. Retrying...")
                raise OracleTransientError(f"Transient Oracle API Error {response.status_code}: {response.text}")
            else:
                logger.error(f"Oracle fetch error ({response.status_code}): {response.text}")
                raise Exception(f"Oracle API Error {response.status_code}: {response.text}")

        pages = 0
        MAX_PAGES = int(os.getenv("ORACLE_MAX_PAGES", "100"))
        while has_more and pages < MAX_PAGES:
            data = await _fetch_page(offset)
            items = data.get("items", [])
            all_items.extend(items)
            has_more = data.get("hasMore", False)
            offset += limit
            pages += 1

        if has_more:
            logger.warning(f"Pagination capped at {MAX_PAGES} pages. Some candidates may be truncated.")

        return all_items
    except (OracleTransientError, httpx.RequestError) as e:
        logger.warning(f"Transient Oracle fetch exception: {e}")
        raise e
    except Exception as e:
        logger.error(f"Permanent Oracle fetch exception: {e}")
        raise e

def safe_float_match(expected_amount: Any, actual_amount: Any) -> bool:
    if expected_amount is None or actual_amount is None:
        return False
    try:
        exp_str = str(expected_amount).strip().replace(",", "")
        act_str = str(actual_amount).strip().replace(",", "")
        if not exp_str or not act_str or exp_str.lower() == "none" or act_str.lower() == "none":
            return False
        f_exp = float(exp_str)
        f_act = float(act_str)
        if not math.isfinite(f_exp) or not math.isfinite(f_act):
            return False
        return Decimal(exp_str) == Decimal(act_str)
    except (ValueError, TypeError, InvalidOperation):
        return False

def safe_str_match(val1: Any, val2: Any) -> bool:
    if val1 is None or val2 is None or str(val1).strip() == "" or str(val2).strip() == "":
        return False
    return str(val1).strip().lower() == str(val2).strip().lower()

def is_receipt_unapplied(candidate: dict[str, Any]) -> bool:
    state = str(candidate.get("State", "")).strip().lower()
    return state in ["unapplied", "unapp", "unid"]

def is_invoice_open(candidate: dict[str, Any]) -> bool:
    status = str(candidate.get("InvoiceStatus", "")).strip().lower()
    if status == "closed":
        return False

    # Try to parse InvoiceBalanceAmount if available
    bal = candidate.get("InvoiceBalanceAmount")
    if bal is not None:
        try:
            return abs(float(bal)) > 0
        except ValueError:
            pass

    # Default to Open if not explicitly closed
    return True

def apply_rules_to_candidates(candidates: list[dict[str, Any]], rules: list[tuple[str, Callable[[dict[str, Any]], bool]]]) -> tuple[dict[str, Any] | None, str | None]:
    for rule_name, condition in rules:
        matches = [candidate for candidate in candidates if condition(candidate)]
        if len(matches) == 1:
            return matches[0], rule_name
        elif len(matches) > 1:
            logger.debug(f"Rule {rule_name}: {len(matches)} candidates, continuing to next rule.")
    return None, None

async def check_receipt_cascading(client: httpx.AsyncClient, user: str, password: str, receipt_num: str, amount: float | None, receipt_date: str, customer_name: str, sem: asyncio.Semaphore | None = None) -> dict[str, Any]:
    """
    Receipt Cascading matching: Two-Phase Search (Unapplied first, then Applied).
    """
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    context = OracleClientContext(client, user, password, sem=sem)
    formatted_date = format_oracle_date(receipt_date)
    candidates = []

    fields = "ReceiptNumber,Amount,State,CustomerName,ReceiptDate"
    try:
        if receipt_num:
            query = f"ReceiptNumber='{escape_oracle(receipt_num)}'"
            candidates.extend(await fetch_oracle_candidates(context, "standardReceipts", query, fields=fields))

        if customer_name:
            query = f"CustomerName='{escape_oracle(customer_name)}'"
            candidates.extend(await fetch_oracle_candidates(context, "standardReceipts", query, fields=fields))

        if amount is not None and formatted_date:
            query = f"Amount={float(amount):.2f} and ReceiptDate='{formatted_date}'"
            candidates.extend(await fetch_oracle_candidates(context, "standardReceipts", query, fields=fields))

        # Deduplicate candidates by composite key to preserve distinct records
        seen = set()
        unique_candidates = []
        for c in candidates:
            key = (
                str(c.get("ReceiptNumber", "")).strip(),
                str(c.get("Amount", "")).strip(),
                str(c.get("ReceiptDate", "")).strip(),
                str(c.get("State", "")).strip(),
            )
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)
        candidates = unique_candidates

    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": "No candidates found in Oracle for ReceiptNumber or CustomerName."}

    # 2. Local Filtering Rules
    if receipt_num:
        rules = [
            ("A1", lambda candidate: safe_str_match(candidate.get("ReceiptNumber"), receipt_num) and safe_float_match(candidate.get("Amount"), amount) and safe_date_match(candidate.get("ReceiptDate"), receipt_date) and (safe_str_match(candidate.get("CustomerName"), customer_name) if customer_name else True)),
            ("A2", lambda candidate: safe_str_match(candidate.get("ReceiptNumber"), receipt_num) and safe_float_match(candidate.get("Amount"), amount) and (safe_str_match(candidate.get("CustomerName"), customer_name) if customer_name else True)),
            ("A3", lambda candidate: safe_str_match(candidate.get("ReceiptNumber"), receipt_num) and (safe_str_match(candidate.get("CustomerName"), customer_name) if customer_name else True)),
            ("A4", lambda candidate: bool(customer_name) and safe_str_match(candidate.get("CustomerName"), customer_name) and safe_float_match(candidate.get("Amount"), amount) and safe_date_match(candidate.get("ReceiptDate"), receipt_date)),
        ]
    else:
        # B1 matches Amount+Date with optional Customer narrowing.
        # B2 (Customer+Amount+Date) was removed as it is functionally identical
        # to B1 when customer is present, and unreachable when customer is absent.
        rules = [
            ("B1", lambda candidate: safe_float_match(candidate.get("Amount"), amount) and safe_date_match(candidate.get("ReceiptDate"), receipt_date) and (safe_str_match(candidate.get("CustomerName"), customer_name) if customer_name else True)),
        ]

    # Phase 1: Search Unapplied Receipts
    unapplied_candidates = [candidate for candidate in candidates if is_receipt_unapplied(candidate)]
    match, rule_name = apply_rules_to_candidates(unapplied_candidates, rules)

    if match:
        logger.info(f"Receipt Rule {rule_name} Matched in UNAPPLIED phase!")
    else:
        # Phase 2: Search Applied Receipts
        applied_candidates = [candidate for candidate in candidates if not is_receipt_unapplied(candidate)]
        match, rule_name = apply_rules_to_candidates(applied_candidates, rules)
        if match:
            logger.info(f"Receipt Rule {rule_name} Matched in APPLIED fallback phase!")

    if match:
        return {
            "matched_in_oracle": True,
            "fusion_receipt_number": match.get("ReceiptNumber"),
            "fusion_receipt_date": match.get("ReceiptDate"),
            "fusion_customer_name": match.get("CustomerName"),
            "match_phase": "UNAPPLIED" if is_receipt_unapplied(match) else "APPLIED",
            "match_rule": rule_name
        }

    return {"matched_in_oracle": False, "error": "No single match found after two-phase cascading rules."}

async def fetch_by_query(context: OracleClientContext, query: str, inv_fields: str, cm_fields: str, force_both: bool = False) -> list[dict[str, Any]]:
    """
    Sequentially fetches invoices, then credit memos (if force_both is True or no invoices found).
    """
    candidates = []
    last_exception = None

    try:
        inv_res = await fetch_oracle_candidates(context, "receivablesInvoices", query, fields=inv_fields)
        if isinstance(inv_res, list):
            candidates.extend(inv_res)
    except Exception as e:
        logger.warning(f"Raw Invoice fetch exception: {e}")
        last_exception = e

    if force_both or not candidates:
        try:
            cm_res = await fetch_oracle_candidates(context, "receivablesCreditMemos", query, fields=cm_fields)
            if isinstance(cm_res, list):
                for candidate in cm_res:
                    candidate["InvoiceStatus"] = candidate.get("CreditMemoStatus")
                    candidate["InvoiceBalanceAmount"] = candidate.get("TransactionBalanceDue")
                candidates.extend(cm_res)
        except Exception as e:
            logger.warning(f"Raw CM fetch exception: {e}")
            if last_exception:
                raise Exception(f"Both Invoice and CM fetch failed. Invoice err: {last_exception}, CM err: {e}") from e
            raise e

    if last_exception is not None and not candidates:
        raise last_exception

    return candidates

async def fetch_by_field(context: OracleClientContext, query_key: str, raw_value: str, inv_fields: str, cm_fields: str, is_unique: bool = True) -> list[dict[str, Any]]:
    """Concurrent fetch for Invoices and Credit Memos to prevent N+1 fallback."""
    query = f"{query_key}='{escape_oracle(raw_value)}'"
    return await fetch_by_query(context, query, inv_fields, cm_fields, force_both=not is_unique)

async def check_invoice_cascading(client: httpx.AsyncClient, user: str, password: str, invoice_number: str, inv_date: str, amount: float | None, document_number: str, customer_name: str, cache_customer: dict[str, list[dict[str, Any]]] | None = None, customer_lock: asyncio.Lock | None = None, sem: asyncio.Semaphore | None = None) -> dict[str, Any]:
    """
    Invoice Cascading matching: Two-Phase Search (Open first, then Closed).
    Uses pre-fetched dictionaries (cache_inv_num, cache_doc_num) if available to avoid HTTP calls.
    Lazily fetches and caches customer_name fallbacks using customer_lock to prevent N+1 duplicate calls.
    """
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    context = OracleClientContext(client, user, password, sem=sem)
    candidates = []

    inv_fields = "TransactionNumber,TransactionDate,EnteredAmount,InvoiceStatus,InvoiceBalanceAmount,DocumentNumber,BillToCustomerName"
    cm_fields = "TransactionNumber,TransactionDate,EnteredAmount,CreditMemoStatus,TransactionBalanceDue,DocumentNumber,BillToCustomerName"

    try:
        if invoice_number:
            candidates.extend(await fetch_by_field(context, "TransactionNumber", invoice_number, inv_fields, cm_fields))
            # Also fetch by prefix for Rule 3
            escaped_prefix = escape_oracle(invoice_number).upper().replace("%", "\\%").replace("_", "\\_")
            prefix_query = f"TransactionNumber LIKE '{escaped_prefix}%'"
            candidates.extend(await fetch_by_query(context, prefix_query, inv_fields, cm_fields))

        if document_number:
            candidates.extend(await fetch_by_field(context, "DocumentNumber", document_number, inv_fields, cm_fields))

        if customer_name:
            if cache_customer is not None and customer_lock is not None:
                # Lazy fetching with lock to prevent N+1 duplicate calls
                c_name_lower = customer_name.lower()
                if c_name_lower not in cache_customer:
                    async with customer_lock:
                        if c_name_lower not in cache_customer:
                            try:
                                result = await fetch_by_field(context, "BillToCustomerName", customer_name, inv_fields, cm_fields, is_unique=False)
                                cache_customer[c_name_lower] = result
                            except Exception as e:
                                logger.error(f"Customer fallback query failed: {str(e)}")
                                cache_customer[c_name_lower] = []
                candidates.extend(cache_customer[c_name_lower])
            else:
                try:
                    candidates.extend(await fetch_by_field(context, "BillToCustomerName", customer_name, inv_fields, cm_fields, is_unique=False))
                except Exception as e:
                    logger.error(f"Customer fallback query failed: {str(e)}")

        # Deduplicate candidates by composite key to preserve distinct records
        seen = set()
        unique_candidates = []
        for c in candidates:
            key = (
                str(c.get("TransactionNumber", "")).strip(),
                str(c.get("EnteredAmount", "")).strip(),
                str(c.get("TransactionDate", "")).strip(),
                str(c.get("DocumentNumber", "")).strip(),
            )
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)
        candidates = unique_candidates

    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": f"No candidates found for invoice {invoice_number}."}

    # 2. Local Filtering Rules
    # Rules ordered per report_processing_rules.md: 1a, 1b, 2, 3, 4
    rules = [
        ("1a", lambda candidate: safe_str_match(candidate.get("TransactionNumber"), invoice_number) and safe_date_match(candidate.get("TransactionDate"), inv_date) and safe_float_match(candidate.get("EnteredAmount"), amount)),
        ("1b", lambda candidate: safe_str_match(candidate.get("TransactionNumber"), invoice_number) and safe_float_match(candidate.get("EnteredAmount"), amount)),
        ("2",  lambda candidate: bool(document_number) and safe_str_match(candidate.get("DocumentNumber"), document_number) and safe_date_match(candidate.get("TransactionDate"), inv_date) and safe_float_match(candidate.get("EnteredAmount"), amount)),
        ("3",  lambda candidate: bool(invoice_number) and str(candidate.get("TransactionNumber", "")).lower().startswith(str(invoice_number).lower()) and safe_date_match(candidate.get("TransactionDate"), inv_date) and safe_float_match(candidate.get("EnteredAmount"), amount)),
        ("4",  lambda candidate: bool(customer_name) and safe_str_match(candidate.get("BillToCustomerName"), customer_name) and safe_date_match(candidate.get("TransactionDate"), inv_date) and safe_float_match(candidate.get("EnteredAmount"), amount)),
    ]

    # Phase 1: Search Open Invoices
    open_candidates = [candidate for candidate in candidates if is_invoice_open(candidate)]
    match, rule_name = apply_rules_to_candidates(open_candidates, rules)

    if match:
        logger.info(f"Invoice Rule {rule_name} Matched in OPEN phase!")
    else:
        # Phase 2: Search Closed Invoices
        closed_candidates = [candidate for candidate in candidates if not is_invoice_open(candidate)]
        match, rule_name = apply_rules_to_candidates(closed_candidates, rules)
        if match:
            logger.info(f"Invoice Rule {rule_name} Matched in CLOSED fallback phase!")

    if match:
        return {
            "matched_in_oracle": True,
            "fusion_invoice_number": match.get("TransactionNumber"),
            "fusion_invoice_date": match.get("TransactionDate"),
            "fusion_invoice_amount": float(str(match.get("EnteredAmount")).replace(",", "")) if match.get("EnteredAmount") is not None else None,
            "match_phase": "OPEN" if is_invoice_open(match) else "CLOSED",
            "match_rule": rule_name
        }

    return {"matched_in_oracle": False, "error": f"No single match found for invoice {invoice_number}."}
