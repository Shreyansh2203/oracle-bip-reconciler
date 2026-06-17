from __future__ import annotations

import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from src.constants import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT, MAX_CONNECTIONS
from src.config import get_oracle_url
from src.models import MetaDataModel, ReconciliationRequest
from src.services.oracle_bip import fetch_bip_invoices, fetch_bip_receipts
from src.services.oracle_matcher import (
    check_invoice_cascading_native,
    check_receipt_cascading_native,
    match_invoice_in_memory,
    match_receipt_in_memory,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")



def _redact(name: str | None) -> str:
    if not name:
        return "UNKNOWN"
    return f"{name[:3]}***"

# Global HTTP client
http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS))
        logger.info("Lazily initialized global HTTP client")
    return http_client

def get_oracle_sem(app: FastAPI) -> asyncio.Semaphore:
    if not hasattr(app.state, "oracle_sem"):
        sem_limit = int(os.getenv("MAX_CONCURRENCY", str(DEFAULT_CONCURRENCY)))
        app.state.oracle_sem = asyncio.Semaphore(sem_limit)
        logger.info(f"Lazily initialized oracle semaphore with limit {sem_limit}")
    return app.state.oracle_sem

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_http_client()
    get_oracle_sem(app)
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
    if not os.getenv("ORACLE_USER") or not os.getenv("ORACLE_PASS") or not get_oracle_url():
        raise HTTPException(status_code=503, detail="Service not ready: missing required configuration")
    return {"status": "ready"}

# Helpers for Batch Mapping
async def _process_batch_receipt_multi_stage(payload: ReconciliationRequest, receipt_number: str, receipt_amount: float | None, receipt_date: str, customer_name: str) -> None:
    client = get_http_client()
    user = os.getenv("ORACLE_USER", "")
    pwd = os.getenv("ORACLE_PASS", "")
    
    # Stage 1: Fetch by Receipt Number
    if receipt_number:
        try:
            bip_receipts = await fetch_bip_receipts(client, user, pwd, receipt_number=receipt_number)
            if bip_receipts:
                receipt_result = match_receipt_in_memory(receipt_number, receipt_amount, receipt_date, customer_name, bip_receipts)
                if receipt_result.get("matched_in_oracle"):
                    _apply_receipt_match_result(payload, receipt_result)
                    return
        except Exception as error:
            logger.error(f"BIP receipt fetch by number failed: {error}")
            
    # Stage 2: Fallback Fetch by Customer Name
    if customer_name:
        try:
            bip_receipts = await fetch_bip_receipts(client, user, pwd, customer_name=customer_name)
            if bip_receipts:
                receipt_result = match_receipt_in_memory(receipt_number, receipt_amount, receipt_date, customer_name, bip_receipts)
                if receipt_result.get("matched_in_oracle"):
                    _apply_receipt_match_result(payload, receipt_result)
                    return
                else:
                    payload.add_warning(f"Receipt match failed after customer fallback: {receipt_result.get('error')}")
                    return
        except Exception as error:
            logger.error(f"BIP receipt fetch by customer failed: {error}")
            
    payload.add_warning("Receipt match failed: No records matched after cascading fetch rules.")

def _apply_receipt_match_result(payload: ReconciliationRequest, receipt_result: dict[str, Any]) -> None:
    payload.fusion_receipt_number = receipt_result.get("fusion_receipt_number")
    payload.fusion_receipt_date = receipt_result.get("fusion_receipt_date")
    payload.fusion_customer_name = receipt_result.get("fusion_customer_name")
    payload.match_phase = receipt_result.get("match_phase")
    payload.match_rule = receipt_result.get("match_rule")

async def _process_batch_invoice_multi_stage(invoice: Any, customer_name: str, payload: ReconciliationRequest) -> bool:
    client = get_http_client()
    user = os.getenv("ORACLE_USER", "")
    pwd = os.getenv("ORACLE_PASS", "")
    
    invoice_number = str(invoice.invoice_number) if invoice.invoice_number else ""
    invoice_date = str(invoice.invoice_date) if invoice.invoice_date else ""
    invoice_amount = invoice.invoice_amount
    document_number = str(invoice.customer_invoice_number) if invoice.customer_invoice_number else ""
    
    # Stage 1: Fetch by Invoice Number
    if invoice_number:
        try:
            bip_invoices = await fetch_bip_invoices(client, user, pwd, invoice_number=invoice_number)
            if bip_invoices:
                invoice_result = match_invoice_in_memory(invoice_number, invoice_date, invoice_amount, document_number, customer_name, bip_invoices)
                if invoice_result.get("matched_in_oracle"):
                    _apply_invoice_match_result(invoice, invoice_result)
                    return True
        except Exception as error:
            logger.error(f"BIP invoice fetch by number failed for {invoice_number}: {error}")
            
    # Stage 2: Fallback Fetch by Customer Name
    if customer_name:
        try:
            bip_invoices = await fetch_bip_invoices(client, user, pwd, customer_name=customer_name)
            if bip_invoices:
                invoice_result = match_invoice_in_memory(invoice_number, invoice_date, invoice_amount, document_number, customer_name, bip_invoices)
                if invoice_result.get("matched_in_oracle"):
                    _apply_invoice_match_result(invoice, invoice_result)
                    return True
                else:
                    payload.add_warning(f"Invoice {invoice_number} match failed after customer fallback: {invoice_result.get('error')}")
                    return False
        except Exception as error:
            logger.error(f"BIP invoice fetch by customer failed for {invoice_number}: {error}")

    payload.add_warning(f"Invoice {invoice_number} match failed: No records matched after cascading fetch rules.")
    return False

