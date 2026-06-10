import logging
import urllib.parse
from src.utils.date_formatter import format_oracle_date

logger = logging.getLogger(__name__)
ORACLE_URL = "https://fa-epxp-test-saasfaprod1.fa.ocs.oraclecloud.com"

async def fetch_oracle_candidates(client, user, pwd, endpoint, query, limit=200):
    """
    Fetch candidates from Oracle using only indexable fields.
    """
    try:
        q = urllib.parse.quote(query)
        url = f"{ORACLE_URL}/fscmRestApi/resources/11.13.18.05/{endpoint}?q={q}&limit={limit}"
        response = await client.get(url, auth=(user, pwd))
        if response.status_code == 200:
            return response.json().get("items", [])
        else:
            logger.error(f"Oracle fetch error ({response.status_code}): {response.text}")
            return []
    except Exception as e:
        logger.error(f"Oracle fetch exception: {e}")
        return []

def safe_float_match(val1, val2, tolerance=0.01):
    try:
        if val1 is None or val2 is None: return False
        return abs(float(val1) - float(val2)) < tolerance
    except ValueError:
        return False

def safe_str_match(val1, val2):
    if not val1 or not val2: return False
    return str(val1).strip().lower() == str(val2).strip().lower()

def is_receipt_unapplied(c):
    state = str(c.get("State", "")).strip().lower()
    return state in ["unapplied", "unapp"]

def is_invoice_open(c):
    status = str(c.get("InvoiceStatus", "")).strip().lower()
    if status == "closed":
        return False
    
    # Try to parse InvoiceBalanceAmount if available
    bal = c.get("InvoiceBalanceAmount")
    if bal is not None:
        try:
            return float(bal) > 0
        except ValueError:
            pass
            
    # Default to Open if not explicitly closed
    return True

def apply_rules_to_candidates(candidates, rules):
    for rule_name, condition in rules:
        matches = [c for c in candidates if condition(c)]
        if len(matches) == 1:
            return matches[0], rule_name
    return None, None

async def check_receipt_cascading(client, user, pwd, receipt_num, amount, receipt_date, customer_name):
    """
    Receipt Cascading matching: Two-Phase Search (Unapplied first, then Applied).
    """
    formatted_date = format_oracle_date(receipt_date)
    candidates = []
    
    # 1. Fetch Candidates (Bypass Oracle's 400 Bad Request on Amount/Date)
    if receipt_num:
        candidates = await fetch_oracle_candidates(client, user, pwd, "standardReceipts", f"ReceiptNumber='{receipt_num}'")
    
    if not candidates and customer_name:
        candidates = await fetch_oracle_candidates(client, user, pwd, "standardReceipts", f"CustomerName='{customer_name}'")
        
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

async def check_invoice_cascading(client, user, pwd, inv_num, inv_date, amount, doc_num, customer_name):
    """
    Invoice Cascading matching: Two-Phase Search (Open first, then Closed).
    """
    formatted_date = format_oracle_date(inv_date)
    candidates = []
    
    # 1. Fetch Candidates
    if inv_num:
        candidates = await fetch_oracle_candidates(client, user, pwd, "receivablesInvoices", f"TrxNumber='{inv_num}'")
        
    if not candidates and doc_num:
        candidates = await fetch_oracle_candidates(client, user, pwd, "receivablesInvoices", f"CustomerReference='{doc_num}'")
        
    if not candidates and customer_name:
        candidates = await fetch_oracle_candidates(client, user, pwd, "receivablesInvoices", f"BillToCustomerName='{customer_name}'")
        
    if not candidates:
        return {"matched_in_oracle": False, "error": f"No candidates found for invoice {inv_num}."}
        
    # 2. Local Filtering Rules
    rules = [
        ("1a", lambda c: safe_str_match(c.get("TrxNumber"), inv_num)),
        ("1b", lambda c: safe_str_match(c.get("TrxNumber"), inv_num) and c.get("TrxDate") == formatted_date),
        ("2",  lambda c: bool(doc_num) and safe_str_match(c.get("CustomerReference"), doc_num) and c.get("TrxDate") == formatted_date),
        ("3",  lambda c: bool(inv_num) and str(inv_num).lower() in str(c.get("TrxNumber", "")).lower() and c.get("TrxDate") == formatted_date),
        ("4",  lambda c: bool(customer_name) and safe_str_match(c.get("BillToCustomerName"), customer_name) and c.get("TrxDate") == formatted_date and safe_float_match(c.get("InvoiceAmount"), amount)),
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
            "fusion_invoice_number": match.get("TrxNumber"),
            "fusion_invoice_date": match.get("TrxDate"),
            "fusion_invoice_amount": match.get("InvoiceAmount"),
            "match_phase": "OPEN" if is_invoice_open(match) else "CLOSED",
            "match_rule": rule_name
        }
            
    return {"matched_in_oracle": False, "error": f"No single match found for invoice {inv_num}."}
