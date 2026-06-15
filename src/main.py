import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from src.config import get_oracle_url
from src.models import MetaDataModel, ReconciliationRequest
from src.services.oracle_bip import run_bip_bulk_match
from src.services.oracle_matcher import (
    apply_rules_to_candidates,
    check_invoice_cascading,
    check_receipt_cascading,
    is_invoice_open,
    safe_float_match,
    safe_str_match,
)
from src.utils.date_formatter import safe_date_match

# Constants
DEFAULT_TIMEOUT = 15.0
MAX_CONNECTIONS = 200
DEFAULT_CONCURRENCY = 50

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")

# Global HTTP client
http_client = None

# Global Job Store for async endpoint tracking
jobs_store: dict[str, dict[str, Any]] = {}

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Security(api_key_header)):
    expected_api_key = os.getenv("API_KEY")
    if not expected_api_key:
        logger.error("API_KEY environment variable is not set. Failing closed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: API Key not configured.",
        )

    if not api_key or not secrets.compare_digest(api_key, expected_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
    return api_key

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
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["http://localhost:disabled"], # Fail closed if no origins provided
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Mount static files for the UI
app.mount("/public", StaticFiles(directory="public"), name="public")

@app.get("/tester")
async def tester_ui():
    """Serves the automated Async API Web Interface"""
    return FileResponse("public/index.html")

@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "online", "message": "Oracle Reconciliation API is running"}

@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/ready")
async def readiness_check() -> dict[str, str]:
    if not http_client:
        raise HTTPException(status_code=503, detail="Service not ready")
    if not os.getenv("ORACLE_USER") or not os.getenv("ORACLE_PASS") or not get_oracle_url():
        raise HTTPException(status_code=503, detail="Service not ready: missing required configuration")
    return {"status": "ready"}

async def _fetch_receipt_data(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str, sem: asyncio.Semaphore | None = None) -> None:
    receipt_num = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""
    customer_name = str(payload.customer_name) if payload.customer_name else ""

    logger.info("Fetching Receipt data...")
    receipt_result = await check_receipt_cascading(
        http_client, x_oracle_user, x_oracle_pass, receipt_num, receipt_amount, receipt_date, customer_name, sem=sem
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

async def _fetch_invoices_concurrently(payload: ReconciliationRequest, unmatched_invoices: list[Any], x_oracle_user: str, x_oracle_pass: str, customer_name: str, sem: asyncio.Semaphore | None = None) -> list[Any]:
    if sem is None:
        sem = app.state.oracle_sem
    shared_customer_cache = {}
    customer_lock = asyncio.Lock()

    async def check_invoice_with_semaphore(*args, **kwargs):
        return await check_invoice_cascading(*args, **kwargs, sem=sem)

    unique_searches = {}
    tasks = []

    for inv in unmatched_invoices:
        inv_num = str(inv.invoice_number) if inv.invoice_number else ""
        inv_date = str(inv.invoice_date) if inv.invoice_date else ""
        inv_amount = inv.invoice_amount
        doc_num = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""

        inv_amount_str = str(inv.invoice_amount).strip() if inv.invoice_amount is not None else None
        search_key = (inv_num, inv_date, inv_amount_str, doc_num)
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

        inv_amount_str = str(inv.invoice_amount).strip() if inv.invoice_amount is not None else None
        search_key = (inv_num, inv_date, inv_amount_str, doc_num)
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

async def _process_reconciliation(payload: ReconciliationRequest, request_id: str, sem: asyncio.Semaphore | None = None) -> ReconciliationRequest:
    if sem is None:
        sem = app.state.oracle_sem
    """
    Core logic for executing the reconciliation matching.
    """
    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")

    if not x_oracle_user or not x_oracle_pass:
        logger.error("Oracle credentials are not configured in the environment.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Oracle credentials are not configured.")

    if not get_oracle_url():
        logger.error("ORACLE_URL is not configured or is invalid in the environment.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Oracle URL is not configured.")

    if not http_client:
        logger.error("Global HTTP client is not initialized")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error: HTTP client not initialized")

    start_time = time.time()

    await _fetch_receipt_data(payload, x_oracle_user, x_oracle_pass, sem)

    invoice_map = await _build_bip_invoice_map(payload, x_oracle_user, x_oracle_pass)
    unmatched_invoices = _map_bip_invoices(payload, invoice_map)

    invoice_results: list = []
    if unmatched_invoices:
        logger.info(f"BIP matched {len(payload.invoices) - len(unmatched_invoices)} invoices. Falling back to REST for {len(unmatched_invoices)} unmatched invoices.")
        customer_name = str(payload.customer_name) if payload.customer_name else ""
        invoice_results = await _fetch_invoices_concurrently(payload, unmatched_invoices, x_oracle_user, x_oracle_pass, customer_name, sem)
        _map_invoice_results(payload, unmatched_invoices, invoice_results)

    execution_time = round(time.time() - start_time, 2)
    bip_matched_count = len(payload.invoices) - len(unmatched_invoices)
    rest_matched_count = len([inv for inv in unmatched_invoices if inv.fusion_invoice_number])

    # Structured JSON Log
    log_data = {
        "request_id": request_id,
        "invoice_count": len(payload.invoices),
        "bip_matched": bip_matched_count,
        "rest_matched": rest_matched_count,
        "duration_ms": int(execution_time * 1000),
        "oracle_calls": len(invoice_results) if unmatched_invoices else 0, # Approximation of REST calls
    }
    logger.info(f"Reconciliation Summary: {json.dumps(log_data)}")

    return payload

async def _background_reconcile_job(job_id: str, payload: ReconciliationRequest, sem: asyncio.Semaphore) -> None:
    """
    Background worker that runs the exact same reconciliation logic, 
    but catches all errors and securely updates the job store instead of returning an HTTP response.
    """
    try:
        jobs_store[job_id]["status"] = "processing"
        
        # We need a new request_id for logging to distinguish it from the HTTP dispatch
        req_id = str(uuid.uuid4())
        
        # Execute the core matching pipeline
        result_payload = await _process_reconciliation(payload, req_id, sem)
        
        # Serialize the pydantic model cleanly for the job store
        jobs_store[job_id]["result"] = json.loads(result_payload.model_dump_json())
        jobs_store[job_id]["status"] = "completed"
        jobs_store[job_id]["completed_at"] = time.time()
    except Exception as e:
        logger.error(f"[{job_id}] Background job failed: {e}", exc_info=True)
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["error"] = str(e)
        jobs_store[job_id]["completed_at"] = time.time()

@app.post("/v1/reconcile", response_model=ReconciliationRequest)
async def reconcile_data_v1(request: Request, payload: ReconciliationRequest, api_key: str = Depends(get_api_key)):
    """
    Core hybrid endpoint executing BI Publisher bulk match with concurrent REST API fallback.
    """
    request_id = str(uuid.uuid4())
    sem = request.app.state.oracle_sem
    try:
        return await _process_reconciliation(payload, request_id, sem)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Top-level processing exception: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected system error occurred during reconciliation.") from e

@app.post("/v2/reconcile/async")
async def reconcile_data_v2_async(
    request: Request, 
    payload: ReconciliationRequest, 
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key)
):
    """
    Asynchronous V2 Endpoint: Immediately returns a Job ID and pushes processing to a background worker.
    This prevents API timeouts for massive enterprise payloads.
    """
    job_id = str(uuid.uuid4())
    sem = request.app.state.oracle_sem
    
    # Initialize job state
    jobs_store[job_id] = {
        "status": "pending",
        "created_at": time.time(),
        "result": None,
        "error": None,
        "completed_at": None
    }
    
    # Dispatch to background task
    background_tasks.add_task(_background_reconcile_job, job_id, payload, sem)
    
    return {"status": "processing", "job_id": job_id}

