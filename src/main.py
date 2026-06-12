import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

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
async def root():
    return {"status": "online", "message": "Oracle Reconciliation API is running"}

async def _fetch_receipt_data(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str):
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

async def _fetch_invoices_concurrently(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str, customer_name: str):
    sem = app.state.oracle_sem
    shared_customer_cache = {}
    customer_lock = asyncio.Lock()

    async def check_invoice_with_semaphore(*args, **kwargs):
        async with sem:
            return await check_invoice_cascading(*args, **kwargs)

    tasks = []
    for inv in payload.invoices:
        inv_num = str(inv.invoice_number) if inv.invoice_number else ""
        inv_date = str(inv.invoice_date) if inv.invoice_date else ""
        inv_amount = inv.invoice_amount
        doc_num = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""

        tasks.append(check_invoice_with_semaphore(
            http_client, x_oracle_user, x_oracle_pass, inv_num, inv_date, inv_amount, doc_num, customer_name,
            cache_customer=shared_customer_cache, customer_lock=customer_lock
        ))

    return await asyncio.gather(*tasks, return_exceptions=True)

def _map_invoice_results(payload: ReconciliationRequest, invoice_results: list):
    for idx, inv in enumerate(payload.invoices):
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

    customer_name = str(payload.customer_name) if payload.customer_name else ""
    invoice_results = await _fetch_invoices_concurrently(payload, x_oracle_user, x_oracle_pass, customer_name)
    
    _map_invoice_results(payload, invoice_results)

    execution_time = round(time.time() - start_time, 2)
    # At 150, Oracle throttles to 26 TPS. At 50, it peaks at 52 TPS.
    logger.info(f"Reconciliation completed in {execution_time}s. Invoices checked: {len(invoice_results)}")

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

async def _build_bip_invoice_map(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str) -> dict:
    invoice_numbers = set()
    for inv in payload.invoices:
        num = str(inv.invoice_number) if inv.invoice_number else ""
        if num:
            invoice_numbers.add(num)

    if invoice_numbers:
        return await run_bip_bulk_match(http_client, x_oracle_user, x_oracle_pass, list(invoice_numbers))
    return {}

def _map_bip_invoices(payload: ReconciliationRequest, invoice_map: dict):
    for inv in payload.invoices:
        num = str(inv.invoice_number) if inv.invoice_number else ""
        if num:
            if num in invoice_map:
                match = invoice_map[num]
                inv.fusion_invoice_number = match.get("TransactionNumber") or match.get("InvoiceNumber")
                inv.fusion_invoice_date = match.get("TransactionDate") or match.get("InvoiceDate")
                try:
                    inv.fusion_invoice_amount = float(match.get("EnteredAmount") or match.get("Amount") or 0.0)
                except ValueError:
                    inv.fusion_invoice_amount = 0.0
            else:
                if payload.meta_data is None:
                    payload.meta_data = MetaDataModel()
                payload.meta_data.warnings.append(f"Invoice {num} match failed: Not found in BIP extract.")

@app.post("/reconcile/bip", response_model=ReconciliationRequest)
async def reconcile_data_bip(payload: ReconciliationRequest):
    """
    Experimental reconciliation logic using BI Publisher for bulk SQL fetching.
    """
    try:
        x_oracle_user = os.getenv("ORACLE_USER")
        x_oracle_pass = os.getenv("ORACLE_PASS")

        invoice_map = await _build_bip_invoice_map(payload, x_oracle_user, x_oracle_pass)
        _map_bip_invoices(payload, invoice_map)

        await _fetch_receipt_data(payload, x_oracle_user, x_oracle_pass)

        return payload
    except Exception as e:
        logger.error(f"Top-level processing exception in BIP: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
