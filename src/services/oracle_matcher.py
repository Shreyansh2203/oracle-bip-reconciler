import logging
import urllib.parse
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

from src.utils.date_formatter import format_oracle_date

logger = logging.getLogger(__name__)
ORACLE_URL = "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com"

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
    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": "No candidates found in Oracle for ReceiptNumber or CustomerName."}

    # 2. Local Filtering Rules
    rules = [
        ("A1", lambda c: safe_str_match(c.get("ReceiptNumber"), receipt_num) and safe_float_match(c.get("Amount"), amount) and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
        ("A2", lambda c: safe_str_match(c.get("ReceiptNumber"), receipt_num) and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
        ("A3", lambda c: safe_str_match(c.get("ReceiptNumber"), receipt_num) and safe_float_match(c.get("Amount"), amount) and c.get("ReceiptDate") == formatted_date and (safe_str_match(c.get("CustomerName"), customer_name) if customer_name else True)),
        ("A4", lambda c: bool(customer_name) and safe_str_match(c.get("CustomerName"), customer_name) and safe_float_match(c.get("Amount"), amount)),
        ("A5", lambda c: bool(customer_name) and safe_str_match(c.get("CustomerName"), customer_name) and bool(formatted_date) and c.get("ReceiptDate") == formatted_date),
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
    """Raw version of fetch_both_inv_and_cm that accepts a full custom query string."""
    inv_task = fetch_oracle_candidates(client, user, pwd, "receivablesInvoices", query, fields=inv_fields)
    cm_task = fetch_oracle_candidates(client, user, pwd, "receivablesCreditMemos", query, fields=cm_fields)
    
    inv_res, cm_res = await asyncio.gather(inv_task, cm_task, return_exceptions=True)
    
    candidates = []
    if isinstance(inv_res, list):
        candidates.extend(inv_res)
    if isinstance(cm_res, list):
        for c in cm_res:
            c["InvoiceStatus"] = c.get("CreditMemoStatus")
            c["InvoiceBalanceAmount"] = c.get("TransactionBalanceDue")
        candidates.extend(cm_res)
        
    return candidates

async def fetch_both_inv_and_cm(client, user, pwd, query_key, raw_value, inv_fields, cm_fields):
    """Fix 5: Concurrent fetch for Invoices and Credit Memos to prevent N+1 fallback."""
    query = f"{query_key}='{escape_oracle(raw_value)}'"
    return await fetch_both_inv_and_cm_raw(client, user, pwd, query, inv_fields, cm_fields)

async def prefetch_candidates_in_bulk(client, user, pwd, query_key, values, inv_fields, cm_fields, chunk_size=40):
    """
    Groups values into massive OR queries and fetches all matching candidates concurrently.
    Returns a dictionary mapping the lowercase query_key value to a list of candidates.
    """
    candidates_dict = {}
    
    unique_vals = list(set([str(v).strip() for v in values if v and str(v).strip() != "None"]))
    if not unique_vals:
        return candidates_dict
        
    tasks = []
    for i in range(0, len(unique_vals), chunk_size):
        chunk = unique_vals[i:i+chunk_size]
        query_parts = [f"{query_key}='{escape_oracle(v)}'" for v in chunk]
        query = " OR ".join(query_parts)
        tasks.append(fetch_both_inv_and_cm_raw(client, user, pwd, query, inv_fields, cm_fields))
        
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for res_list in results:
        if isinstance(res_list, list):
            for c in res_list:
                key_val = str(c.get(query_key, "")).strip().lower()
                if key_val not in candidates_dict:
                    candidates_dict[key_val] = []
                candidates_dict[key_val].append(c)
            
    return candidates_dict

async def check_invoice_cascading(client, user, pwd, inv_num, inv_date, amount, doc_num, customer_name, cache_inv_num=None, cache_doc_num=None):
    """
    Invoice Cascading matching: Two-Phase Search (Open first, then Closed).
    Uses pre-fetched dictionaries (cache_inv_num, cache_doc_num) if available to avoid HTTP calls.
    """
    formatted_date = format_oracle_date(inv_date)
    candidates = []

    inv_fields = "TransactionNumber,TransactionDate,EnteredAmount,InvoiceStatus,InvoiceBalanceAmount,DocumentNumber,BillToCustomerName"
    cm_fields = "TransactionNumber,TransactionDate,EnteredAmount,CreditMemoStatus,TransactionBalanceDue,DocumentNumber,BillToCustomerName"
    
    try:
        if inv_num:
            if cache_inv_num is not None:
                candidates = cache_inv_num.get(inv_num.lower(), [])
            else:
                candidates = await fetch_both_inv_and_cm(client, user, pwd, "TransactionNumber", inv_num, inv_fields, cm_fields)
            
        if not candidates and doc_num:
            if cache_doc_num is not None:
                candidates = cache_doc_num.get(doc_num.lower(), [])
            else:
                candidates = await fetch_both_inv_and_cm(client, user, pwd, "DocumentNumber", doc_num, inv_fields, cm_fields)
            
        if not candidates and customer_name:
            candidates = await fetch_both_inv_and_cm(client, user, pwd, "BillToCustomerName", customer_name, inv_fields, cm_fields)
            
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
