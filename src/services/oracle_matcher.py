from __future__ import annotations
import asyncio
import logging
import math
from typing import Any
import httpx
from src.config import get_oracle_url
from src.utils.date_formatter import format_oracle_date

logger = logging.getLogger(__name__)

def safe_float_match(val1, val2) -> bool:
    try:
        if val1 is None or val2 is None:
            return False
        return round(float(val1) * 100) == round(float(val2) * 100)
    except (ValueError, TypeError):
        return False

def safe_str_match(val1, val2):
    if val1 is None or val2 is None:
        return False
    return str(val1).strip().lower() == str(val2).strip().lower()

# =========================================================================
# IN-MEMORY BATCH MATCHING (APPROACH 1)
# =========================================================================

def match_receipt_in_memory(receipt_num: str, amount: float | None, receipt_date: str, customer_name: str, bip_receipts: list[dict[str, Any]]) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not bip_receipts:
        return {"matched_in_oracle": False, "error": "No receipt matches returned from Oracle Batch."}

    match = bip_receipts[0]
    status_code = match.get("RECEIPT_STATUS_CODE", "")
    phase = "UNAPPLIED" if status_code in ["UNAPP", "UNID"] else "APPLIED"

    return {
        "matched_in_oracle": True,
        "fusion_receipt_number": match.get("RECEIPT_NUMBER"),
        "fusion_receipt_date": match.get("RECEIPT_DATE"),
        "fusion_customer_name": match.get("BILL_CUSTOMER_NAME"),
        "match_phase": phase,
        "match_rule": "bip_batch_match"
    }

def match_invoice_in_memory(invoice_number: str, inv_date: str, amount: float | None, document_number: str, customer_name: str, bip_invoices: list[dict[str, Any]]) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not bip_invoices:
        return {"matched_in_oracle": False, "error": "No customer invoices returned from Oracle Batch."}

    formatted_date = format_oracle_date(inv_date) if inv_date else None
    
    exact_matches = [i for i in bip_invoices if safe_str_match(i.get("TRANSACTION_NUMBER"), invoice_number)]
    if exact_matches:
        open_exact = [i for i in exact_matches if i.get("INVOICE_STATUS") == "OPEN"]
        match = open_exact[0] if open_exact else exact_matches[0]
        return _build_invoice_response(match, "exact_number_match")
    
    if amount is not None and inv_date:
        amount_date_matches = [
            i for i in bip_invoices 
            if safe_float_match(i.get("TOTAL_AMOUNTS"), amount) 
            and i.get("TRANSACTION_DATE") == inv_date
        ]
        if amount_date_matches:
            open_matches = [i for i in amount_date_matches if i.get("INVOICE_STATUS") == "OPEN"]
            match = open_matches[0] if open_matches else amount_date_matches[0]
            return _build_invoice_response(match, "amount_and_date_fallback")

    return {"matched_in_oracle": False, "error": f"No match found in bulk data for {invoice_number}."}

def _build_invoice_response(match, rule_name):
    amt_str = match.get("TRANSACTION_TOTAL", "")
    parsed_amt = None
    if amt_str:
        try:
            parsed_amt = float(amt_str.replace(",", ""))
        except ValueError:
            pass

    return {
        "matched_in_oracle": True,
        "fusion_invoice_number": match.get("TRANSACTION_NUMBER"),
        "fusion_invoice_date": match.get("TRANSACTION_DATE"),
        "fusion_invoice_amount": parsed_amt,
        "match_phase": match.get("INVOICE_STATUS", "OTHER"),
        "match_rule": rule_name
    }

# =========================================================================
# LEGACY NATIVE REST API MATCHING (APPROACH 3)
# =========================================================================

async def fetch_oracle_candidates_native(client, user, pwd, endpoint, query, limit=None, fields=None):
    base_url = get_oracle_url()
    url = f"{base_url}/fscmRestApi/resources/11.13.18.05/{endpoint}"
    params = {"q": query}
    if limit: params["limit"] = limit
    if fields: params["fields"] = fields
    try:
        response = await client.get(url, params=params, auth=(user, pwd))
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        logger.error(f"Native Oracle fetch exception: {e}")
        raise e

async def check_receipt_cascading_native(client, user, pwd, receipt_num, amount, receipt_date, customer_name, sem=None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    formatted_date = format_oracle_date(receipt_date)
    fields = "ReceiptNumber,ReceiptDate,Amount,CustomerName,Status"
    candidates = []

    try:
        if receipt_num:
            query = f"ReceiptNumber='{receipt_num}'"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "standardReceipts", query, fields=fields)

        if not candidates and amount is not None and formatted_date:
            query = f"Amount={float(amount):.2f} and ReceiptDate='{formatted_date}'"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "standardReceipts", query, fields=fields)
    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": "No single match found native."}

    match = candidates[0]
    status_code = match.get("Status", "")
    phase = "UNAPPLIED" if status_code in ["Unapplied", "Unidentified"] else "APPLIED"

    return {
        "matched_in_oracle": True,
        "fusion_receipt_number": match.get("ReceiptNumber"),
        "fusion_receipt_date": match.get("ReceiptDate"),
        "fusion_customer_name": match.get("CustomerName"),
        "match_phase": phase,
        "match_rule": "native_rest_match"
    }

async def check_invoice_cascading_native(client, user, pwd, inv_num, inv_date, amount, doc_num, customer_name, sem=None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    formatted_date = format_oracle_date(inv_date)
    candidates = []
    inv_fields = "TransactionNumber,TransactionDate,EnteredAmount,InvoiceStatus,InvoiceBalanceAmount,DocumentNumber,BillToCustomerName"

    try:
        if inv_num:
            query = f"TransactionNumber='{inv_num}'"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "receivablesInvoices", query, fields=inv_fields)
        if not candidates and customer_name and amount is not None:
            query = f"BillToCustomerName='{customer_name}' and EnteredAmount={float(amount):.2f}"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "receivablesInvoices", query, fields=inv_fields)
    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": f"No single match found native for {inv_num}."}

    match = candidates[0]
    return {
        "matched_in_oracle": True,
        "fusion_invoice_number": match.get("TransactionNumber"),
        "fusion_invoice_date": match.get("TransactionDate"),
        "fusion_invoice_amount": match.get("EnteredAmount"),
        "match_phase": "OPEN" if float(match.get("InvoiceBalanceAmount", 0)) > 0 else "CLOSED",
        "match_rule": "native_rest_match"
    }