@app.get("/v2/reconcile/status/{job_id}")
async def reconcile_status_v2(job_id: str, api_key: str = Depends(get_api_key)):
    """
    Status Polling V2 Endpoint: Check the status of an asynchronous job.
    """
    job = jobs_store.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        
    response = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"]
    }
    
    if job["status"] == "completed":
        response["result"] = job["result"]
    elif job["status"] == "failed":
        response["error"] = job["error"]
        
    return response

async def _build_bip_invoice_map(payload: ReconciliationRequest, x_oracle_user: str, x_oracle_pass: str) -> dict[str, Any]:
    invoice_numbers = set()
    for inv in payload.invoices:
        num = str(inv.invoice_number) if inv.invoice_number else ""
        if num:
            invoice_numbers.add(num)

    invoice_list = list(invoice_numbers)
    if not invoice_list:
        return {}

    chunk_size = 100
    chunks = [invoice_list[i:i + chunk_size] for i in range(0, len(invoice_list), chunk_size)]

    tasks = []
    for chunk in chunks:
        tasks.append(run_bip_bulk_match(http_client, x_oracle_user, x_oracle_pass, chunk))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    final_map = {}
    for res in results:
        if isinstance(res, dict):
            for k, v_list in res.items():
                if k not in final_map:
                    final_map[k] = []
                if isinstance(v_list, list):
                    final_map[k].extend(v_list)
                else:
                    final_map[k].append(v_list)
        else:
            logger.error(f"BIP chunk fetch failed: {res}")

    return final_map

