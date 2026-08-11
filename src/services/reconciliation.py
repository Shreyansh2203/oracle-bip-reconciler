import asyncio
import logging
import os
import time
import uuid
from typing import Any

import httpx
import Levenshtein

from src.models import ReconciliationRequest
from src.services.discovery import _filter_data_rows, discover_potential_customers
from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts
from src.utils.date_formatter import format_oracle_date
from src.utils.validators import sanitize_float_val

logger = logging.getLogger("reconciliation_api")


def _is_date_equal(date1: Any, date2: Any) -> bool:
    """Compare two dates after normalizing both to YYYY-MM-DD."""
    n1 = format_oracle_date(date1)
    n2 = format_oracle_date(date2)
    if n1 is None or n2 is None:
        # Fallback: raw string comparison (case-insensitive)
        return str(date1).strip().lower() == str(date2).strip().lower()
    return n1 == n2


def _is_amount_equal(amt1: Any, amt2: Any) -> bool:
    if amt1 is None or amt2 is None:
        return False
    try:
        return sanitize_float_val(amt1) == sanitize_float_val(amt2)
    except Exception:
        return False


def _is_num_ok(inv_num: str, o_num: str) -> bool:
    if not inv_num or not o_num:
        return False
    if inv_num == o_num:
        return True

    # Substring check for OCR truncation (enforce min length on both to prevent false positives)
    if len(inv_num) >= 5 and len(o_num) >= 5 and (inv_num in o_num or o_num in inv_num):
        return True

    # Fuzzy matching using Levenshtein distance for OCR typos
    # Allow 1 typo for strings up to 6 chars, 2 typos for longer
    if len(inv_num) >= 5:
        max_dist = 1 if len(inv_num) <= 6 else 2
        if Levenshtein.distance(inv_num, o_num) <= max_dist:
            return True

    return False


def map_ledger_to_payload(
    payload: ReconciliationRequest,
    customer_name: str,
    all_receipts_raw: list[dict[str, Any]],
    all_invoices_raw: list[dict[str, Any]],
) -> None:
    # ── STEP 3: Map Receipt ──
    def _apply_receipt_mapping(r: dict[str, Any]) -> None:
        payload.fusion_receipt_number = r.get("RECEIPT_NUMBER")
        payload.fusion_receipt_date = r.get("RECEIPT_DATE")
        payload.fusion_applied_amount = sanitize_float_val(r.get("RECEIPT_AMOUNT")) if r.get("RECEIPT_AMOUNT") else None
        payload.fusion_currency = r.get("CURRENCY")
        payload.fusion_receipt_status_code = r.get("RECEIPT_STATUS_CODE")
        payload.fusion_customer_number = r.get("BILL_CUSTOMER_NUMBER")

        if not payload.payment_reference:
            payload.payment_reference = payload.fusion_receipt_number
        if not payload.payment_date:
            payload.payment_date = payload.fusion_receipt_date
        if payload.total_amount is None:
            payload.total_amount = payload.fusion_applied_amount
        if not payload.customer_name:
            payload.customer_name = r.get("BILL_CUSTOMER_NAME") or customer_name

    receipt_number = str(payload.payment_reference).strip() if payload.payment_reference else ""
    if receipt_number:
        for r in all_receipts_raw:
            cand_num = str(r.get("RECEIPT_NUMBER", "")).strip()
            if cand_num and (receipt_number.lower() in cand_num.lower() or cand_num.lower() in receipt_number.lower()):
                _apply_receipt_mapping(r)
                break
    else:
        total_amt = payload.total_amount
        pay_date = payload.payment_date
        if total_amt is not None and pay_date:
            for r in all_receipts_raw:
                r_amt = r.get("RECEIPT_AMOUNT")
                r_date = r.get("RECEIPT_DATE")
                if _is_amount_equal(total_amt, r_amt) and _is_date_equal(pay_date, r_date):
                    _apply_receipt_mapping(r)
                    break

    # ── STEP 4: Map Invoices (Tiered Matching) ──
    mapped_oracle_invoices: set[str] = set()

    def _apply_invoice_mapping(inv_item: Any, o_inv: dict[str, Any]) -> None:
        inv_item.fusion_invoice_number = o_inv.get("TRANSACTION_NUMBER") or o_inv.get("INVOICE_NUMBER")
        inv_item.fusion_invoice_date = o_inv.get("TRANSACTION_DATE") or o_inv.get("INVOICE_DATE")
        inv_item.fusion_invoice_amount = o_inv.get("TRANSACTION_TOTAL") or o_inv.get("TOTAL_AMOUNTS") or o_inv.get("INVOICE_AMOUNT")
        inv_item.match_phase = "MATCHED"

        inv_item.invoice_number = inv_item.fusion_invoice_number
        inv_item.invoice_date = inv_item.fusion_invoice_date
        if inv_item.fusion_invoice_amount is not None:
            inv_item.invoice_amount = sanitize_float_val(inv_item.fusion_invoice_amount)

        mapped_oracle_invoices.add(str(inv_item.fusion_invoice_number))

    # Pre-index Oracle invoices
    inv_by_num: dict[str, list[dict[str, Any]]] = {}

    for o_inv in all_invoices_raw:
        o_num = str(o_inv.get("TRANSACTION_NUMBER") or o_inv.get("INVOICE_NUMBER", ""))
        if o_num not in inv_by_num:
            inv_by_num[o_num] = []
        inv_by_num[o_num].append(o_inv)

    for invoice in payload.invoices:
        inv_num = str(invoice.invoice_number).strip() if invoice.invoice_number else ""
        inv_date = str(invoice.invoice_date).strip() if invoice.invoice_date else ""
        inv_amt = invoice.invoice_amount

        matched_o_inv = None

        # 1. Dictionary-based lookup for EXACT number matches
        if inv_num in inv_by_num:
            candidates = [o for o in inv_by_num[inv_num] if str(o.get("TRANSACTION_NUMBER") or o.get("INVOICE_NUMBER")) not in mapped_oracle_invoices]

            # Exact 3-Way Match
            for o_inv in candidates:
                o_date = str(o_inv.get("TRANSACTION_DATE") or o_inv.get("INVOICE_DATE", ""))
                o_amt = o_inv.get("TRANSACTION_TOTAL") or o_inv.get("TOTAL_AMOUNTS") or o_inv.get("INVOICE_AMOUNT")
                if _is_date_equal(inv_date, o_date) and _is_amount_equal(inv_amt, o_amt):
                    matched_o_inv = o_inv
                    break

            # 2-Way Match Fallbacks (Num + Amt, Num + Date)
            if not matched_o_inv:
                for o_inv in candidates:
                    o_date = str(o_inv.get("TRANSACTION_DATE") or o_inv.get("INVOICE_DATE", ""))
                    o_amt = o_inv.get("TRANSACTION_TOTAL") or o_inv.get("TOTAL_AMOUNTS") or o_inv.get("INVOICE_AMOUNT")
                    if _is_amount_equal(inv_amt, o_amt) or _is_date_equal(inv_date, o_date):
                        matched_o_inv = o_inv
                        break

            # 1-Way Match Fallback (Num Exact)
            if not matched_o_inv and len(candidates) == 1:
                matched_o_inv = candidates[0]

        # 2. Fuzzy Matching Fallback (if exact num failed)
        if not matched_o_inv:
            available_o_invoices = [o for o in all_invoices_raw if str(o.get("TRANSACTION_NUMBER") or o.get("INVOICE_NUMBER")) not in mapped_oracle_invoices]

            matches_date_amt = []
            matches_amt = []
            matches_date = []
            matches_fuzzy_num = []

            for o_inv in available_o_invoices:
                o_num = str(o_inv.get("TRANSACTION_NUMBER") or o_inv.get("INVOICE_NUMBER", ""))
                o_date = str(o_inv.get("TRANSACTION_DATE") or o_inv.get("INVOICE_DATE", ""))
                o_amt = o_inv.get("TRANSACTION_TOTAL") or o_inv.get("TOTAL_AMOUNTS") or o_inv.get("INVOICE_AMOUNT")

                date_ok = _is_date_equal(inv_date, o_date)
                amt_ok = _is_amount_equal(inv_amt, o_amt)

                if date_ok and amt_ok:
                    matches_date_amt.append(o_inv)

                # Expensive fuzzy check only if needed
                if _is_num_ok(inv_num, o_num):
                    if amt_ok or date_ok:
                        matches_fuzzy_num.insert(0, o_inv) # Priority
                    else:
                        matches_fuzzy_num.append(o_inv)

                if amt_ok:
                    matches_amt.append(o_inv)
                if date_ok:
                    matches_date.append(o_inv)

            if matches_date_amt:
                matched_o_inv = matches_date_amt[0]
            elif matches_fuzzy_num:
                matched_o_inv = matches_fuzzy_num[0]
            elif len(matches_amt) == 1:
                matched_o_inv = matches_amt[0]
            elif len(matches_date) == 1:
                matched_o_inv = matches_date[0]

        if matched_o_inv:
            _apply_invoice_mapping(invoice, matched_o_inv)
        else:
            invoice.match_phase = "UNMATCHED"


