from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config import get_oracle_url
from src.utils.date_formatter import format_oracle_date

logger = logging.getLogger(__name__)

def safe_float_match(val1, val2) -> bool:
    try:
        if val1 is None or val2 is None:
            return False
        v1_str = str(val1).replace(",", "").strip()
        v2_str = str(val2).replace(",", "").strip()
        if not v1_str or not v2_str:
            return False
        d1 = Decimal(v1_str).quantize(Decimal('0.01'))
        d2 = Decimal(v2_str).quantize(Decimal('0.01'))
        return d1 == d2
    except (InvalidOperation, ValueError, TypeError):
        return False

def safe_str_match(val1, val2):
    if val1 is None or val2 is None:
        return False
    return str(val1).strip().lower() == str(val2).strip().lower()

def safe_starts_with(full_val, prefix_val):
    if full_val is None or prefix_val is None:
        return False
    return str(full_val).strip().lower().startswith(str(prefix_val).strip().lower())

def escape_query_value(val: Any) -> str:
    """Escapes single quotes for Oracle REST query strings."""
    if val is None:
        return ""
    return str(val).replace("'", "''")

def get_receipt_phase(status_code: str) -> str:
    return "UNAPPLIED" if status_code in ["UNAPP", "UNID", "Unapplied", "Unidentified"] else "APPLIED"

def get_invoice_phase(status_code: str, balance: Any) -> str:
    if status_code:
        return status_code.upper()
    try:
        if float(balance) > 0:
            return "OPEN"
        return "CLOSED"
    except (ValueError, TypeError):
        return "OTHER"

# =========================================================================
# IN-MEMORY BATCH MATCHING (APPROACH 1)
# =========================================================================

def match_receipt_in_memory(receipt_num: str, amount: float | None, receipt_date: str, customer_name: str, bip_receipts: list[dict[str, Any]]) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not bip_receipts:
        return {"matched_in_oracle": False, "error": "No receipt matches returned from Oracle Batch."}

    formatted_date = format_oracle_date(receipt_date) if receipt_date else None

    # Phase 1: Unapplied/Unidentified, Phase 2: Applied
    for phase_num in [1, 2]:
        candidates = [
            c for c in bip_receipts
            if (phase_num == 1 and get_receipt_phase(c.get("RECEIPT_STATUS_CODE", "")) == "UNAPPLIED") or
               (phase_num == 2 and get_receipt_phase(c.get("RECEIPT_STATUS_CODE", "")) == "APPLIED")
        ]

        if not candidates:
            continue

        if receipt_num:
            # Scenario A
            # A1: Num, Amount, Date, [Customer]
            res = [c for c in candidates if safe_str_match(c.get("RECEIPT_NUMBER"), receipt_num)
                   and (amount is None or safe_float_match(c.get("RECEIPT_AMOUNT"), amount))
                   and safe_str_match(c.get("RECEIPT_DATE"), formatted_date)
                   and (not customer_name or safe_str_match(c.get("BILL_CUSTOMER_NAME"), customer_name))]
            if len(res) == 1: return _build_receipt_response(res[0], "A1")

            # A2: Num, Amount, [Customer]
            res = [c for c in candidates if safe_str_match(c.get("RECEIPT_NUMBER"), receipt_num)
                   and (amount is None or safe_float_match(c.get("RECEIPT_AMOUNT"), amount))
                   and (not customer_name or safe_str_match(c.get("BILL_CUSTOMER_NAME"), customer_name))]
            if len(res) == 1: return _build_receipt_response(res[0], "A2")

            # A3: Num, [Customer]
            res = [c for c in candidates if safe_str_match(c.get("RECEIPT_NUMBER"), receipt_num)
                   and (not customer_name or safe_str_match(c.get("BILL_CUSTOMER_NAME"), customer_name))]
            if len(res) == 1: return _build_receipt_response(res[0], "A3")

            # A4: Customer, Amount, Date
            if customer_name and amount is not None and formatted_date:
                res = [c for c in candidates if safe_str_match(c.get("BILL_CUSTOMER_NAME"), customer_name)
                       and safe_float_match(c.get("RECEIPT_AMOUNT"), amount)
                       and safe_str_match(c.get("RECEIPT_DATE"), formatted_date)]
                if len(res) == 1: return _build_receipt_response(res[0], "A4")
        else:
            # Scenario B
            # B1: Amount, Date, [Customer]
            if amount is not None and formatted_date:
                res = [c for c in candidates if safe_float_match(c.get("RECEIPT_AMOUNT"), amount)
                       and safe_str_match(c.get("RECEIPT_DATE"), formatted_date)
                       and (not customer_name or safe_str_match(c.get("BILL_CUSTOMER_NAME"), customer_name))]
                if len(res) == 1: return _build_receipt_response(res[0], "B1")

    return {"matched_in_oracle": False, "error": "No single match found after cascading rules"}

def _build_receipt_response(match, rule_name):
    return {
        "matched_in_oracle": True,
        "fusion_receipt_number": match.get("RECEIPT_NUMBER"),
        "fusion_receipt_date": match.get("RECEIPT_DATE"),
        "fusion_customer_name": match.get("BILL_CUSTOMER_NAME"),
        "match_phase": get_receipt_phase(match.get("RECEIPT_STATUS_CODE", "")),
        "match_rule": rule_name
    }


