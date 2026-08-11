from __future__ import annotations

import logging
import os
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.config import get_oracle_url
from src.constants import DEFAULT_TIMEOUT, MAX_CONNECTIONS
from src.models import ReconciliationRequest
from src.services.reconciliation import process_reconciliation_batch

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("reconciliation_api")


def _redact(name: str | None) -> str:
    if not name:
        return "UNKNOWN"
    return f"{name[:3]}***"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS),
    )
    app.state.http_client = client
    logger.info("Initialized HTTP client in lifespan")
    yield
    logger.info("Shutting down HTTP client")
    await client.aclose()


app = FastAPI(
    title="Oracle Reconciliation Live API",
    version="4.0.0",
    description="Professional enterprise API for Oracle ERP Cloud reconciliation matching.",
    lifespan=lifespan,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, cast(Any, _rate_limit_exceeded_handler))

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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"Unhandled internal error: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected internal server error occurred."},
    )


@app.get("/")
@limiter.limit("60/minute")
async def root(request: Request) -> dict[str, str]:
    return {"status": "online", "message": "Oracle Reconciliation API is running"}


@app.get("/health")
@limiter.limit("60/minute")
async def health_check(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
@limiter.limit("60/minute")
async def readiness_check(request: Request) -> dict[str, str]:
    try:
        oracle_url = get_oracle_url()
    except ValueError:
        oracle_url = None
    if not os.getenv("ORACLE_USER") or not os.getenv("ORACLE_PASS") or not oracle_url:
        raise HTTPException(status_code=503, detail="Service not ready: missing required configuration")
    return {"status": "ready"}


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key(api_key: str | None = Security(api_key_header)) -> str | None:
    expected_api_key = os.getenv("API_KEY")
    if not expected_api_key:
        logger.warning("API_KEY environment variable is not set. API is unsecured.")
        return api_key
    if not api_key or api_key != expected_api_key:
        raise HTTPException(status_code=403, detail="Could not validate API Key")
    return api_key

def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


@app.post("/v1/reconcile/batch", response_model=ReconciliationRequest | None)
@limiter.limit("10/minute")
async def reconcile_data_batch(
    request: Request,
    payload: ReconciliationRequest,
    client: httpx.AsyncClient = Depends(get_client),  # noqa: B008
    api_key: str | None = Depends(get_api_key)  # noqa: B008
) -> ReconciliationRequest | None:
    res, err, status = await process_reconciliation_batch(payload, client)
    if err:
        raise HTTPException(status_code=status or 500, detail=err)
    return res
