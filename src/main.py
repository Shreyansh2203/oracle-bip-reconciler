from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_oracle_url
from src.constants import DEFAULT_TIMEOUT, MAX_CONNECTIONS, PHASE_CLOSED_OR_OTHER
from src.models import ReconciliationRequest
from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts
from src.services.oracle_matcher import (
    OracleInvoiceIndex,
    OracleReceiptIndex,
    match_invoice_by_customer,
    match_invoices_bipartite,
    match_receipt_in_memory,
    safe_float_match,
    safe_str_match,
)

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("reconciliation_api")


def _redact(name: str | None) -> str:
    if not name:
        return "UNKNOWN"
    return f"{name[:3]}***"


# Global HTTP client
http_client: httpx.AsyncClient | None = None
_http_client_lock = threading.Lock()


def get_http_client() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        with _http_client_lock:
            if http_client is None:
                http_client = httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT,
                    limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS),
                )
                logger.info("Lazily initialized global HTTP client")
    return http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_http_client()
    yield
    logger.info("Shutting down global HTTP client")
    global http_client
    if http_client:
        await http_client.aclose()
        http_client = None


app = FastAPI(
    title="Oracle Reconciliation Live API",
    version="4.0.0",
    description="Professional enterprise API for Oracle ERP Cloud reconciliation matching.",
    lifespan=lifespan,
)

