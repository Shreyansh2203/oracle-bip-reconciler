import asyncio
import logging
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

# Returned verbatim to the client so Oracle report paths, SQL and hostnames are never
# echoed back; the underlying exception is only ever written to the server log.
CLIENT_UPSTREAM_ERROR = "The reconciliation service is temporarily unable to reach the Oracle ERP ledger."


def _normalized_date(value: Any) -> str:
    return str(value).strip().lower()


def _is_date_equal(date1: Any, date2: Any) -> bool:
    """Compare two dates after normalizing both to YYYY-MM-DD."""
    n1 = format_oracle_date(date1)
    n2 = format_oracle_date(date2)
    if n1 is None or n2 is None:
        # Fallback: raw string comparison (case-insensitive)
        return _normalized_date(date1) == _normalized_date(date2)
    return n1 == n2


def _dates_match(
    norm1: str | None,
    raw1: str,
    norm2: str | None,
    raw2: str,
) -> bool:
    if norm1 is None or norm2 is None:
        return raw1 == raw2
    return norm1 == norm2


def _is_amount_equal(amt1: Any, amt2: Any) -> bool:
    if amt1 is None or amt2 is None:
        return False
    try:
        return sanitize_float_val(amt1) == sanitize_float_val(amt2)
    except (ValueError, TypeError):
        return False


def _is_num_ok(inv_num: str, o_num: str) -> bool:
    if not inv_num or not o_num:
        return False
    if inv_num == o_num:
        return True

    # The minimum length applies to BOTH sides: a 3-4 character Oracle number is within
    # the typo tolerance of a 5 character OCR number, which produces false positives.
    if len(inv_num) < 5 or len(o_num) < 5:
        return False

    # Substring check for OCR truncation
    if inv_num in o_num or o_num in inv_num:
        return True

    # Fuzzy matching using Levenshtein distance for OCR typos.
    # Allow 1 typo for numbers up to 6 chars, 2 typos for longer. The budget is derived
    # from the shorter of the two numbers so it is not skewed by an OCR-truncated input.
    max_dist = 1 if min(len(inv_num), len(o_num)) <= 6 else 2
    return Levenshtein.distance(inv_num, o_num) <= max_dist


