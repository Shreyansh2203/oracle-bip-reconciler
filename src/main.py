import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from src.models import MetaDataModel, ReconciliationRequest
from src.services.oracle_bip import run_bip_bulk_match
from src.services.oracle_matcher import check_invoice_cascading, check_receipt_cascading

load_dotenv()

# Constants
DEFAULT_TIMEOUT = 15.0
MAX_CONNECTIONS = 200
DEFAULT_CONCURRENCY = 50

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")

# Global HTTP client
http_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS))
    sem_limit = int(os.getenv("MAX_CONCURRENCY", str(DEFAULT_CONCURRENCY)))
    app.state.oracle_sem = asyncio.Semaphore(sem_limit)
    logger.info("Starting up global HTTP client")
    yield
    logger.info("Shutting down global HTTP client")
    if http_client:
        await http_client.aclose()

app = FastAPI(
    title="Oracle Reconciliation Live API",
    version="4.0.0",
    description="Professional enterprise API for Oracle ERP Cloud reconciliation matching.",
    lifespan=lifespan
)

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "online", "message": "Oracle Reconciliation API is running"}

async def _fetch_receipt_data(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str) -> None:
    receipt_num = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""
    customer_name = str(payload.customer_name) if payload.customer_name else ""

    logger.info("Fetching Receipt data...")
    receipt_result = await check_receipt_cascading(
        http_client, x_oracle_user, x_oracle_pass, receipt_num, receipt_amount, receipt_date, customer_name
    )

    if receipt_result.get("matched_in_oracle"):
        payload.fusion_receipt_number = receipt_result.get("fusion_receipt_number")
        payload.fusion_receipt_date = receipt_result.get("fusion_receipt_date")
        payload.fusion_customer_name = receipt_result.get("fusion_customer_name")
    else:
        clean_error = str(receipt_result.get('error', '')).replace("\n", " ").replace("\r", " ")
        logger.warning(f"Receipt match error or not found: {clean_error}")
        if payload.meta_data is None:
            payload.meta_data = MetaDataModel()
        payload.meta_data.warnings.append(f"Receipt match failed: {clean_error}")

async def _fetch_invoices_concurrently(payload: ReconciliationRequest, unmatched_invoices: list[Any], x_oracle_user: str, x_oracle_pass: str, customer_name: str) -> list[Any]:
    sem = app.state.oracle_sem
    shared_customer_cache = {}
    customer_lock = asyncio.Lock()

    async def check_invoice_with_semaphore(*args, **kwargs):
        async with sem:
            return await check_invoice_cascading(*args, **kwargs)

    unique_searches = {}
    tasks = []

    for inv in unmatched_invoices:
        inv_num = str(inv.invoice_number) if inv.invoice_number else ""
        inv_date = str(inv.invoice_date) if inv.invoice_date else ""
        inv_amount = inv.invoice_amount
        doc_num = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""

        search_key = (inv_num, inv_date, inv_amount, doc_num)
        if search_key not in unique_searches:
            unique_searches[search_key] = len(tasks)
            tasks.append(check_invoice_with_semaphore(
                http_client, x_oracle_user, x_oracle_pass, inv_num, inv_date, inv_amount, doc_num, customer_name,
                cache_customer=shared_customer_cache, customer_lock=customer_lock
            ))

    unique_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_results = []
    for inv in unmatched_invoices:
        inv_num = str(inv.invoice_number) if inv.invoice_number else ""
        inv_date = str(inv.invoice_date) if inv.invoice_date else ""
        inv_amount = inv.invoice_amount
        doc_num = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""
        
        search_key = (inv_num, inv_date, inv_amount, doc_num)
        final_results.append(unique_results[unique_searches[search_key]])

    return final_results

