from __future__ import annotations
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

def safe_float_match(expected_amount: Any, actual_amount: Any, allow_missing_expected: bool = False, tolerance: float = 0.01) -> bool:
    if expected_amount is None:
        return allow_missing_expected
    if actual_amount is None:
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
        return math.isclose(f_exp, f_act, abs_tol=tolerance)
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



def score_receipt_candidate(candidate: dict[str, Any], receipt_num: str, amount: float | None, receipt_date: str, customer_name: str) -> int:
    score = 0
    if receipt_num and safe_str_match(candidate.get("ReceiptNumber"), receipt_num):
        score += 100
    if amount is not None and safe_float_match(candidate.get("Amount"), amount, allow_missing_expected=True):
        score += 40
    if receipt_date and safe_date_match(candidate.get("ReceiptDate"), receipt_date):
        score += 40
    if customer_name and safe_str_match(candidate.get("CustomerName"), customer_name):
        score += 20
    return score

def select_best_receipt(candidates: list[dict[str, Any]], receipt_num: str, amount: float | None, receipt_date: str, customer_name: str) -> tuple[dict[str, Any] | None, str | None]:
    if not candidates:
        return None, None
    scored = [(score_receipt_candidate(c, receipt_num, amount, receipt_date, customer_name), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score = scored[0][0]
    if best_score < 100:
        return None, "score_too_low"
    if len(scored) > 1 and scored[1][0] == best_score:
        return None, "ambiguous"
    return scored[0][1], f"score_{best_score}"

async def check_receipt_cascading(client: httpx.AsyncClient, user: str, password: str, receipt_num: str, amount: float | None, receipt_date: str, customer_name: str, sem: asyncio.Semaphore | None = None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    context = OracleClientContext(client, user, password, sem=sem)
    formatted_date = format_oracle_date(receipt_date)
    fields = "ReceiptNumber,Amount,State,CustomerName,ReceiptDate"

    async def _evaluate_candidates(candidates_list: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not candidates_list:
            return None
        seen = set()
        unique_candidates = []
        for c in candidates_list:
            key = (str(c.get("ReceiptNumber", "")).strip(), str(c.get("Amount", "")).strip(), str(c.get("ReceiptDate", "")).strip(), str(c.get("State", "")).strip())
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)
        
        # Phase 1: Unapplied
        unapplied_candidates = [c for c in unique_candidates if is_receipt_unapplied(c)]
        match, reason = select_best_receipt(unapplied_candidates, receipt_num, amount, receipt_date, customer_name)
        if match:
            logger.info(f"Receipt Matched ({reason}) in UNAPPLIED phase!")
            return {"matched_in_oracle": True, "fusion_receipt_number": match.get("ReceiptNumber"), "fusion_receipt_date": match.get("ReceiptDate"), "fusion_customer_name": match.get("CustomerName"), "match_phase": "UNAPPLIED", "match_rule": reason}
        elif reason == "ambiguous":
            return {"matched_in_oracle": False, "error": "Ambiguous match: multiple identical scoring unapplied candidates found."}

        # Phase 2: Applied
        applied_candidates = [c for c in unique_candidates if not is_receipt_unapplied(c)]
        match, reason = select_best_receipt(applied_candidates, receipt_num, amount, receipt_date, customer_name)
        if match:
            logger.info(f"Receipt Matched ({reason}) in APPLIED phase!")
            return {"matched_in_oracle": True, "fusion_receipt_number": match.get("ReceiptNumber"), "fusion_receipt_date": match.get("ReceiptDate"), "fusion_customer_name": match.get("CustomerName"), "match_phase": "APPLIED", "match_rule": reason}
        elif reason == "ambiguous":
            return {"matched_in_oracle": False, "error": "Ambiguous match: multiple identical scoring applied candidates found."}
        return None

    try:
        primary_candidates = []
        if receipt_num:
            query = f"ReceiptNumber='{escape_oracle(receipt_num)}'"
            primary_candidates.extend(await fetch_oracle_candidates(context, "standardReceipts", query, fields=fields))
        if amount is not None and formatted_date:
            query = f"Amount={amount} and ReceiptDate='{formatted_date}'"
            primary_candidates.extend(await fetch_oracle_candidates(context, "standardReceipts", query, fields=fields))
        
        result = await _evaluate_candidates(primary_candidates)
        if result:
            return result

        if customer_name:
            customer_candidates = []
            query = f"CustomerName='{escape_oracle(customer_name)}'"
            customer_candidates.extend(await fetch_oracle_candidates(context, "standardReceipts", query, fields=fields))
            
            result = await _evaluate_candidates(customer_candidates)
            if result:
                return result

    except httpx.RequestError as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Request Error: {str(e)}"}
    except Exception as e:
        logger.exception("Unexpected error in check_receipt_cascading fetch")
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    return {"matched_in_oracle": False, "error": "No single match found after scoring evaluation."}

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
        logger.error(f"Raw Invoice fetch exception: {e}")
        raise e

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

    return candidates

async def fetch_by_field(context: OracleClientContext, query_key: str, raw_value: str, inv_fields: str, cm_fields: str, is_unique: bool = True) -> list[dict[str, Any]]:
    """Concurrent fetch for Invoices and Credit Memos to prevent N+1 fallback."""
    query = f"{query_key}='{escape_oracle(raw_value)}'"
    return await fetch_by_query(context, query, inv_fields, cm_fields, force_both=not is_unique)

def score_invoice_candidate(candidate: dict[str, Any], invoice_number: str, doc_number: str, inv_date: str, amount: float | None, customer_name: str) -> int:
    score = 0
    if invoice_number and safe_str_match(candidate.get("TransactionNumber"), invoice_number):
        score += 100
    if doc_number and safe_str_match(candidate.get("DocumentNumber"), doc_number):
        score += 80
    if inv_date and safe_date_match(candidate.get("TransactionDate"), inv_date):
        score += 40
    if amount is not None and safe_float_match(candidate.get("EnteredAmount"), amount, allow_missing_expected=True):
        score += 40
    if customer_name and safe_str_match(candidate.get("BillToCustomerName"), customer_name):
        score += 20
    return score

def select_best_invoice(candidates: list[dict[str, Any]], invoice_number: str, doc_number: str, inv_date: str, amount: float | None, customer_name: str) -> tuple[dict[str, Any] | None, str | None]:
    if not candidates:
        return None, None
    scored = [(score_invoice_candidate(c, invoice_number, doc_number, inv_date, amount, customer_name), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score = scored[0][0]
    if best_score < 100:
        return None, "score_too_low"
    if len(scored) > 1 and scored[1][0] == best_score:
        return None, "ambiguous"
    return scored[0][1], f"score_{best_score}"

async def check_invoice_cascading(client: httpx.AsyncClient, user: str, password: str, invoice_number: str, inv_date: str, amount: float | None, document_number: str, customer_name: str, cache_customer: dict[str, list[dict[str, Any]]] | None = None, customer_lock: asyncio.Lock | None = None, sem: asyncio.Semaphore | None = None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    context = OracleClientContext(client, user, password, sem=sem)
    inv_fields = "TransactionNumber,TransactionDate,EnteredAmount,InvoiceStatus,InvoiceBalanceAmount,DocumentNumber,BillToCustomerName"
    cm_fields = "TransactionNumber,TransactionDate,EnteredAmount,CreditMemoStatus,TransactionBalanceDue,DocumentNumber,BillToCustomerName"

    async def _evaluate_candidates(candidates_list: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not candidates_list:
            return None
        seen = set()
        unique_candidates = []
        for c in candidates_list:
            key = (str(c.get("TransactionNumber", "")).strip(), str(c.get("EnteredAmount", "")).strip(), str(c.get("TransactionDate", "")).strip(), str(c.get("DocumentNumber", "")).strip())
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)
        
        # Phase 1: Open
        open_candidates = [c for c in unique_candidates if is_invoice_open(c)]
        match, reason = select_best_invoice(open_candidates, invoice_number, document_number, inv_date, amount, customer_name)
        if match:
            logger.info(f"Invoice Matched ({reason}) in OPEN phase!")
            return {"matched_in_oracle": True, "fusion_invoice_number": match.get("TransactionNumber"), "fusion_invoice_date": match.get("TransactionDate"), "fusion_invoice_amount": float(str(match.get("EnteredAmount")).replace(",", "")) if match.get("EnteredAmount") is not None else None, "match_phase": "OPEN", "match_rule": reason}
        elif reason == "ambiguous":
            return {"matched_in_oracle": False, "error": f"Ambiguous match for invoice {invoice_number} in OPEN phase."}

        # Phase 2: Closed
        closed_candidates = [c for c in unique_candidates if not is_invoice_open(c)]
        match, reason = select_best_invoice(closed_candidates, invoice_number, document_number, inv_date, amount, customer_name)
        if match:
            logger.info(f"Invoice Matched ({reason}) in CLOSED phase!")
            return {"matched_in_oracle": True, "fusion_invoice_number": match.get("TransactionNumber"), "fusion_invoice_date": match.get("TransactionDate"), "fusion_invoice_amount": float(str(match.get("EnteredAmount")).replace(",", "")) if match.get("EnteredAmount") is not None else None, "match_phase": "CLOSED", "match_rule": reason}
        elif reason == "ambiguous":
            return {"matched_in_oracle": False, "error": f"Ambiguous match for invoice {invoice_number} in CLOSED phase."}
        
        return None

    try:
        primary_candidates = []
        if invoice_number:
            primary_candidates.extend(await fetch_by_field(context, "TransactionNumber", invoice_number, inv_fields, cm_fields))
        if document_number:
            primary_candidates.extend(await fetch_by_field(context, "DocumentNumber", document_number, inv_fields, cm_fields))
        
        result = await _evaluate_candidates(primary_candidates)
        if result:
            return result

        if customer_name:
            customer_candidates = []
            if cache_customer is not None and customer_lock is not None:
                c_name_lower = customer_name.lower()
                if c_name_lower not in cache_customer:
                    async with customer_lock:
                        if c_name_lower not in cache_customer:
                            cache_customer[c_name_lower] = await fetch_by_field(context, "BillToCustomerName", customer_name, inv_fields, cm_fields, is_unique=False)
                customer_candidates.extend(cache_customer[c_name_lower])
            else:
                customer_candidates.extend(await fetch_by_field(context, "BillToCustomerName", customer_name, inv_fields, cm_fields, is_unique=False))
            
            result = await _evaluate_candidates(customer_candidates)
            if result:
                return result

    except httpx.RequestError as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Request Error: {str(e)}"}
    except Exception as e:
        logger.exception("Unexpected error in check_invoice_cascading fetch")
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    return {"matched_in_oracle": False, "error": f"No single match found for invoice {invoice_number}."}
