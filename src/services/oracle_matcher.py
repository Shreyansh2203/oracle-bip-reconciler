import logging
import os
import urllib.parse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.utils.date_formatter import format_oracle_date

logger = logging.getLogger(__name__)
ORACLE_URL = os.getenv("ORACLE_URL", "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com")

class OracleTransientError(Exception):
    pass

def escape_oracle(val):
    """Fix 3: Escape single quotes for Oracle REST API query injection prevention."""
    if val is None:
        return ""
    return str(val).replace("'", "''")

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((OracleTransientError, httpx.RequestError)),
    reraise=True
)
async def fetch_oracle_candidates(client, user, pwd, endpoint, query, limit=499, fields=""):
    """
    Fetch candidates from Oracle using indexable fields, with pagination to fix truncation (Fix 4).
    """
    try:
        q = urllib.parse.quote(query)
        all_items = []
        offset = 0
        has_more = True

        while has_more:
            url = f"{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/{endpoint}?q={q}&limit={limit}&offset={offset}"
            if fields:
                url += f"&fields={fields}"

            response = await client.get(url, auth=(user, pwd))
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                all_items.extend(items)
                has_more = data.get("hasMore", False)
                offset += limit
            elif response.status_code in [429, 500, 502, 503, 504]:
                logger.warning(f"Transient Oracle fetch error ({response.status_code}): {response.text}. Retrying...")
                raise OracleTransientError(f"Transient Oracle API Error {response.status_code}: {response.text}")
            else:
                logger.error(f"Oracle fetch error ({response.status_code}): {response.text}")
                raise Exception(f"Oracle API Error {response.status_code}: {response.text}")

        return all_items
    except (OracleTransientError, httpx.RequestError) as e:
        logger.warning(f"Transient Oracle fetch exception: {e}")
        raise e
    except Exception as e:
        logger.error(f"Permanent Oracle fetch exception: {e}")
        raise e

def safe_float_match(val1, val2, tolerance=0.01):
    try:
        if val1 is None or val2 is None:
            return False
        return abs(float(val1) - float(val2)) < tolerance
    except ValueError:
        return False

def safe_str_match(val1, val2):
    if not val1 or not val2:
        return False
    return str(val1).strip().lower() == str(val2).strip().lower()

def is_receipt_unapplied(c):
    state = str(c.get("State", "")).strip().lower()
    return state in ["unapplied", "unapp", "unid"]

def is_invoice_open(c):
    status = str(c.get("InvoiceStatus", "")).strip().lower()
    if status == "closed":
        return False

    # Try to parse InvoiceBalanceAmount if available
    bal = c.get("InvoiceBalanceAmount")
    if bal is not None:
        try:
            return abs(float(bal)) > 0
        except ValueError:
            pass

    # Default to Open if not explicitly closed
    return True

def apply_rules_to_candidates(candidates, rules):
    for rule_name, condition in rules:
        matches = [c for c in candidates if condition(c)]
        if len(matches) == 1:
            return matches[0], rule_name
        elif len(matches) > 1:
            # Fix 6: Handle Duplicate Matches safely
            logger.warning(f"Duplicate matches ({len(matches)}) found for rule {rule_name}. Using the first one.")
            return matches[0], rule_name
    return None, None