class _OracleInvoice:
    """A single Oracle ledger row with its comparison keys pre-computed once."""

    __slots__ = ("number", "number_raw", "date_raw", "date_norm", "date_cmp", "amount_raw", "amount", "mapped")

    def __init__(self, raw: dict[str, Any]) -> None:
        number_raw = raw.get("TRANSACTION_NUMBER") or raw.get("INVOICE_NUMBER")
        date_raw = raw.get("TRANSACTION_DATE") or raw.get("INVOICE_DATE")
        amount_raw = raw.get("TRANSACTION_TOTAL") or raw.get("TOTAL_AMOUNTS") or raw.get("INVOICE_AMOUNT")

        self.number_raw = number_raw
        # "" (not "None") is the key used by both the lookup index and the mapped ledger,
        # so a number-less row can never be handed out twice.
        self.number = str(number_raw) if number_raw is not None else ""
        self.date_raw = date_raw
        self.date_norm = format_oracle_date(date_raw)
        self.date_cmp = _normalized_date(date_raw)
        self.amount_raw = amount_raw
        self.amount = sanitize_float_val(amount_raw) if amount_raw is not None else None
        self.mapped = False


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
    # Normalisation (date parsing, float coercion, number keying) is done once per Oracle
    # row here instead of inside the O(payload_invoices x ledger_rows) matching loop.
    ledger = [_OracleInvoice(row) for row in all_invoices_raw]
    inv_by_num: dict[str, list[_OracleInvoice]] = {}
    for entry in ledger:
        inv_by_num.setdefault(entry.number, []).append(entry)

    def _apply_invoice_mapping(inv_item: Any, o_inv: _OracleInvoice) -> None:
        inv_item.fusion_invoice_number = o_inv.number_raw
        inv_item.fusion_invoice_date = o_inv.date_raw
        inv_item.fusion_invoice_amount = o_inv.amount_raw
        inv_item.match_phase = "MATCHED"

        inv_item.invoice_number = inv_item.fusion_invoice_number
        inv_item.invoice_date = inv_item.fusion_invoice_date
        if inv_item.fusion_invoice_amount is not None:
            inv_item.invoice_amount = sanitize_float_val(inv_item.fusion_invoice_amount)

        o_inv.mapped = True

    for invoice in payload.invoices:
        inv_num = str(invoice.invoice_number).strip() if invoice.invoice_number else ""
        inv_date = str(invoice.invoice_date).strip() if invoice.invoice_date else ""
        inv_amt = invoice.invoice_amount

        inv_date_norm = format_oracle_date(inv_date)
        inv_date_cmp = _normalized_date(inv_date)
        inv_amt_cmp = sanitize_float_val(inv_amt) if inv_amt is not None else None

        matched_o_inv = None

        # 1. Dictionary-based lookup for EXACT number matches
        if inv_num in inv_by_num:
            candidates = [o for o in inv_by_num[inv_num] if not o.mapped]

            # Exact 3-Way Match
            for o_inv in candidates:
                if _dates_match(inv_date_norm, inv_date_cmp, o_inv.date_norm, o_inv.date_cmp) and inv_amt_cmp == o_inv.amount:
                    matched_o_inv = o_inv
                    break

            # 2-Way Match Fallbacks (Num + Amt, Num + Date)
            if not matched_o_inv:
                for o_inv in candidates:
                    if inv_amt_cmp == o_inv.amount or _dates_match(inv_date_norm, inv_date_cmp, o_inv.date_norm, o_inv.date_cmp):
                        matched_o_inv = o_inv
                        break

            # 1-Way Match Fallback (Num Exact)
            if not matched_o_inv and len(candidates) == 1:
                matched_o_inv = candidates[0]

        # 2. Fuzzy Matching Fallback (if exact num failed)
        if not matched_o_inv:
            available_o_invoices = [o for o in ledger if not o.mapped]

            matches_date_amt: list[_OracleInvoice] = []
            matches_amt: list[_OracleInvoice] = []
            matches_date: list[_OracleInvoice] = []
            # Split by priority instead of insert(0), which reversed the tie-break order
            # and made the last priority candidate win.
            matches_fuzzy_num_corroborated: list[_OracleInvoice] = []
            matches_fuzzy_num: list[_OracleInvoice] = []

            for o_inv in available_o_invoices:
                date_ok = _dates_match(inv_date_norm, inv_date_cmp, o_inv.date_norm, o_inv.date_cmp)
                amt_ok = inv_amt_cmp is not None and inv_amt_cmp == o_inv.amount

                if date_ok and amt_ok:
                    matches_date_amt.append(o_inv)

                if amt_ok:
                    matches_amt.append(o_inv)
                if date_ok:
                    matches_date.append(o_inv)

            if not matches_date_amt:
                for o_inv in available_o_invoices:
                    if not _is_num_ok(inv_num, o_inv.number):
                        continue
                    amt_ok = inv_amt_cmp is not None and inv_amt_cmp == o_inv.amount
                    date_ok = _dates_match(inv_date_norm, inv_date_cmp, o_inv.date_norm, o_inv.date_cmp)
                    if amt_ok or date_ok:
                        matches_fuzzy_num_corroborated.append(o_inv)
                    else:
                        matches_fuzzy_num.append(o_inv)

            if matches_date_amt:
                matched_o_inv = matches_date_amt[0]
            elif matches_fuzzy_num_corroborated:
                matched_o_inv = matches_fuzzy_num_corroborated[0]
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


async def process_reconciliation_batch(
    payload: ReconciliationRequest,
    client: httpx.AsyncClient,
    oracle_user: str,
    oracle_pass: str
) -> tuple[ReconciliationRequest | None, str | None, int | None]:
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting RECONCILIATION for payload")
    start_time = time.time()

    user = oracle_user
    pwd = oracle_pass

    try:
        customer_name, cached_r_res = await discover_potential_customers(client, user, pwd, payload)
    except Exception as e:
        logger.exception(f"[{request_id}] Oracle fetch failed during customer discovery: {type(e).__name__}: {e}")
        return None, CLIENT_UPSTREAM_ERROR, 502

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
        i_raw, r_raw = await asyncio.gather(i_task, r_task, return_exceptions=True)  # type: ignore

    if isinstance(i_raw, BaseException) or isinstance(r_raw, BaseException):
        err = i_raw if isinstance(i_raw, BaseException) else r_raw
        logger.error(f"[{request_id}] Oracle fetch failed during ledger fetch: {type(err).__name__}: {err}", exc_info=err)
        return None, CLIENT_UPSTREAM_ERROR, 502

    all_invoices_raw = _filter_data_rows(i_raw)
    all_receipts_raw = _filter_data_rows(r_raw)

    map_ledger_to_payload(payload, customer_name, all_receipts_raw, all_invoices_raw)

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] RECON COMPLETE: Customer='{customer_name}'. Returning mapped payload in {duration}ms.")
    return payload, None, None
