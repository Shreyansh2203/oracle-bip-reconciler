import asyncio
import logging
import os
import time
import uuid
import json
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from src.models import ReconciliationRequest
from src.services.oracle_matcher import check_invoice_cascading, check_receipt_cascading

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")

# Global HTTP client
http_client = None

# Global Job Store for Async Polling
JOB_STORE = {}

async def cleanup_stale_jobs():
    """Background task to delete jobs older than 1 hour to prevent memory leaks."""
    while True:
        try:
            current_time = time.time()
            stale_keys = [
                k for k, v in JOB_STORE.items() 
                if current_time - v.get("created_at", current_time) > 3600
            ]
            for k in stale_keys:
                del JOB_STORE[k]
                logger.info(f"Cleaned up stale job {k} from memory.")
        except Exception as e:
            logger.error(f"Failed to cleanup old jobs: {str(e).replace(chr(10), ' ').replace(chr(13), ' ')}")
        await asyncio.sleep(600)  # Check every 10 minutes

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    asyncio.create_task(cleanup_stale_jobs())
    # Increase keepalive connections to match max_connections to avoid TLS handshake overhead
    http_client = httpx.AsyncClient(timeout=15.0, limits=httpx.Limits(max_connections=200, max_keepalive_connections=200))
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

# Setup CORS - Fix 7
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "online", "message": "Oracle Reconciliation API is running"}

async def _process_reconciliation(payload: ReconciliationRequest) -> ReconciliationRequest:
    """
    Core logic for executing the reconciliation matching.
    """
    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")

    # Fix 11: Remove hardcoded dummy credentials check
    if not x_oracle_user or not x_oracle_pass:
        logger.error("Oracle credentials are not configured in the environment.")
        raise Exception("Oracle credentials are not configured.")

    if not http_client:
        logger.error("Global HTTP client is not initialized")
        raise Exception("Internal server error: HTTP client not initialized")

    start_time = time.time()

    # Clean input strings
    receipt_num = str(payload.payment_reference).strip() if payload.payment_reference is not None else ""
    if receipt_num == "None":
        receipt_num = ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date).strip() if payload.payment_date is not None else ""
    if receipt_date == "None":
        receipt_date = ""
    customer_name = str(payload.customer_name).strip() if payload.customer_name is not None else ""
    if customer_name == "None":
        customer_name = ""

    # 1. Check Receipt (Cascading)
    receipt_result = await check_receipt_cascading(
        http_client, x_oracle_user, x_oracle_pass, receipt_num, receipt_amount, receipt_date, customer_name
    )

    if receipt_result.get("matched_in_oracle"):
        payload.fusion_receipt_number = receipt_result.get("fusion_receipt_number")
        payload.fusion_receipt_date = receipt_result.get("fusion_receipt_date")
        payload.fusion_customer_name = receipt_result.get("fusion_customer_name")
    else:
        # Fix 10: Sanitize log string
        clean_error = str(receipt_result.get('error', '')).replace("\n", " ").replace("\r", " ")
        logger.warning(f"Receipt match error or not found: {clean_error}")
        if hasattr(payload, "meta_data"):
            if payload.meta_data is None:
                payload.meta_data = {}
            if isinstance(payload.meta_data, dict):
                if "warnings" not in payload.meta_data:
                    payload.meta_data["warnings"] = []
                payload.meta_data["warnings"].append(f"Receipt match failed: {clean_error}")

    # 2. Check Invoices concurrently
    # Fix 9: Reduce concurrency to 50 to prevent Oracle connection exhaustion
    sem = asyncio.Semaphore(50)

    async def sem_check_invoice(*args, **kwargs):
        async with sem:
            return await check_invoice_cascading(*args, **kwargs)

    tasks = []
    for inv in payload.invoices:
        inv_num = str(inv.invoice_number).strip() if inv.invoice_number is not None else ""
        if inv_num == "None":
            inv_num = ""
        inv_date = str(inv.invoice_date).strip() if inv.invoice_date is not None else ""
        if inv_date == "None":
            inv_date = ""
        inv_amount = inv.invoice_amount
        doc_num = str(inv.customer_invoice_number).strip() if inv.customer_invoice_number is not None else ""
        if doc_num == "None":
            doc_num = ""

        tasks.append(sem_check_invoice(
            http_client, x_oracle_user, x_oracle_pass, inv_num, inv_date, inv_amount, doc_num, customer_name
        ))

    invoice_results = await asyncio.gather(*tasks)

    # 3. Map invoice results back to the payload
    for idx, inv in enumerate(payload.invoices):
        inv_res = invoice_results[idx]
        if inv_res and inv_res.get("matched_in_oracle"):
            inv.fusion_invoice_number = inv_res.get("fusion_invoice_number")
            inv.fusion_invoice_date = inv_res.get("fusion_invoice_date")
            inv.fusion_invoice_amount = inv_res.get("fusion_invoice_amount")
        elif inv_res and inv_res.get("error"):
            if hasattr(payload, "meta_data"):
                if payload.meta_data is None:
                    payload.meta_data = {}
                if isinstance(payload.meta_data, dict):
                    if "warnings" not in payload.meta_data:
                        payload.meta_data["warnings"] = []
                    payload.meta_data["warnings"].append(f"Invoice {inv.invoice_number} match failed: {inv_res.get('error')}")

    execution_time = round(time.time() - start_time, 2)
    logger.info(f"Reconciliation completed in {execution_time}s. Invoices checked: {len(invoice_results)}")

    return payload

async def background_job_runner(job_id: str, payload: ReconciliationRequest):
    """
    Executes the reconciliation and updates the in-memory dictionary when done or failed.
    """
    logger.info(f"Background Job {job_id} Started.")
    try:
        JOB_STORE[job_id]["status"] = "PROCESSING"
        result_payload = await _process_reconciliation(payload)
        JOB_STORE[job_id]["status"] = "COMPLETED"
        JOB_STORE[job_id]["result"] = result_payload.model_dump()
        logger.info(f"Background Job {job_id} Completed successfully.")
    except Exception as e:
        clean_error = str(e).replace("\n", " ").replace("\r", " ")
        logger.exception(f"Background Job {job_id} Failed: {clean_error}")
        JOB_STORE[job_id]["status"] = "FAILED"
        JOB_STORE[job_id]["error"] = clean_error

@app.post("/reconcile")
async def reconcile_data_async(payload: ReconciliationRequest, background_tasks: BackgroundTasks):
    """
    Endpoint for asynchronous Oracle matching.
    Returns a job_id instantly. The processing happens in the background.
    """
    job_id = str(uuid.uuid4())
    
    JOB_STORE[job_id] = {
        "status": "QUEUED",
        "result": None,
        "error": None,
        "created_at": time.time()
    }
    
    background_tasks.add_task(background_job_runner, job_id, payload)
    
    return {
        "job_id": job_id,
        "status": "QUEUED",
        "message": "Reconciliation job is queued and processing in the background."
    }

@app.get("/reconcile/{job_id}")
async def get_reconciliation_status(job_id: str):
    """
    Poll this endpoint with the job_id to get the status or the completed payload.
    """
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job ID not found or has expired")
        
    job_data = JOB_STORE[job_id]
    
    if job_data["status"] == "COMPLETED":
        return {
            "status": "COMPLETED",
            "result": job_data["result"]
        }
    elif job_data["status"] == "FAILED":
        return {
            "status": "FAILED",
            "error": job_data["error"]
        }
    else:
        return {
            "status": job_data["status"],
            "message": "Job is still processing. Please check back later."
        }


