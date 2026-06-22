from __future__ import annotations

import asyncio
import logging
import os
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
    """Smart targeted fetch returning a list of potential customer names."""

    from src.constants import DEFAULT_CONCURRENCY

    async def _search_invoices_concurrently(inv_queries: list[dict[str, Any]]) -> list[str]:
        chunk_size = DEFAULT_CONCURRENCY
        for i in range(0, len(inv_queries), chunk_size):
            chunk = inv_queries[i:i+chunk_size]
            logger.info(f"Priority 5: Searching {len(chunk)} invoices concurrently...")
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
                                discovered_name = invoices_raw[0].get("BILL_CUSTOMER_NAME", "")
                                if discovered_name:
                                    logger.info(f"Discovered customer name from Invoice Fallback: '{discovered_name}'")
                                    return [discovered_name]
                        except Exception:
                            pass
            finally:
                for p in pending:
                    p.cancel()
        return []

    c_name = str(payload.customer_name).strip() if payload.customer_name else None
    r_num = str(payload.payment_reference).strip() if payload.payment_reference else None
    r_date = str(payload.payment_date).strip() if payload.payment_date else None
    r_amt = payload.total_amount

    # Priority 1: payment_reference ONLY
    if r_num:
        logger.info("Priority 1: Searching by payment_reference ONLY")
        r_res = await fetch_bip_receipts(client, user, pwd, receipt_number=r_num)
        receipts_raw = _filter_data_rows(r_res)
        if len(receipts_raw) == 1:
            discovered_name = receipts_raw[0].get("BILL_CUSTOMER_NAME", "")
            if discovered_name:
                logger.info(f"Priority 1 matched uniquely: '{discovered_name}'")
                return [discovered_name]

    # Priority 2: customer_name ONLY
    if c_name:
        logger.info("Priority 2: Searching by customer_name ONLY")
        r_res = await fetch_bip_receipts(client, user, pwd, customer_name=c_name)
        receipts_raw = _filter_data_rows(r_res)
        if receipts_raw:
            logger.info(f"Priority 2 matched: '{c_name}'")
            return [c_name]

    # Priority 3: payment_reference + total_amount + payment_date
    if r_num and r_amt is not None and r_date:
        logger.info("Priority 3: Searching by reference + amount + date")
        r_res = await fetch_bip_receipts(
            client, user, pwd,
            receipt_number=r_num,
            receipt_amount=r_amt,
            receipt_date=r_date
        )
        receipts_raw = _filter_data_rows(r_res)
        if receipts_raw:
            customers = list({r.get("BILL_CUSTOMER_NAME", "") for r in receipts_raw if r.get("BILL_CUSTOMER_NAME", "")})
            if customers:
                logger.info(f"Priority 3 matched multiple/single customers: {customers}")
                return customers

    # Priority 4: payment_date + total_amount
    if r_date and r_amt is not None:
        logger.info("Priority 4: Searching by date + amount ONLY")
        r_res = await fetch_bip_receipts(
            client, user, pwd,
            receipt_date=r_date,
            receipt_amount=r_amt
        )
        receipts_raw = _filter_data_rows(r_res)
        if len(receipts_raw) == 1:
            discovered_name = receipts_raw[0].get("BILL_CUSTOMER_NAME", "")
            if discovered_name:
                logger.info(f"Priority 4 matched uniquely: '{discovered_name}'")
                return [discovered_name]

    # Priority 5: Strict Invoice Search Fallback (Concurrent)
    p5_queries = []
    for inv in payload.invoices or []:
        i_num = str(inv.invoice_number).strip() if inv.invoice_number else ""
        i_date = str(inv.invoice_date).strip() if inv.invoice_date else ""
        i_amt = inv.invoice_amount
        if i_num and i_date and i_amt is not None:
            p5_queries.append({"invoice_number": i_num, "invoice_date": i_date, "invoice_amount": str(i_amt)})

    if p5_queries:
        logger.info("Priority 5: Strict Invoice Report Search Fallback (Num + Date + Amount)")
        return await _search_invoices_concurrently(p5_queries)

    logger.warning("No valid identifiers found to execute safe bulk-fetch.")
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
            status_code=502,
            detail=f"Oracle API returned an error or timed out while fetching records: {e}"
        ) from e

    if not potential_customers:
        logger.warning(f"[{request_id}] Unable to safely determine customer name from available identifiers. Returning null.")
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
        receipt_result = await asyncio.to_thread(_try_match_receipt, receipt_number, receipt_amount, receipt_date, customer_name, all_receipts)
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
                invoice_num_str, invoice_date_str, invoice_amt_float, document_num_str, customer_name, all_invoices
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
                match_invoices_bipartite,
                unmatched_invoices, customer_name, all_invoices, phase=PHASE_CLOSED_OR_OTHER
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
                f"Invoices={matched_count}/{len(attempt_payload.invoices or [])} matched in {duration}ms"
            )
            return attempt_payload

        logger.info(f"[{request_id}] Ledger for '{customer_name}' yielded zero matches. Moving to next potential customer.")

    # ── FINAL FAILURE ──
    logger.info(f"[{request_id}] No matching records found across any potential customers. Returning null.")
    return None
