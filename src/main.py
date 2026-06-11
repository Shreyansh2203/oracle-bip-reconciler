import asyncio
import logging
import os
import time
import uuid
import json
import sqlite3
import aiosqlite
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

DB_PATH = "jobs.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def cleanup_old_jobs():
    """Background task to delete jobs older than 24 hours to prevent disk leak."""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM jobs WHERE created_at < datetime('now', '-1 day')")
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to cleanup old jobs: {str(e).replace(chr(10), ' ').replace(chr(13), ' ')}")
        await asyncio.sleep(3600)  # Run once an hour

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    await init_db()
    asyncio.create_task(cleanup_old_jobs())
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

async def set_job_status(job_id: str, status: str, result: dict = None, error: str = None):
    """Helper to update job status in SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        result_str = json.dumps(result) if result else None
        await db.execute('''
            UPDATE jobs SET status = ?, result = ?, error = ? WHERE job_id = ?
        ''', (status, result_str, error, job_id))
        await db.commit()

async def background_job_runner(job_id: str, payload: ReconciliationRequest):
    """
    Executes the reconciliation and updates SQLite when done or failed.
    """
    logger.info(f"Background Job {job_id} Started.")
    try:
        await set_job_status(job_id, "PROCESSING")
        result_payload = await _process_reconciliation(payload)
        await set_job_status(job_id, "COMPLETED", result=result_payload.model_dump())
        logger.info(f"Background Job {job_id} Completed successfully.")
    except Exception as e:
        # Fix 8: Use logger.exception to log the traceback, and fix 10: Sanitize string
        clean_error = str(e).replace("\n", " ").replace("\r", " ")
        logger.exception(f"Background Job {job_id} Failed: {clean_error}")
        await set_job_status(job_id, "FAILED", error=clean_error)

@app.post("/reconcile")
async def reconcile_data_async(payload: ReconciliationRequest, background_tasks: BackgroundTasks):
    """
    Endpoint for asynchronous Oracle matching.
    Returns a job_id instantly. The processing happens in the background.
    """
    job_id = str(uuid.uuid4())
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO jobs (job_id, status) VALUES (?, ?)
        ''', (job_id, "QUEUED"))
        await db.commit()
    
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT status, result, error FROM jobs WHERE job_id = ?', (job_id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row:
        raise HTTPException(status_code=404, detail="Job ID not found")
        
    status, result_str, error = row
    
    if status == "COMPLETED":
        return {
            "status": "COMPLETED",
            "result": json.loads(result_str) if result_str else None
        }
    elif status == "FAILED":
        return {
            "status": "FAILED",
            "error": error
        }
    else:
        return {
            "status": status,
            "message": "Job is still processing. Please check back later."
        }