def normalize_invoice_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = {}
    def get_any(keys: list[str]) -> Any:
        for k in keys:
            if k in raw:
                return raw[k]
            if k.upper() in raw:
                return raw[k.upper()]
            if k.lower() in raw:
                return raw[k.lower()]
            k_clean = k.upper().replace("_", "").replace(" ", "")
            if k_clean in raw:
                return raw[k_clean]
        return None
    normalized["TransactionNumber"] = get_any(["TransactionNumber", "TRANSACTION_NUMBER", "InvoiceNumber", "INVOICE_NUMBER"])
    normalized["TransactionDate"] = get_any(["TransactionDate", "TRANSACTION_DATE", "InvoiceDate", "INVOICE_DATE"])
    normalized["EnteredAmount"] = get_any(["EnteredAmount", "ENTERED_AMOUNT", "TotalAmounts", "TOTAL_AMOUNTS", "Amount", "AMOUNT"])
    normalized["InvoiceStatus"] = get_any(["InvoiceStatus", "INVOICE_STATUS", "CreditMemoStatus", "CREDIT_MEMO_STATUS", "Status", "STATUS"])
    normalized["InvoiceBalanceAmount"] = get_any(["InvoiceBalanceAmount", "INVOICE_BALANCE_AMOUNT", "TransactionBalanceDue", "TRANSACTION_BALANCE_DUE", "Balance", "BALANCE", "AMOUNT_DUE_REMAINING", "AmountDueRemaining"])
    normalized["DocumentNumber"] = get_any(["DocumentNumber", "DOCUMENT_NUMBER"])
    normalized["BillToCustomerName"] = get_any(["BillToCustomerName", "BILL_TO_CUSTOMER_NAME", "BillCustomerName", "BILL_CUSTOMER_NAME", "CustomerName", "CUSTOMER_NAME"])
    mapped_upper_keys = {mk.upper().replace("_", "").replace(" ", "") for mk in normalized}
    for k, v in raw.items():
        # Only pass through keys whose normalized equivalent was NOT already mapped
        k_upper = k.upper().replace("_", "").replace(" ", "")
        if k not in normalized and k_upper not in mapped_upper_keys:
            normalized[k] = v
            mapped_upper_keys.add(k_upper)
    return normalized


def _map_bip_invoices(payload: ReconciliationRequest, invoice_map: dict[str, Any]) -> list[Any]:
    """Maps BIP matches using cascading rules and Two-Phase status check, returns unmatched invoices."""
    unmatched_invoices = []
    customer_name = str(payload.customer_name) if payload.customer_name else ""
    for inv in payload.invoices:
        num = str(inv.invoice_number) if inv.invoice_number else ""
        if num and num in invoice_map:
            raw_candidates = invoice_map[num]
            if not isinstance(raw_candidates, list):
                raw_candidates = [raw_candidates]

            normalized_candidates = [normalize_invoice_candidate(cand) for cand in raw_candidates]

            invoice_number = num
            inv_date = str(inv.invoice_date) if inv.invoice_date else ""
            amount = inv.invoice_amount
            document_number = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""

            # Rules ordered per report_processing_rules.md: 1a, 1b, 2, 3, 4
            # Variables bound via default args to avoid late-binding closure issues (B023)
            rules = [
                ("1a", lambda candidate, _inv_num=invoice_number, _d=inv_date, _amt=amount: safe_str_match(candidate.get("TransactionNumber"), _inv_num) and safe_date_match(candidate.get("TransactionDate"), _d) and safe_float_match(_amt, candidate.get("EnteredAmount"), allow_missing_expected=True)),
                ("1b", lambda candidate, _inv_num=invoice_number, _amt=amount: safe_str_match(candidate.get("TransactionNumber"), _inv_num) and safe_float_match(_amt, candidate.get("EnteredAmount"), allow_missing_expected=True)),
                ("2",  lambda candidate, _doc=document_number, _d=inv_date, _amt=amount: bool(_doc) and safe_str_match(candidate.get("DocumentNumber"), _doc) and safe_date_match(candidate.get("TransactionDate"), _d) and safe_float_match(_amt, candidate.get("EnteredAmount"), allow_missing_expected=True)),
                ("3",  lambda candidate, _inv_num=invoice_number, _d=inv_date, _amt=amount: bool(_inv_num) and str(candidate.get("TransactionNumber", "")).lower().startswith(str(_inv_num).lower()) and safe_date_match(candidate.get("TransactionDate"), _d) and safe_float_match(_amt, candidate.get("EnteredAmount"), allow_missing_expected=True)),
                ("4",  lambda candidate, _cust=customer_name, _d=inv_date, _amt=amount: bool(_cust) and safe_str_match(candidate.get("BillToCustomerName"), _cust) and safe_date_match(candidate.get("TransactionDate"), _d) and safe_float_match(_amt, candidate.get("EnteredAmount"), allow_missing_expected=True)),
            ]

            # Two-Phase Status Priority check (Open first, then Closed)
            open_candidates = [c for c in normalized_candidates if is_invoice_open(c)]
            match, rule_name = apply_rules_to_candidates(open_candidates, rules)

            if not match:
                closed_candidates = [c for c in normalized_candidates if not is_invoice_open(c)]
                match, rule_name = apply_rules_to_candidates(closed_candidates, rules)

            if match:
                raw_amt = match.get("EnteredAmount")
                fusion_amount = None
                if raw_amt is not None:
                    try:
                        fusion_amount = float(str(raw_amt).replace(",", "").strip())
                    except ValueError:
                        logger.warning(f"Could not parse fusion_invoice_amount from BIP EnteredAmount: {raw_amt!r}")
                inv.fusion_invoice_number = match.get("TransactionNumber")
                inv.fusion_invoice_date = match.get("TransactionDate")
                inv.fusion_invoice_amount = fusion_amount
            else:
                unmatched_invoices.append(inv)
        else:
            unmatched_invoices.append(inv)
    return unmatched_invoices
