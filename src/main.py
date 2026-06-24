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


async def _discover_by_receipt(client: httpx.AsyncClient, user: str, pwd: str, r_num: str) -> str | None:
    if not r_num:
        return None
        
    logger.info(f"Step 2: Searching Receipt Report using payment_reference '{r_num}'")
    
    def _verify_receipt(raw_receipts: list[dict[str, Any]]) -> str | None:
        if raw_receipts:
            cand_name = raw_receipts[0].get("BILL_CUSTOMER_NAME", "").strip()
            if cand_name:
                return cand_name
        return None

    r_res = await fetch_bip_receipts(client, user, pwd, receipt_number=r_num)
    cand = _verify_receipt(_filter_data_rows(r_res))
    if cand:
        return cand

    return None

async def _discover_by_invoice_sequence(client: httpx.AsyncClient, user: str, pwd: str, invoices: list) -> str | None:
    from src.constants import DEFAULT_CONCURRENCY
    
    levels = [
        {"desc": "Step 3 (Priority 1: Invoice Number)", "use_amt": False, "use_date": False},
        {"desc": "Step 3 (Priority 2: Invoice Number + Amount)", "use_amt": True, "use_date": False},
        {"desc": "Step 3 (Priority 3: Invoice Number + Amount + Date)", "use_amt": True, "use_date": True},
    ]
    
    for level in levels:
        logger.info(f"Executing {level['desc']} sequence...")
        
        queries = []
        for inv in invoices:
            i_num = str(inv.invoice_number).strip() if inv.invoice_number else ""
            if not i_num:
                continue
                
            kwargs = {"invoice_number": i_num}
            if level["use_amt"] and inv.invoice_amount is not None:
                kwargs["invoice_amount"] = str(inv.invoice_amount)
            if level["use_date"] and inv.invoice_date:
                kwargs["invoice_date"] = str(inv.invoice_date).strip()
                
            queries.append(kwargs)
            
        if not queries:
            continue
            
        chunk_size = DEFAULT_CONCURRENCY
        discovered_candidates = set()
        
        for i in range(0, len(queries), chunk_size):
            chunk = queries[i : i + chunk_size]
            tasks = [fetch_bip_invoices(client, user, pwd, **kw) for kw in chunk]
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
                            logger.error(f"Invoice fetch failed in sequence: {e}")
            finally:
                for p in pending:
                    p.cancel()
                    
        if len(discovered_candidates) == 1:
            d_name = list(discovered_candidates)[0]
            logger.info(f"Successfully isolated unique customer '{d_name}' at {level['desc']}")
            return d_name
        elif len(discovered_candidates) > 1:
            logger.warning(f"Multiple customers found {list(discovered_candidates)} at {level['desc']}, narrowing down...")
        else:
            logger.warning(f"No customers found at {level['desc']}")
            
    return None

async def _discover_potential_customers(
    client: httpx.AsyncClient, user: str, pwd: str, payload: ReconciliationRequest
) -> list[str]:
    c_name = str(payload.customer_name).strip() if payload.customer_name else None
    r_num = str(payload.payment_reference).strip() if payload.payment_reference else None

    # Special Case: Both Null -> Skip directly to Step 3
    if not c_name and not r_num:
        logger.warning("Special Case Triggered: Both Customer Name and Payment Reference are NULL. Jumping to Step 3.")
        d_name = await _discover_by_invoice_sequence(client, user, pwd, payload.invoices)
        return [d_name] if d_name else []

    # Step 1: Direct Identification (Customer Name)
    if c_name:
        logger.info(f"Step 1: Testing customer_name from JSON: '{c_name}' in Receipt Details Report")
        r_res = await fetch_bip_receipts(client, user, pwd, customer_name=c_name)
        
        if _filter_data_rows(r_res):
            logger.info(f"Step 1: Confirmed customer '{c_name}' has ledger data.")
            return [c_name]
            
        logger.warning(f"Step 1: Customer '{c_name}' has no ledger data. Moving to Step 2.")

    # Step 2: Reference-Based Identification (Payment Reference)
    if r_num:
        d_name = await _discover_by_receipt(client, user, pwd, r_num)
        if d_name:
            logger.info(f"Step 2: Successfully identified customer '{d_name}' via Payment Reference.")
            return [d_name]
        logger.warning("Step 2 failed to identify customer. Moving to Step 3.")

    # Step 3: Invoice-Based Identification
    logger.info("Step 3: Attempting to identify Customer Name using Invoice Details Report sequence...")
    d_name = await _discover_by_invoice_sequence(client, user, pwd, payload.invoices)
    if d_name:
        return [d_name]

    logger.warning("Customer could not be identified after all steps. Returning NULL.")
    return []





