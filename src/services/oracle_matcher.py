from __future__ import annotations
import asyncio
import logging
import math
from typing import Any
import httpx
from src.config import get_oracle_url

logger = logging.getLogger(__name__)

async def check_receipt_cascading(client: httpx.AsyncClient, user: str, password: str, receipt_num: str, amount: float | None, receipt_date: str, customer_name: str, sem: asyncio.Semaphore | None = None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    from src.services.oracle_bip import run_bip_receipt_match
    
    try:
        results = await run_bip_receipt_match(client, user, password, receipt_num, receipt_date, amount, customer_name)
    except Exception as e:
        logger.exception("Unexpected error in BIP receipt fetch")
        return {"matched_in_oracle": False, "error": f"BIP Fetch Error: {str(e)}"}

    if not results:
        return {"matched_in_oracle": False, "error": "No single match found after scoring evaluation."}

    match = results[0]
    status_code = match.get("RECEIPT_STATUS_CODE", "")
    phase = "UNAPPLIED" if status_code in ["UNAPP", "UNID"] else "APPLIED"

    return {
        "matched_in_oracle": True,
        "fusion_receipt_number": match.get("RECEIPT_NUMBER"),
        "fusion_receipt_date": match.get("RECEIPT_DATE"),
        "fusion_customer_name": match.get("BILL_CUSTOMER_NAME"),
        "match_phase": phase,
        "match_rule": "bip_sql_match"
    }

async def check_invoice_cascading(client: httpx.AsyncClient, user: str, password: str, invoice_number: str, inv_date: str, amount: float | None, document_number: str, customer_name: str, cache_customer: dict[str, list[dict[str, Any]]] | None = None, customer_lock: asyncio.Lock | None = None, sem: asyncio.Semaphore | None = None) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    from src.services.oracle_bip import run_bip_invoice_match
    
    try:
        results = await run_bip_invoice_match(client, user, password, invoice_number, inv_date, amount, customer_name)
    except Exception as e:
        logger.exception("Unexpected error in BIP invoice fetch")
        return {"matched_in_oracle": False, "error": f"BIP Fetch Error: {str(e)}"}

    if not results:
        return {"matched_in_oracle": False, "error": f"No single match found for invoice {invoice_number}."}

    match = results[0]
    
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
        "match_rule": "bip_sql_match"
    }