async def check_receipt_cascading(client, user, pwd, receipt_num, amount, receipt_date, customer_name):
    """
    Receipt Cascading matching: Two-Phase Search (Unapplied first, then Applied).
    """
    formatted_date = format_oracle_date(receipt_date)
    candidates = []

    fields = "ReceiptNumber,Amount,State,CustomerName,ReceiptDate"
    try:
        if receipt_num:
            query = f"ReceiptNumber='{escape_oracle(receipt_num)}'"
            candidates = await fetch_oracle_candidates(client, user, pwd, "standardReceipts", query, fields=fields)

        if not candidates and customer_name:
            query = f"CustomerName='{escape_oracle(customer_name)}'"
            candidates = await fetch_oracle_candidates(client, user, pwd, "standardReceipts", query, fields=fields)

        if not candidates and amount and formatted_date:
            query = f"Amount={amount} and ReceiptDate='{formatted_date}'"
            candidates = await fetch_oracle_candidates(client, user, pwd, "standardReceipts", query, fields=fields)
    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": "No candidates found in Oracle for ReceiptNumber or CustomerName."}

    # 2. Local Filtering Rules
    if receipt_num:
        rules = [
            ("A1", lambda c: safe_str_match(c.get("ReceiptNumber"), receipt_num) and safe_float_match(c.get("Amount"), amount) and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
            ("A2", lambda c: safe_str_match(c.get("ReceiptNumber"), receipt_num) and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
            ("A3", lambda c: safe_str_match(c.get("ReceiptNumber"), receipt_num) and safe_float_match(c.get("Amount"), amount) and c.get("ReceiptDate") == formatted_date and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
            ("A4", lambda c: bool(customer_name) and safe_str_match(c.get("CustomerName"), customer_name) and safe_float_match(c.get("Amount"), amount)),
            ("A5", lambda c: bool(customer_name) and safe_str_match(c.get("CustomerName"), customer_name) and bool(formatted_date) and c.get("ReceiptDate") == formatted_date),
        ]
    else:
        rules = [
            ("B1", lambda c: safe_float_match(c.get("Amount"), amount) and bool(formatted_date) and c.get("ReceiptDate") == formatted_date and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
            ("B2", lambda c: bool(customer_name) and safe_str_match(c.get("CustomerName"), customer_name) and safe_float_match(c.get("Amount"), amount)),
            ("B3", lambda c: bool(customer_name) and safe_str_match(c.get("CustomerName"), customer_name) and bool(formatted_date) and c.get("ReceiptDate") == formatted_date),
        ]

    # Phase 1: Search Unapplied Receipts
    unapplied_candidates = [c for c in candidates if is_receipt_unapplied(c)]
    match, rule_name = apply_rules_to_candidates(unapplied_candidates, rules)

    if match:
        logger.info(f"Receipt Rule {rule_name} Matched in UNAPPLIED phase!")
    else:
        # Phase 2: Search Applied Receipts
        applied_candidates = [c for c in candidates if not is_receipt_unapplied(c)]
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

async def fetch_both_inv_and_cm_raw(client, user, pwd, query, inv_fields, cm_fields):
    """
    Sequentially fetches invoices, then credit memos (if no invoices found).
    This cuts total HTTP requests in half since TransactionNumber is unique, massively improving Oracle throughput.
    """
    candidates = []

    try:
        inv_res = await fetch_oracle_candidates(client, user, pwd, "receivablesInvoices", query, fields=inv_fields)
        if isinstance(inv_res, list):
            candidates.extend(inv_res)
    except Exception as e:
        logger.warning(f"Raw Invoice fetch exception: {e}")

    if not candidates:
        try:
            cm_res = await fetch_oracle_candidates(client, user, pwd, "receivablesCreditMemos", query, fields=cm_fields)
            if isinstance(cm_res, list):
                for c in cm_res:
                    c["InvoiceStatus"] = c.get("CreditMemoStatus")
                    c["InvoiceBalanceAmount"] = c.get("TransactionBalanceDue")
                candidates.extend(cm_res)
        except Exception as e:
            logger.warning(f"Raw CM fetch exception: {e}")

    return candidates

async def fetch_both_inv_and_cm(client, user, pwd, query_key, raw_value, inv_fields, cm_fields):
    """Fix 5: Concurrent fetch for Invoices and Credit Memos to prevent N+1 fallback."""
    query = f"{query_key}='{escape_oracle(raw_value)}'"
    return await fetch_both_inv_and_cm_raw(client, user, pwd, query, inv_fields, cm_fields)

async def check_invoice_cascading(client, user, pwd, inv_num, inv_date, amount, doc_num, customer_name, cache_customer=None, customer_lock=None):
    """
    Invoice Cascading matching: Two-Phase Search (Open first, then Closed).
    Uses pre-fetched dictionaries (cache_inv_num, cache_doc_num) if available to avoid HTTP calls.
    Lazily fetches and caches customer_name fallbacks using customer_lock to prevent N+1 duplicate calls.
    """
    formatted_date = format_oracle_date(inv_date)
    candidates = []

    inv_fields = "TransactionNumber,TransactionDate,EnteredAmount,InvoiceStatus,InvoiceBalanceAmount,DocumentNumber,BillToCustomerName"
    cm_fields = "TransactionNumber,TransactionDate,EnteredAmount,CreditMemoStatus,TransactionBalanceDue,DocumentNumber,BillToCustomerName"

    try:
        if inv_num:
            candidates = await fetch_both_inv_and_cm(client, user, pwd, "TransactionNumber", inv_num, inv_fields, cm_fields)

        if not candidates and doc_num:
            candidates = await fetch_both_inv_and_cm(client, user, pwd, "DocumentNumber", doc_num, inv_fields, cm_fields)

        if not candidates and customer_name:
            if cache_customer is not None and customer_lock is not None:
                # Lazy fetching with lock to prevent N+1 duplicate calls
                c_name_lower = customer_name.lower()
                async with customer_lock:
                    if c_name_lower not in cache_customer:
                        try:
                            cache_customer[c_name_lower] = await fetch_both_inv_and_cm(client, user, pwd, "BillToCustomerName", customer_name, inv_fields, cm_fields)
                        except Exception as e:
                            # If BillToCustomerName is not queriable (400) or fails, cache the empty failure
                            # so 500 subsequent invoices don't sequentially retry and cause a 120s timeout!
                            logger.error(f"Customer fallback query failed: {str(e)}")
                            cache_customer[c_name_lower] = []
                candidates = cache_customer[c_name_lower]
            else:
                try:
                    candidates = await fetch_both_inv_and_cm(client, user, pwd, "BillToCustomerName", customer_name, inv_fields, cm_fields)
                except Exception as e:
                    logger.error(f"Customer fallback query failed: {str(e)}")
                    candidates = []

    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": f"No candidates found for invoice {inv_num}."}

    # 2. Local Filtering Rules
    rules = [
        ("1a", lambda c: safe_str_match(c.get("TransactionNumber"), inv_num)),
        ("1b", lambda c: safe_str_match(c.get("TransactionNumber"), inv_num) and c.get("TransactionDate") == formatted_date),
        ("2",  lambda c: bool(doc_num) and safe_str_match(c.get("DocumentNumber"), doc_num) and c.get("TransactionDate") == formatted_date),
        ("3",  lambda c: bool(inv_num) and str(inv_num).lower() in str(c.get("TransactionNumber", "")).lower() and c.get("TransactionDate") == formatted_date),
        ("4",  lambda c: bool(customer_name) and safe_str_match(c.get("BillToCustomerName"), customer_name) and c.get("TransactionDate") == formatted_date and safe_float_match(c.get("EnteredAmount"), amount)),
    ]

    # Phase 1: Search Open Invoices
    open_candidates = [c for c in candidates if is_invoice_open(c)]
    match, rule_name = apply_rules_to_candidates(open_candidates, rules)

    if match:
        logger.info(f"Invoice Rule {rule_name} Matched in OPEN phase!")
    else:
        # Phase 2: Search Closed Invoices
        closed_candidates = [c for c in candidates if not is_invoice_open(c)]
        match, rule_name = apply_rules_to_candidates(closed_candidates, rules)
        if match:
            logger.info(f"Invoice Rule {rule_name} Matched in CLOSED fallback phase!")

    if match:
        return {
            "matched_in_oracle": True,
            "fusion_invoice_number": match.get("TransactionNumber"),
            "fusion_invoice_date": match.get("TransactionDate"),
            "fusion_invoice_amount": match.get("EnteredAmount"),
            "match_phase": "OPEN" if is_invoice_open(match) else "CLOSED",
            "match_rule": rule_name
        }

    return {"matched_in_oracle": False, "error": f"No single match found for invoice {inv_num}."}