def _apply_invoice_match_result(invoice: Any, invoice_result: dict[str, Any]) -> None:
    invoice.fusion_invoice_number = invoice_result.get("fusion_invoice_number")
    invoice.fusion_invoice_date = invoice_result.get("fusion_invoice_date")
    invoice.fusion_invoice_amount = invoice_result.get("fusion_invoice_amount")
    invoice.match_phase = invoice_result.get("match_phase")
    invoice.match_rule = invoice_result.get("match_rule")

@app.post("/v1/reconcile/batch", response_model=ReconciliationRequest)
async def reconcile_data_batch(request: Request, payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting APPROACH 1 (BATCH) for customer {_redact(payload.customer_name)}")
    start_time = time.time()

    customer_name = str(payload.customer_name) if payload.customer_name else ""

    receipt_number = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""

    # Process receipt matching
    await _process_batch_receipt_multi_stage(payload, receipt_number, receipt_amount, receipt_date, customer_name)
    
    # Process invoice matching
    matched_count = 0
    if payload.invoices:
        tasks = []
        for invoice in payload.invoices:
            tasks.append(_process_batch_invoice_multi_stage(invoice, customer_name, payload))
        
        results = await asyncio.gather(*tasks)
        matched_count = sum(1 for r in results if r)

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] Batch Match Complete: {matched_count}/{len(payload.invoices or [])} matched in {duration}ms")
    return payload

# Helpers for Native Mapping
@app.post("/v1/reconcile/native", response_model=ReconciliationRequest)
async def reconcile_data_native(request: Request, payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting APPROACH 3 (NATIVE) for customer {_redact(payload.customer_name)}")
    start_time = time.time()

    semaphore = get_oracle_sem(request.app)
    client = get_http_client()
    oracle_username = os.getenv("ORACLE_USER", "")
    oracle_password = os.getenv("ORACLE_PASS", "")

    customer_name = str(payload.customer_name) if payload.customer_name else ""

    receipt_number = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""

    receipt_result = await check_receipt_cascading_native(client, oracle_username, oracle_password, receipt_number, receipt_amount, receipt_date, customer_name, semaphore)
    if receipt_result.get("matched_in_oracle"):
        payload.fusion_receipt_number = receipt_result.get("fusion_receipt_number")
        payload.fusion_receipt_date = receipt_result.get("fusion_receipt_date")
        payload.fusion_customer_name = receipt_result.get("fusion_customer_name")
        payload.match_phase = receipt_result.get("match_phase")
        payload.match_rule = receipt_result.get("match_rule")
    else:
        payload.add_warning(f"Receipt match failed: {receipt_result.get('error')}")

    matched_count = 0
    if payload.invoices:
        tasks = []
        for invoice in payload.invoices:
            invoice_number = str(invoice.invoice_number) if invoice.invoice_number else ""
            invoice_date = str(invoice.invoice_date) if invoice.invoice_date else ""
            invoice_amount = invoice.invoice_amount
            document_number = str(invoice.customer_invoice_number) if invoice.customer_invoice_number else ""

            tasks.append(check_invoice_cascading_native(client, oracle_username, oracle_password, invoice_number, invoice_date, invoice_amount, document_number, customer_name, semaphore))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, invoice_result in enumerate(results):
            invoice = payload.invoices[idx]
            if isinstance(invoice_result, BaseException):
                payload.add_warning(f"Invoice {invoice.invoice_number} match failed: {str(invoice_result)}")
                logger.error(f"[{request_id}] Invoice {invoice.invoice_number} match exception: {str(invoice_result)}")
            elif invoice_result and invoice_result.get("matched_in_oracle"):
                invoice.fusion_invoice_number = invoice_result.get("fusion_invoice_number")
                invoice.fusion_invoice_date = invoice_result.get("fusion_invoice_date")
                invoice.fusion_invoice_amount = invoice_result.get("fusion_invoice_amount")
                invoice.match_phase = invoice_result.get("match_phase")
                invoice.match_rule = invoice_result.get("match_rule")
                matched_count += 1
            else:
                payload.add_warning(f"Invoice {invoice.invoice_number} match failed: {invoice_result.get('error')}")

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] Native Match Complete: {matched_count}/{len(payload.invoices)} matched in {duration}ms")
    return payload
