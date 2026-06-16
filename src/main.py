from __future__ import annotations
from fastapi import FastAPI
import traceback
app = FastAPI()

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
from fastapi.security import APIKeyHeader

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_oracle_url
from src.models import MetaDataModel, ReconciliationRequest
from src.services.oracle_bip import run_bip_invoice_match, run_bip_receipt_match
from src.services.oracle_matcher import (
    match_receipt_in_memory,
    match_invoice_in_memory,
    check_receipt_cascading_native,
    check_invoice_cascading_native,
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


# =========================================================================================
# APPROACH 1: BATCH MATCHING (Highly Optimized for Serverless)
# =========================================================================================

@app.post("/v1/reconcile/batch", response_model=ReconciliationRequest)
async def reconcile_data_batch(request: Request, payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting APPROACH 1 (BATCH) for customer {payload.customer_name}")
    start_time = time.time()
    
    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")

    customer_name = str(payload.customer_name) if payload.customer_name else ""
    if not customer_name:
        raise HTTPException(status_code=400, detail="customer_name is required for batch matching.")

    # Fetch ALL customer invoices in ONE network call by leaving invoice specific params empty
    try:
        bip_invoices = await run_bip_invoice_match(http_client, x_oracle_user, x_oracle_pass, "", "", None, customer_name)
    except Exception as e:
        logger.error(f"BIP Batch fetch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Fetch receipt in ONE network call
    receipt_num = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""
    try:
        bip_receipts = await run_bip_receipt_match(http_client, x_oracle_user, x_oracle_pass, receipt_num, receipt_date, receipt_amount, customer_name)
    except Exception as e:
        bip_receipts = []

    # Map Receipt Locally
    receipt_res = match_receipt_in_memory(receipt_num, receipt_amount, receipt_date, customer_name, bip_receipts)
    if receipt_res.get("matched_in_oracle"):
        payload.fusion_receipt_number = receipt_res.get("fusion_receipt_number")
        payload.fusion_receipt_date = receipt_res.get("fusion_receipt_date")
        payload.fusion_customer_name = receipt_res.get("fusion_customer_name")
    else:
        if payload.meta_data is None: payload.meta_data = MetaDataModel()
        payload.meta_data.warnings.append(f"Receipt match failed: {receipt_res.get('error')}")

    # Map Invoices Locally (Millisecond execution)
    matched_count = 0
    if payload.invoices:
        for inv in payload.invoices:
            inv_num = str(inv.invoice_number) if inv.invoice_number else ""
            inv_date = str(inv.invoice_date) if inv.invoice_date else ""
            inv_amount = inv.invoice_amount
            doc_num = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""

            inv_res = match_invoice_in_memory(inv_num, inv_date, inv_amount, doc_num, customer_name, bip_invoices)
            
            if inv_res.get("matched_in_oracle"):
                inv.fusion_invoice_number = inv_res.get("fusion_invoice_number")
                inv.fusion_invoice_date = inv_res.get("fusion_invoice_date")
                inv.fusion_invoice_amount = inv_res.get("fusion_invoice_amount")
                matched_count += 1
            else:
                if payload.meta_data is None: payload.meta_data = MetaDataModel()
                payload.meta_data.warnings.append(f"Invoice {inv_num} match failed: {inv_res.get('error')}")

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] Batch Match Complete: {matched_count}/{len(payload.invoices)} matched in {duration}ms")
    return payload


# =========================================================================================
# APPROACH 3: NATIVE REST API (Legacy High-Concurrency)
# =========================================================================================

@app.post("/v1/reconcile/native", response_model=ReconciliationRequest)
async def reconcile_data_native(request: Request, payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting APPROACH 3 (NATIVE) for customer {payload.customer_name}")
    start_time = time.time()
    
    sem = request.app.state.oracle_sem
    x_oracle_user = os.getenv("ORACLE_USER")
    x_oracle_pass = os.getenv("ORACLE_PASS")

    customer_name = str(payload.customer_name) if payload.customer_name else ""

    # Receipt Fetch
    receipt_num = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""
    
    receipt_res = await check_receipt_cascading_native(http_client, x_oracle_user, x_oracle_pass, receipt_num, receipt_amount, receipt_date, customer_name, sem)
    if receipt_res.get("matched_in_oracle"):
        payload.fusion_receipt_number = receipt_res.get("fusion_receipt_number")
        payload.fusion_receipt_date = receipt_res.get("fusion_receipt_date")
        payload.fusion_customer_name = receipt_res.get("fusion_customer_name")
    else:
        if payload.meta_data is None: payload.meta_data = MetaDataModel()
        payload.meta_data.warnings.append(f"Receipt match failed: {receipt_res.get('error')}")

    # Invoice Fetch Concurrently
    matched_count = 0
    if payload.invoices:
        tasks = []
        for inv in payload.invoices:
            inv_num = str(inv.invoice_number) if inv.invoice_number else ""
            inv_date = str(inv.invoice_date) if inv.invoice_date else ""
            inv_amount = inv.invoice_amount
            doc_num = str(inv.customer_invoice_number) if inv.customer_invoice_number else ""
            
            tasks.append(check_invoice_cascading_native(http_client, x_oracle_user, x_oracle_pass, inv_num, inv_date, inv_amount, doc_num, customer_name, sem))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for idx, inv_res in enumerate(results):
            inv = payload.invoices[idx]
            if isinstance(inv_res, BaseException):
                if payload.meta_data is None: payload.meta_data = MetaDataModel()
                payload.meta_data.warnings.append(f"Invoice {inv.invoice_number} match failed: {str(inv_res)}")
            elif inv_res and inv_res.get("matched_in_oracle"):
                inv.fusion_invoice_number = inv_res.get("fusion_invoice_number")
                inv.fusion_invoice_date = inv_res.get("fusion_invoice_date")
                inv.fusion_invoice_amount = inv_res.get("fusion_invoice_amount")
                matched_count += 1
            else:
                if payload.meta_data is None: payload.meta_data = MetaDataModel()
                payload.meta_data.warnings.append(f"Invoice {inv.invoice_number} match failed: {inv_res.get('error')}")

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] Native Match Complete: {matched_count}/{len(payload.invoices)} matched in {duration}ms")
    return payload