def _map_invoice_results(payload: ReconciliationRequest, unmatched_invoices: list[Any], invoice_results: list[Any]) -> None:
    for idx, inv in enumerate(unmatched_invoices):
        inv_res = invoice_results[idx]
        if isinstance(inv_res, BaseException):
            if payload.meta_data is None:
                payload.meta_data = MetaDataModel()
            payload.meta_data.warnings.append(f"Invoice {inv.invoice_number} match failed due to unhandled exception: {str(inv_res)}")
        elif inv_res and inv_res.get("matched_in_oracle"):
            inv.fusion_invoice_number = inv_res.get("fusion_invoice_number")
            inv.fusion_invoice_date = inv_res.get("fusion_invoice_date")
            inv.fusion_invoice_amount = inv_res.get("fusion_invoice_amount")
        elif inv_res and inv_res.get("error"):
            if payload.meta_data is None:
                payload.meta_data = MetaDataModel()
            payload.meta_data.warnings.append(f"Invoice {inv.invoice_number} match failed: {inv_res.get('error')}")

async def _process_reconciliation(payload: ReconciliationRequest) -> ReconciliationRequest:
    """
    Core logic for executing the reconciliation matching.
    """
    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")

    if not x_oracle_user or not x_oracle_pass:
        logger.error("Oracle credentials are not configured in the environment.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Oracle credentials are not configured.")

    if not http_client:
        logger.error("Global HTTP client is not initialized")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error: HTTP client not initialized")

    start_time = time.time()

    await _fetch_receipt_data(payload, x_oracle_user, x_oracle_pass)

    invoice_map = await _build_bip_invoice_map(payload, x_oracle_user, x_oracle_pass)
    unmatched_invoices = _map_bip_invoices(payload, invoice_map)

    if unmatched_invoices:
        logger.info(f"BIP matched {len(payload.invoices) - len(unmatched_invoices)} invoices. Falling back to REST for {len(unmatched_invoices)} unmatched invoices.")
        customer_name = str(payload.customer_name) if payload.customer_name else ""
        invoice_results = await _fetch_invoices_concurrently(payload, unmatched_invoices, x_oracle_user, x_oracle_pass, customer_name)
        _map_invoice_results(payload, unmatched_invoices, invoice_results)

    execution_time = round(time.time() - start_time, 2)
    # At 150, Oracle throttles to 26 TPS. At 50, it peaks at 52 TPS.
    logger.info(f"Reconciliation completed in {execution_time}s. Total invoices: {len(payload.invoices)}")

    return payload

@app.post("/reconcile", response_model=ReconciliationRequest)
async def reconcile_data(payload: ReconciliationRequest):
    """
    Standard reconciliation logic executing sequential REST API fetcher.
    """
    try:
        return await _process_reconciliation(payload)
    except Exception as e:
        logger.error(f"Top-level processing exception: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e

async def _build_bip_invoice_map(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str) -> dict[str, Any]:
    invoice_numbers = set()
    for inv in payload.invoices:
        num = str(inv.invoice_number) if inv.invoice_number else ""
        if num:
            invoice_numbers.add(num)

    invoice_list = list(invoice_numbers)
    if not invoice_list:
        return {}

    chunk_size = 500
    chunks = [invoice_list[i:i + chunk_size] for i in range(0, len(invoice_list), chunk_size)]
    
    tasks = []
    for chunk in chunks:
        tasks.append(run_bip_bulk_match(http_client, x_oracle_user, x_oracle_pass, chunk))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_map = {}
    for res in results:
        if isinstance(res, dict):
            final_map.update(res)
        else:
            logger.error(f"BIP chunk fetch failed: {res}")
            
    return final_map

def _map_bip_invoices(payload: ReconciliationRequest, invoice_map: dict[str, Any]) -> list[Any]:
    """Maps BIP exact matches and returns a list of unmatched invoices for REST fallback."""
    unmatched_invoices = []
    for inv in payload.invoices:
        num = str(inv.invoice_number) if inv.invoice_number else ""
        if num and num in invoice_map:
            match = invoice_map[num]
            inv.fusion_invoice_number = match.get("TransactionNumber") or match.get("InvoiceNumber")
            inv.fusion_invoice_date = match.get("TransactionDate") or match.get("InvoiceDate")
            try:
                inv.fusion_invoice_amount = float(match.get("EnteredAmount") or match.get("Amount") or 0.0)
            except ValueError:
                inv.fusion_invoice_amount = 0.0
        else:
            unmatched_invoices.append(inv)
    return unmatched_invoices
