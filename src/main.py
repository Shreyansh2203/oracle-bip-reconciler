from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Security, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from src.constants import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT, MAX_CONNECTIONS
from src.config import get_oracle_url
from src.models import MetaDataModel, ReconciliationRequest
from src.services.oracle_bip import run_bip_invoice_match, run_bip_receipt_match
from src.services.oracle_matcher import (
    check_invoice_cascading_native,
    check_receipt_cascading_native,
    match_invoice_in_memory,
    match_receipt_in_memory,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reconciliation_api")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    expected = os.getenv("API_KEY")
    if expected:
        if not api_key or api_key != expected:
            raise HTTPException(status_code=401, detail="Invalid or missing API Key")

def _redact(name: str | None) -> str:
    if not name:
        return "UNKNOWN"
    return f"{name[:3]}***"

# Global HTTP client
http_client: httpx.AsyncClient | None = None

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

# Helpers for Batch Mapping
async def _fetch_bip_invoices(customer_name: str, payload: ReconciliationRequest) -> list[dict[str, Any]]:
    try:
        return await run_bip_invoice_match(http_client, os.getenv("ORACLE_USER", ""), os.getenv("ORACLE_PASS", ""), "", "", None, customer_name)
    except Exception as error:
        logger.error(f"BIP Batch invoice fetch failed: {error}")
        payload.add_warning("Oracle invoice fetch failed. Downstream invoice matching will be skipped.")
        return []

async def _fetch_bip_receipts(receipt_number: str, receipt_amount: float | None, receipt_date: str, customer_name: str, payload: ReconciliationRequest) -> list[dict[str, Any]]:
    try:
        return await run_bip_receipt_match(http_client, os.getenv("ORACLE_USER", ""), os.getenv("ORACLE_PASS", ""), receipt_number, receipt_date, receipt_amount, customer_name)
    except Exception as error:
        logger.error(f"BIP Batch receipt fetch failed: {error}")
        payload.add_warning("Oracle receipt fetch failed. Downstream receipt matching will be skipped.")
        return []

def _process_batch_receipt_match(payload: ReconciliationRequest, receipt_number: str, receipt_amount: float | None, receipt_date: str, customer_name: str, bip_receipts: list[dict[str, Any]]) -> None:
    receipt_result = match_receipt_in_memory(receipt_number, receipt_amount, receipt_date, customer_name, bip_receipts)
    if receipt_result.get("matched_in_oracle"):
        payload.fusion_receipt_number = receipt_result.get("fusion_receipt_number")
        payload.fusion_receipt_date = receipt_result.get("fusion_receipt_date")
        payload.fusion_customer_name = receipt_result.get("fusion_customer_name")
        payload.match_phase = receipt_result.get("match_phase")
        payload.match_rule = receipt_result.get("match_rule")
    else:
        payload.add_warning(f"Receipt match failed: {receipt_result.get('error')}")

def _process_batch_invoices(payload: ReconciliationRequest, customer_name: str, bip_invoices: list[dict[str, Any]]) -> int:
    matched_count = 0
    if not payload.invoices:
        return matched_count

    for invoice in payload.invoices:
        invoice_number = str(invoice.invoice_number) if invoice.invoice_number else ""
        invoice_date = str(invoice.invoice_date) if invoice.invoice_date else ""
        invoice_amount = invoice.invoice_amount
        document_number = str(invoice.customer_invoice_number) if invoice.customer_invoice_number else ""

        invoice_result = match_invoice_in_memory(invoice_number, invoice_date, invoice_amount, document_number, customer_name, bip_invoices)

        if invoice_result.get("matched_in_oracle"):
            invoice.fusion_invoice_number = invoice_result.get("fusion_invoice_number")
            invoice.fusion_invoice_date = invoice_result.get("fusion_invoice_date")
            invoice.fusion_invoice_amount = invoice_result.get("fusion_invoice_amount")
            invoice.match_phase = invoice_result.get("match_phase")
            invoice.match_rule = invoice_result.get("match_rule")
            matched_count += 1
        else:
            payload.add_warning(f"Invoice {invoice_number} match failed: {invoice_result.get('error')}")
    return matched_count

@app.post("/v1/reconcile/batch", response_model=ReconciliationRequest, dependencies=[Depends(verify_api_key)])
async def reconcile_data_batch(request: Request, payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting APPROACH 1 (BATCH) for customer {_redact(payload.customer_name)}")
    start_time = time.time()

    customer_name = str(payload.customer_name) if payload.customer_name else ""
    if not customer_name:
        raise HTTPException(status_code=400, detail="customer_name is required for batch matching.")

    receipt_number = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""

    # Fetch data concurrently (optimization possible but keeping logic flow same for now, or maybe just sequential as before)
    bip_invoices = await _fetch_bip_invoices(customer_name, payload)
    bip_receipts = await _fetch_bip_receipts(receipt_number, receipt_amount, receipt_date, customer_name, payload)

    _process_batch_receipt_match(payload, receipt_number, receipt_amount, receipt_date, customer_name, bip_receipts)
    matched_count = _process_batch_invoices(payload, customer_name, bip_invoices)

    duration = int((time.time() - start_time) * 1000)
    logger.info(f"[{request_id}] Batch Match Complete: {matched_count}/{len(payload.invoices)} matched in {duration}ms")
    return payload

# Helpers for Native Mapping
@app.post("/v1/reconcile/native", response_model=ReconciliationRequest, dependencies=[Depends(verify_api_key)])
async def reconcile_data_native(request: Request, payload: ReconciliationRequest):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting APPROACH 3 (NATIVE) for customer {_redact(payload.customer_name)}")
    start_time = time.time()

    semaphore = request.app.state.oracle_sem
    oracle_username = os.getenv("ORACLE_USER", "")
    oracle_password = os.getenv("ORACLE_PASS", "")

    customer_name = str(payload.customer_name) if payload.customer_name else ""
    if not customer_name:
        raise HTTPException(status_code=400, detail="customer_name is required for native matching.")

    receipt_number = str(payload.payment_reference) if payload.payment_reference else ""
    receipt_amount = payload.total_amount
    receipt_date = str(payload.payment_date) if payload.payment_date else ""

    receipt_result = await check_receipt_cascading_native(http_client, oracle_username, oracle_password, receipt_number, receipt_amount, receipt_date, customer_name, semaphore)
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

            tasks.append(check_invoice_cascading_native(http_client, oracle_username, oracle_password, invoice_number, invoice_date, invoice_amount, document_number, customer_name, semaphore))

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