# Setup CORS
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if not origins:
    logger.warning("CORS_ORIGINS is empty. API will fail closed to browsers.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else [],  # Fail closed if no origins provided
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "online", "message": "Oracle Reconciliation API is running"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def readiness_check() -> dict[str, str]:
    try:
        oracle_url = get_oracle_url()
    except ValueError:
        oracle_url = None
    if not os.getenv("ORACLE_USER") or not os.getenv("ORACLE_PASS") or not oracle_url:
        raise HTTPException(status_code=503, detail="Service not ready: missing required configuration")
    return {"status": "ready"}


# Helpers for Batch Mapping


def _is_data_row(row: dict[str, Any]) -> bool:
    """Check if a CSV row contains actual data columns (not just parameter echo)."""
    data_columns = {
        "BILL_CUSTOMER_NAME",
        "TRANSACTION_NUMBER",
        "RECEIPT_NUMBER",
        "CUSTOMER_NAME",
        "ACCOUNT_NUMBER",
        "BUSINESS_UNIT",
        "CURRENCY",
        "INVOICE_STATUS",
        "RECEIPT_STATUS_CODE",
    }
    row_keys = {k.lstrip("\ufeff").strip().upper() for k in row.keys()}
    return bool(row_keys & data_columns)


def _filter_data_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out parameter-only rows from BIP CSV results."""
    if not rows:
        return rows
    # Check first row: if it has data columns, all rows are data rows
    if _is_data_row(rows[0]):
        return rows
    # Otherwise filter individually (shouldn't normally happen)
    return [r for r in rows if _is_data_row(r)]


async def _discover_potential_customers(
    client: httpx.AsyncClient, user: str, pwd: str, payload: ReconciliationRequest
) -> list[str]:
    """
    Simplified Discovery Logic:
    1. If customer_name exists -> return it.
    2. If customer_name is null -> search by payment_reference and verify using Receipt Score.
    3. If both are null -> search by invoice details, then verify by checking if a receipt exists with the total_amount & payment_date.
    """
    from src.constants import DEFAULT_CONCURRENCY
    from src.services.oracle_matcher import score_receipt_candidate
    from src.utils.date_formatter import format_oracle_date

    c_name = str(payload.customer_name).strip() if payload.customer_name else None
    r_num = str(payload.payment_reference).strip() if payload.payment_reference else None
    r_date = str(payload.payment_date).strip() if payload.payment_date else None
    r_amt = payload.total_amount

    # Rule 1: Use customer name
    if c_name:
        logger.info(f"Rule 1: Discovered customer from JSON 'customer_name': {c_name}")
        return [c_name]

    # Rule 2: If customer_name is null, use payment reference
    if r_num:
        logger.info(f"Rule 2: Searching Receipt Report using payment_reference '{r_num}'")
        
        # Helper to verify receipt candidate
        def _verify_receipt(raw_receipts: list[dict[str, Any]]) -> str | None:
            formatted_date = format_oracle_date(r_date) if r_date else None
            for r in raw_receipts:
                score = score_receipt_candidate(r, r_num, r_amt, formatted_date)
                if score >= 50:
                    cand_name = r.get("BILL_CUSTOMER_NAME", "").strip()
                    if cand_name:
                        return cand_name
            return None

        # Try exact reference
        try:
            r_res = await fetch_bip_receipts(client, user, pwd, receipt_number=r_num)
            receipts_raw = _filter_data_rows(r_res)
            discovered_name = _verify_receipt(receipts_raw)
            if discovered_name:
                logger.info(f"Rule 2 (Exact): Verified customer '{discovered_name}' via Receipt Score")
                return [discovered_name]
        except Exception as e:
            logger.warning(f"Rule 2 (Exact) fetch failed: {e}")

        # Try stripped reference (e.g. ignoring leading zeroes or spaces)
        stripped_r_num = re.sub(r"[^a-zA-Z0-9]", "", r_num).lstrip("0").lower()
        if stripped_r_num and len(stripped_r_num) >= 5 and stripped_r_num != r_num.lower():
            logger.info(f"Rule 2 (Stripped): Searching Receipt Report using '{stripped_r_num}'")
            try:
                r_res = await fetch_bip_receipts(client, user, pwd, receipt_number=stripped_r_num)
                receipts_raw = _filter_data_rows(r_res)
                discovered_name = _verify_receipt(receipts_raw)
                if discovered_name:
                    logger.info(f"Rule 2 (Stripped): Verified customer '{discovered_name}' via Receipt Score")
                    return [discovered_name]
            except Exception as e:
                logger.warning(f"Rule 2 (Stripped) fetch failed: {e}")

    # Rule 3: If both are null, use invoices
    p4_queries = []
    for inv in payload.invoices:
        i_num = str(inv.invoice_number).strip() if inv.invoice_number else ""
        i_date = str(inv.invoice_date).strip() if inv.invoice_date else ""
        i_amt = inv.invoice_amount
        if i_num and i_date and i_amt is not None:
            p4_queries.append({"invoice_number": i_num, "invoice_date": i_date, "invoice_amount": str(i_amt)})

    if p4_queries:
        logger.info("Rule 3: Searching Invoice Report using provided invoices...")
        chunk_size = DEFAULT_CONCURRENCY
        
        discovered_candidates = set()
        for i in range(0, len(p4_queries), chunk_size):
            chunk = p4_queries[i : i + chunk_size]
            tasks = [fetch_bip_invoices(client, user, pwd, **kwargs) for kwargs in chunk]
            pending = [asyncio.create_task(t) for t in tasks]
            try:
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        try:
                            i_res = task.result()
                            invoices_raw = _filter_data_rows(i_res)
                            if invoices_raw:
                                d_name = invoices_raw[0].get("BILL_CUSTOMER_NAME", "").strip()
                                if d_name:
                                    discovered_candidates.add(d_name)
                        except Exception as e:
                            logger.error(f"Rule 3 Invoice fetch failed: {e}")
            finally:
                for p in pending:
                    p.cancel()

        # "also match payment date and total amount just to confirm we are fetching correct records"
        valid_customers = []
        for cand_name in discovered_candidates:
            logger.info(f"Rule 3 Verification: Checking receipt for '{cand_name}' (Amount: {r_amt}, Date: {r_date})")
            try:
                r_res = await fetch_bip_receipts(
                    client, user, pwd, 
                    customer_name=cand_name,
                    receipt_amount=r_amt,
                    receipt_date=r_date
                )
                receipts_raw = _filter_data_rows(r_res)
                if receipts_raw:
                    logger.info(f"Rule 3 Verification: Confirmed customer '{cand_name}' via matched receipt")
                    valid_customers.append(cand_name)
            except Exception as e:
                logger.warning(f"Rule 3 Verification failed for '{cand_name}': {e}")
                
        return valid_customers

    logger.warning("No valid identifiers found to execute discovery.")
    return []


def _try_match_receipt(
    receipt_number: str, receipt_amount: float | None, receipt_date: str, customer_name: str, index: OracleReceiptIndex
) -> dict[str, Any] | None:
    """Try to match a receipt against a dataset. Returns match result or None."""
    if not index.bip_receipts:
        return None
    result = match_receipt_in_memory(receipt_number, receipt_amount, receipt_date, customer_name, index)
    if result.get("matched_in_oracle"):
        return result
    return None


def _apply_receipt_match_result(payload: ReconciliationRequest, receipt_result: dict[str, Any]) -> None:
    payload.fusion_receipt_number = receipt_result.get("fusion_receipt_number")
    payload.fusion_receipt_date = receipt_result.get("fusion_receipt_date")
    payload.fusion_customer_name = receipt_result.get("fusion_customer_name")
    payload.fusion_customer_number = receipt_result.get("fusion_customer_number")
    payload.fusion_currency = receipt_result.get("fusion_currency")
    payload.fusion_receipt_status_code = receipt_result.get("fusion_receipt_status_code")
    payload.fusion_applied_amount = receipt_result.get("fusion_applied_amount")
    payload.match_phase = receipt_result.get("match_phase")
    payload.match_rule = receipt_result.get("match_rule")


def _apply_invoice_match_result(invoice: Any, invoice_result: dict[str, Any]) -> None:
    invoice.fusion_invoice_number = invoice_result.get("fusion_invoice_number")
    invoice.fusion_invoice_date = invoice_result.get("fusion_invoice_date")
    invoice.fusion_invoice_amount = invoice_result.get("fusion_invoice_amount")
    invoice.match_phase = invoice_result.get("match_phase")
    invoice.match_rule = invoice_result.get("match_rule")


@app.post("/v1/reconcile/batch", response_model=ReconciliationRequest | None)
async def reconcile_data_batch(payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting RECONCILIATION for customer {_redact(payload.customer_name)}")
    start_time = time.time()

    receipt_number = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""

    client = get_http_client()
    user = os.getenv("ORACLE_USER", "")
    pwd = os.getenv("ORACLE_PASS", "")

    # ── STEP 1: Discover Potential Customers ──
    try:
        potential_customers = await _discover_potential_customers(client, user, pwd, payload)
    except Exception as e:
        logger.error(f"[{request_id}] Oracle fetch failed: {e}")
        raise HTTPException(
            status_code=502, detail=f"Oracle API returned an error or timed out while fetching records: {e}"
        ) from e

    if not potential_customers:
        logger.warning(
            f"[{request_id}] Unable to safely determine customer name from available identifiers. Returning null."
        )
        return None

    # Loop over all potential customers. First one to yield a successful match wins.
    import copy

    for customer_name in potential_customers:
        logger.info(f"[{request_id}] Testing customer ledger: '{customer_name}'")
        attempt_payload = copy.deepcopy(payload)

        # ── STEP 2: Index Records for this Customer ──
        i_task = fetch_bip_invoices(client, user, pwd, customer_name=customer_name)
        r_task = fetch_bip_receipts(client, user, pwd, customer_name=customer_name)
        i_raw, r_raw = await asyncio.gather(i_task, r_task, return_exceptions=True)

        if isinstance(i_raw, Exception) or isinstance(r_raw, Exception):
            err = i_raw if isinstance(i_raw, Exception) else r_raw
            logger.error(f"[{request_id}] Oracle fetch failed during step 2: {err}")
            raise HTTPException(status_code=502, detail=f"Oracle API error during ledger fetch: {err}")

        all_invoices_raw = _filter_data_rows(i_raw)
        all_receipts_raw = _filter_data_rows(r_raw)

        all_invoices = OracleInvoiceIndex(all_invoices_raw)
        all_receipts = OracleReceiptIndex(all_receipts_raw)

        receipt_matched = False
        matched_count = 0
        unmatched_invoices = list(attempt_payload.invoices)

        # ── STEP 3: Match Receipt ──
        receipt_result = await asyncio.to_thread(
            _try_match_receipt, receipt_number, receipt_amount, receipt_date, customer_name, all_receipts
        )
        if receipt_result:
            _apply_receipt_match_result(attempt_payload, receipt_result)
            receipt_matched = True

        # ── STEP 4: Match Invoices (Exact) ──
        still_unmatched = []
        for invoice in unmatched_invoices:
            invoice_num_str = str(invoice.invoice_number) if invoice.invoice_number else ""
            invoice_date_str = str(invoice.invoice_date) if invoice.invoice_date else ""
            invoice_amt_float = invoice.invoice_amount
            document_num_str = str(invoice.customer_invoice_number) if invoice.customer_invoice_number else ""

            result = await asyncio.to_thread(
                match_invoice_by_customer,
                invoice_num_str,
                invoice_date_str,
                invoice_amt_float,
                document_num_str,
                customer_name,
                all_invoices,
            )
            if result.get("matched_in_oracle"):
                _apply_invoice_match_result(invoice, result)
                matched_count += 1
            else:
                still_unmatched.append(invoice)
        unmatched_invoices = still_unmatched

        # ── STEP 5: Match Invoices (Fuzzy Bipartite Fallback) ──
        if unmatched_invoices and all_invoices_raw:
            bipartite_results = await asyncio.to_thread(
                match_invoices_bipartite, unmatched_invoices, customer_name, all_invoices, phase=PHASE_CLOSED_OR_OTHER
            )
            still_unmatched = []
            for i, invoice in enumerate(unmatched_invoices):
                if i in bipartite_results:
                    _apply_invoice_match_result(invoice, bipartite_results[i])
                    matched_count += 1
                else:
                    still_unmatched.append(invoice)
            unmatched_invoices = still_unmatched

        # ── EVALUATE SUCCESS ──
        # If we found any match (either receipt or at least one invoice), this is the correct customer
        if receipt_matched or matched_count > 0:
            if not receipt_matched:
                attempt_payload.add_warning("Receipt: No matching record found in Oracle.")
            for invoice in unmatched_invoices:
                inv_num = str(invoice.invoice_number) if invoice.invoice_number else "UNKNOWN"
                attempt_payload.add_warning(f"Invoice {inv_num}: No matching record found in Oracle.")

            duration = int((time.time() - start_time) * 1000)
            logger.info(
                f"[{request_id}] RECON COMPLETE: Customer='{customer_name}', Receipt={'YES' if receipt_matched else 'NO'}, "
                f"Invoices={matched_count}/{len(attempt_payload.invoices)} matched in {duration}ms"
            )
            return attempt_payload

        logger.info(
            f"[{request_id}] Ledger for '{customer_name}' yielded zero matches. Moving to next potential customer."
        )

    # ── FINAL FAILURE ──
    logger.info(f"[{request_id}] No matching records found across any potential customers. Returning null.")
    return None
