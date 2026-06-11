import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.models import ReconciliationRequest
from src.services.oracle_matcher import check_invoice_cascading, check_receipt_cascading

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")

# Global HTTP client
http_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
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

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "online", "message": "Oracle Reconciliation API is running"}

@app.post("/reconcile", response_model=ReconciliationRequest)
async def reconcile_data(payload: ReconciliationRequest):
    """
    Endpoint for real-time Oracle matching using Cascading Rules.
    Expects a JSON payload and returns the same payload enriched with 'fusion_' mapped fields.
    """
    logger.info(f"Received reconcile request for payment_reference: {payload.payment_reference}")

    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")

    if not x_oracle_user or not x_oracle_pass or x_oracle_user == "YOUR_USERNAME_HERE":
        logger.error("Oracle credentials are not configured in the .env file.")
        raise HTTPException(status_code=500, detail="Oracle credentials are not configured in the .env file.")

    if not http_client:
        logger.error("Global HTTP client is not initialized")
        raise HTTPException(status_code=500, detail="Internal server error: HTTP client not initialized")

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
        logger.warning(f"Receipt match error or not found: {receipt_result.get('error')}")
        if hasattr(payload, "meta_data"):
            if payload.meta_data is None:
                payload.meta_data = {}
            if isinstance(payload.meta_data, dict):
                if "warnings" not in payload.meta_data:
                    payload.meta_data["warnings"] = []
                payload.meta_data["warnings"].append(f"Receipt match failed: {receipt_result.get('error')}")

    # 2. Check Invoices concurrently (Each invoice has cascading rules)
    sem = asyncio.Semaphore(150)

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

    # 4. Return the enriched payload
    return payload