async def process_reconciliation_batch(payload: ReconciliationRequest, client: httpx.AsyncClient) -> tuple[ReconciliationRequest | None, str | None, int | None]:
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting RECONCILIATION for payload")
    start_time = time.time()

    user = os.getenv("ORACLE_USER", "")
    pwd = os.getenv("ORACLE_PASS", "")

    try:
        customer_name, cached_r_res = await discover_potential_customers(client, user, pwd, payload)
    except Exception as e:
        logger.error(f"[{request_id}] Oracle fetch failed: {e}")
        return None, f"Oracle API returned an error or timed out while fetching records: {e}", 502

    if not customer_name:
        logger.warning(f"[{request_id}] Unable to determine customer name. Returning null.")
        return None, None, None

    payload.fusion_customer_name = customer_name

    logger.info(f"[{request_id}] Fetching ledger to map columns for '{customer_name}'")

    i_task = fetch_bip_invoices(client, user, pwd, customer_name=customer_name)

    if cached_r_res is not None:
        logger.info(f"[{request_id}] Using cached Receipt Report from Step 2 discovery.")
        r_raw = cached_r_res
        i_raw = await i_task
    else:
        r_task = fetch_bip_receipts(client, user, pwd, customer_name=customer_name)
        i_raw, r_raw = await asyncio.gather(i_task, r_task, return_exceptions=True)

    if isinstance(i_raw, BaseException) or isinstance(r_raw, BaseException):
        err = i_raw if isinstance(i_raw, BaseException) else r_raw
        logger.error(f"[{request_id}] Oracle fetch failed during ledger fetch: {err}")
        return None, f"Oracle API error during ledger fetch: {err}", 502

    all_invoices_raw = _filter_data_rows(i_raw)
    all_receipts_raw = _filter_data_rows(r_raw)

    map_ledger_to_payload(payload, customer_name, all_receipts_raw, all_invoices_raw)

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] RECON COMPLETE: Customer='{customer_name}'. Returning mapped payload in {duration}ms.")
    return payload, None, None