def match_invoice_in_memory(invoice_number: str, inv_date: str, amount: float | None, document_number: str, customer_name: str, bip_invoices: list[dict[str, Any]]) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not bip_invoices:
        return {"matched_in_oracle": False, "error": "No customer invoices returned from Oracle Batch."}

    formatted_date = format_oracle_date(inv_date) if inv_date else None

    # Filter base candidates by amount constraint up front (implicit constraint)
    base_candidates = [c for c in bip_invoices if amount is None or safe_float_match(c.get("TOTAL_AMOUNTS"), amount)]

    # Phase 1: OPEN, Phase 2: CLOSED/OTHER
    for phase_num in [1, 2]:
        candidates = [
            c for c in base_candidates
            if (phase_num == 1 and get_invoice_phase(c.get("INVOICE_STATUS"), 0) == "OPEN") or
               (phase_num == 2 and get_invoice_phase(c.get("INVOICE_STATUS"), 0) != "OPEN")
        ]

        if not candidates:
            continue

        # Rule 1a: Num + Date
        if invoice_number and formatted_date:
            res = [c for c in candidates if safe_str_match(c.get("TRANSACTION_NUMBER"), invoice_number)
                   and safe_str_match(c.get("TRANSACTION_DATE"), formatted_date)]
            if len(res) == 1: return _build_invoice_response(res[0], "Rule 1a")

        # Rule 1b: Exact Num
        if invoice_number:
            res = [c for c in candidates if safe_str_match(c.get("TRANSACTION_NUMBER"), invoice_number)]
            if len(res) == 1: return _build_invoice_response(res[0], "Rule 1b")

        # Rule 2: Doc Num + Date
        if document_number and formatted_date:
            res = [c for c in candidates if safe_str_match(c.get("DOCUMENT_NUMBER"), document_number)
                   and safe_str_match(c.get("TRANSACTION_DATE"), formatted_date)]
            if len(res) == 1: return _build_invoice_response(res[0], "Rule 2")

        # Rule 3: Prefix Match + Date
        if invoice_number and formatted_date:
            res = [c for c in candidates if safe_starts_with(c.get("TRANSACTION_NUMBER"), invoice_number)
                   and safe_str_match(c.get("TRANSACTION_DATE"), formatted_date)]
            if len(res) == 1: return _build_invoice_response(res[0], "Rule 3")

        # Rule 4: Customer + Date
        if customer_name and formatted_date:
            res = [c for c in candidates if safe_str_match(c.get("BILL_CUSTOMER_NAME"), customer_name)
                   and safe_str_match(c.get("TRANSACTION_DATE"), formatted_date)]
            if len(res) == 1: return _build_invoice_response(res[0], "Rule 4")

    return {"matched_in_oracle": False, "error": "No single match found after cascading rules"}

def _build_invoice_response(match, rule_name):
    amt_str = match.get("TOTAL_AMOUNTS", "")
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
        "match_phase": get_invoice_phase(match.get("INVOICE_STATUS", "OTHER"), 0),
        "match_rule": rule_name
    }


# =========================================================================
# LEGACY NATIVE REST API MATCHING (APPROACH 3)
# =========================================================================

async def fetch_oracle_candidates_native(client, user, pwd, endpoint, query, limit=None, fields=None, sem=None):
    base_url = get_oracle_url()
    url = f"{base_url}/fscmRestApi/resources/11.13.18.05/{endpoint}"
    params = {"q": query}
    if limit: params["limit"] = limit
    if fields: params["fields"] = fields

    async def _do_fetch():
        try:
            response = await client.get(url, params=params, auth=(user, pwd))
            response.raise_for_status()
            return response.json().get("items", [])
        except Exception as e:
            logger.error(f"Native Oracle fetch exception: {e}")
            raise

    if sem:
        async with sem:
            return await _do_fetch()
    else:
        return await _do_fetch()

async def check_receipt_cascading_native(client, user, pwd, receipt_num, amount, receipt_date, customer_name, sem=None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    formatted_date = format_oracle_date(receipt_date) if receipt_date else None
    fields = "ReceiptNumber,ReceiptDate,Amount,CustomerName,Status"
    candidates = []

    try:
        if receipt_num:
            query = f"ReceiptNumber='{escape_query_value(receipt_num)}'"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "standardReceipts", query, fields=fields, sem=sem)

        if not candidates and amount is not None and formatted_date:
            query = f"Amount={float(amount):.2f} and ReceiptDate='{escape_query_value(formatted_date)}'"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "standardReceipts", query, fields=fields, sem=sem)
    except Exception as e:
        return {"matched_in_oracle": False, "error": f"Oracle Fetch Error: {str(e)}"}

    if not candidates:
        return {"matched_in_oracle": False, "error": "No single match found native."}

    match = candidates[0]
    return {
        "matched_in_oracle": True,
        "fusion_receipt_number": match.get("ReceiptNumber"),
        "fusion_receipt_date": match.get("ReceiptDate"),
        "fusion_customer_name": match.get("CustomerName"),
        "match_phase": get_receipt_phase(match.get("Status", "")),
        "match_rule": "native_rest_match"
    }

async def check_invoice_cascading_native(client, user, pwd, inv_num, inv_date, amount, doc_num, customer_name, sem=None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    formatted_date = format_oracle_date(inv_date) if inv_date else None
    candidates = []
    inv_fields = "TransactionNumber,TransactionDate,EnteredAmount,InvoiceStatus,InvoiceBalanceAmount,DocumentNumber,BillToCustomerName"

    try:
        if inv_num:
            query = f"TransactionNumber='{escape_query_value(inv_num)}'"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "receivablesInvoices", query, fields=inv_fields, sem=sem)
        if not candidates and customer_name and amount is not None:
            query = f"BillToCustomerName='{escape_query_value(customer_name)}' and EnteredAmount={float(amount):.2f}"
            candidates = await fetch_oracle_candidates_native(client, user, pwd, "receivablesInvoices", query, fields=inv_fields, sem=sem)
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
        "match_phase": get_invoice_phase(match.get("InvoiceStatus", ""), match.get("InvoiceBalanceAmount", 0)),
        "match_rule": "native_rest_match"
    }