@app.post("/v1/reconcile/batch", response_model=ReconciliationRequest | None)
async def reconcile_data_batch(payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting RECONCILIATION for payload")
    start_time = time.time()
    
    client = get_http_client()
    user = os.getenv("ORACLE_USER", "")
    pwd = os.getenv("ORACLE_PASS", "")

    try:
        potential_customers = await _discover_potential_customers(client, user, pwd, payload)
    except Exception as e:
        logger.error(f"[{request_id}] Oracle fetch failed: {e}")
        raise HTTPException(
            status_code=502, detail=f"Oracle API returned an error or timed out while fetching records: {e}"
        ) from e

    if not potential_customers:
        logger.warning(f"[{request_id}] Unable to determine customer name. Returning null.")
        return None

    # We just take the first valid customer discovered
    customer_name = potential_customers[0]
    payload.fusion_customer_name = customer_name
    
    logger.info(f"[{request_id}] Fetching ledger to map columns for '{customer_name}'")
    
    # ── STEP 2: Fetch Ledger for this Customer ──
    i_task = fetch_bip_invoices(client, user, pwd, customer_name=customer_name)
    r_task = fetch_bip_receipts(client, user, pwd, customer_name=customer_name)
    i_raw, r_raw = await asyncio.gather(i_task, r_task, return_exceptions=True)

    if isinstance(i_raw, Exception) or isinstance(r_raw, Exception):
        err = i_raw if isinstance(i_raw, Exception) else r_raw
        logger.error(f"[{request_id}] Oracle fetch failed during ledger fetch: {err}")
        raise HTTPException(status_code=502, detail=f"Oracle API error during ledger fetch: {err}")

    all_invoices_raw = _filter_data_rows(i_raw)
    all_receipts_raw = _filter_data_rows(r_raw)

    # ── STEP 3: Map Receipt ──
    receipt_number = str(payload.payment_reference).strip() if payload.payment_reference else ""
    if receipt_number:
        for r in all_receipts_raw:
            cand_num = str(r.get("RECEIPT_NUMBER", "")).strip()
            if cand_num and (receipt_number.lower() in cand_num.lower() or cand_num.lower() in receipt_number.lower()):
                payload.fusion_receipt_number = r.get("RECEIPT_NUMBER")
                payload.fusion_receipt_date = r.get("RECEIPT_DATE")
                payload.fusion_applied_amount = r.get("RECEIPT_AMOUNT")
                break

    # ── STEP 4: Map Invoices ──
    for invoice in payload.invoices:
        inv_num = str(invoice.invoice_number) if invoice.invoice_number else ""
        inv_date = str(invoice.invoice_date).strip() if invoice.invoice_date else ""
        inv_amt = invoice.invoice_amount
        
        if not inv_num:
            continue
        
        for o_inv in all_invoices_raw:
            o_num = str(o_inv.get("TRANSACTION_NUMBER") or o_inv.get("INVOICE_NUMBER", ""))
            o_date = str(o_inv.get("TRANSACTION_DATE") or o_inv.get("INVOICE_DATE", ""))
            o_amt = o_inv.get("TRANSACTION_TOTAL") or o_inv.get("TOTAL_AMOUNTS") or o_inv.get("INVOICE_AMOUNT")
            
            if str(inv_num) == str(o_num) and str(inv_date) == str(o_date) and str(inv_amt) == str(o_amt):
                invoice.fusion_invoice_number = o_inv.get("TRANSACTION_NUMBER") or o_inv.get("INVOICE_NUMBER")
                invoice.fusion_invoice_date = o_inv.get("TRANSACTION_DATE") or o_inv.get("INVOICE_DATE")
                invoice.fusion_invoice_amount = o_inv.get("TRANSACTION_TOTAL") or o_inv.get("TOTAL_AMOUNTS") or o_inv.get("INVOICE_AMOUNT")
                invoice.match_phase = "MATCHED"
                break

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] RECON COMPLETE: Customer='{customer_name}'. Returning mapped payload in {duration}ms.")
    return payload
